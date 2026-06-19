"""
Batch re-tag library albums against MusicBrainz via beets.

Walks leaf album folders under library/, strips bogus playlist track numbers,
and runs `beet import -C -I` on each album so beets can match the correct
release and rewrite track listings, filenames, and embedded tags.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from typing import Callable

from groove.config import Settings
from groove.downloader import strip_track_numbers
from groove.importer import _strip_ansi

log = logging.getLogger(__name__)

_AUDIO_SUFFIXES = frozenset({"mp3", "m4a", "flac", "ogg", "opus", "wav", "aac"})
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Fallback stdin when beets still prompts (duplicate merge, candidate pick, apply).
_BEET_STDIN_RESPONSES = "\n".join(["M", "1", "A", ""] * 40) + "\n"

_MATCH_RE = re.compile(r"Match\s*\((\d+(?:\.\d+)?)%\)", re.IGNORECASE)
_TAGGING_RE = re.compile(r"^Tagging:\s+", re.IGNORECASE)
_NO_MATCH_RE = re.compile(r"No matching release found", re.IGNORECASE)
_SKIPPED_RE = re.compile(r"Skipped\s+\d+\s+paths?", re.IGNORECASE)


class RetagStatus(str, Enum):
    TAGGED = "tagged"
    SKIPPED = "skipped"
    NO_MATCH = "no_match"
    ERROR = "error"


@dataclass
class AlbumFolder:
    path: Path
    artist: str
    album_label: str
    track_count: int
    singleton: bool = False


@dataclass
class RetagAlbumResult:
    album: AlbumFolder
    status: RetagStatus
    similarity: float | None = None
    tracks_stripped: int = 0
    message: str = ""
    output_tail: str = ""


@dataclass
class RetagBatchReport:
    results: list[RetagAlbumResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def tagged(self) -> int:
        return sum(1 for r in self.results if r.status == RetagStatus.TAGGED)

    @property
    def no_match(self) -> int:
        return sum(1 for r in self.results if r.status == RetagStatus.NO_MATCH)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == RetagStatus.ERROR)


@dataclass
class RetagState:
    completed: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> RetagState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(completed=data.get("completed", {}))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load retag state %s: %s", path, exc)
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"completed": self.completed}, indent=2),
            encoding="utf-8",
        )

    def is_done(self, album_path: Path) -> bool:
        return str(album_path.resolve()) in self.completed


def audio_files_in_dir(directory: Path, *, recursive: bool = False) -> list[Path]:
    if recursive:
        return sorted(
            p for p in directory.rglob("*")
            if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_SUFFIXES
        )
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_SUFFIXES
    )


def discover_album_directories(library_dir: Path) -> list[AlbumFolder]:
    """Return leaf directories that contain audio files (one album per folder)."""
    if not library_dir.is_dir():
        return []

    albums: list[AlbumFolder] = []
    for path in sorted(library_dir.rglob("*")):
        if not path.is_dir():
            continue
        direct_audio = audio_files_in_dir(path)
        if not direct_audio:
            continue
        child_has_audio = any(
            audio_files_in_dir(child)
            for child in path.iterdir()
            if child.is_dir()
        )
        if child_has_audio:
            continue

        rel = path.relative_to(library_dir)
        parts = rel.parts
        artist = parts[0] if parts else path.name
        album_label = path.name
        singleton = len(direct_audio) == 1 and len(parts) >= 2 and parts[0] == "Non-Album"
        albums.append(
            AlbumFolder(
                path=path,
                artist=artist,
                album_label=album_label,
                track_count=len(direct_audio),
                singleton=singleton,
            )
        )
    return albums


def _track_number_from_file(path: Path) -> int | None:
    try:
        import mutagen
    except ImportError:
        return None

    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError

            try:
                tags = EasyID3(path)
            except ID3NoHeaderError:
                return None
            raw = tags.get("tracknumber", [None])[0]
        elif ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4

            audio = MP4(path)
            if not audio.tags or "trkn" not in audio.tags:
                return None
            raw = str(audio.tags["trkn"][0][0])
        elif ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(path)
            raw = (audio.get("tracknumber") or [None])[0]
        else:
            audio = mutagen.File(path)
            if audio is None:
                return None
            raw = (audio.get("tracknumber") or [None])[0]
            if isinstance(raw, list):
                raw = raw[0] if raw else None
    except Exception:
        return None

    if not raw:
        return None
    try:
        return int(str(raw).split("/")[0].strip())
    except ValueError:
        return None


def _mb_trackid_from_file(path: Path) -> str:
    try:
        import mutagen
    except ImportError:
        return ""

    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError

            try:
                tags = EasyID3(path)
            except ID3NoHeaderError:
                return ""
            val = tags.get("musicbrainz_trackid", [""])[0]
            return val or ""
        audio = mutagen.File(path)
        if audio is None:
            return ""
        for key in ("musicbrainz_trackid", "MUSICBRAINZ_TRACKID"):
            if key in audio:
                val = audio[key]
                if isinstance(val, list):
                    return val[0] if val else ""
                return str(val)
    except Exception:
        return ""
    return ""


def has_suspicious_track_numbers(files: list[Path]) -> bool:
    """True when embedded track numbers would confuse beets album matching."""
    if not files:
        return False

    numbers = [n for f in files if (n := _track_number_from_file(f)) is not None]
    if not numbers:
        return False

    track_count = len(files)
    if len(numbers) < track_count:
        return True
    if len(set(numbers)) == 1 and track_count > 1:
        return True
    if any(n > track_count or n > 30 for n in numbers):
        return True
    return False


def album_needs_retag(album: AlbumFolder) -> bool:
    files = audio_files_in_dir(album.path)
    if not files:
        return False
    if has_suspicious_track_numbers(files):
        return True
    return any(not _mb_trackid_from_file(f) for f in files)


def prepare_album_directory(album: AlbumFolder) -> int:
    """Strip bogus track numbers before beets re-import. Returns files changed."""
    files = audio_files_in_dir(album.path)
    if not has_suspicious_track_numbers(files):
        return 0
    return strip_track_numbers(files)


def _beet_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    if settings.api_keys.acoustid:
        env["ACOUSTID_APIKEY"] = settings.api_keys.acoustid
    if settings.api_keys.lastfm_api_key:
        env["LASTFM_APIKEY"] = settings.api_keys.lastfm_api_key
    return env


def _find_beet() -> str:
    venv_beet = Path(sys.executable).parent / "beet"
    return str(venv_beet) if venv_beet.exists() else "beet"


def _classify_output(output: str, returncode: int) -> tuple[RetagStatus, float | None, str]:
    scan = _strip_ansi(output)
    similarity = None
    if m := _MATCH_RE.search(scan):
        similarity = float(m.group(1))

    if _TAGGING_RE.search(scan) or (similarity is not None and returncode == 0):
        return RetagStatus.TAGGED, similarity, "MusicBrainz match applied"

    if _NO_MATCH_RE.search(scan):
        return RetagStatus.NO_MATCH, similarity, "No MusicBrainz release matched all tracks"

    if _SKIPPED_RE.search(scan) or re.search(r"\bSkipping\.?\b", scan, re.IGNORECASE):
        return RetagStatus.SKIPPED, similarity, "Beets skipped this album (no confident match)"

    if returncode != 0:
        tail = scan.strip()[-300:] or "beet import failed"
        return RetagStatus.ERROR, similarity, tail

    if similarity is not None:
        return RetagStatus.TAGGED, similarity, f"Match applied ({similarity:.1f}%)"

    return RetagStatus.SKIPPED, similarity, "No changes detected"


def retag_album_directory(
    album: AlbumFolder,
    settings: Settings,
    *,
    timeout: int = 600,
) -> RetagAlbumResult:
    """Re-import one album folder in place via beets."""
    tracks_stripped = prepare_album_directory(album)

    cmd = [
        _find_beet(),
        "--config",
        str(settings.beets_config),
        "import",
        "-C",
        "-I",
    ]
    if album.singleton:
        cmd.append("-s")
    cmd.append(str(album.path))

    try:
        proc = subprocess.run(
            cmd,
            input=_BEET_STDIN_RESPONSES,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_beet_env(settings),
        )
    except subprocess.TimeoutExpired:
        return RetagAlbumResult(
            album=album,
            status=RetagStatus.ERROR,
            tracks_stripped=tracks_stripped,
            message=f"Timed out after {timeout}s",
        )
    except FileNotFoundError:
        return RetagAlbumResult(
            album=album,
            status=RetagStatus.ERROR,
            tracks_stripped=tracks_stripped,
            message="beet executable not found",
        )

    output = proc.stdout + proc.stderr
    status, similarity, message = _classify_output(output, proc.returncode)
    return RetagAlbumResult(
        album=album,
        status=status,
        similarity=similarity,
        tracks_stripped=tracks_stripped,
        message=message,
        output_tail=_strip_ansi(output)[-1500:],
    )


def run_retag_batch(
    settings: Settings,
    *,
    only_missing: bool = True,
    artist_filter: str | None = None,
    limit: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
    on_progress: Callable[[int, int, RetagAlbumResult], None] | None = None,
) -> RetagBatchReport:
    """
    Walk the library and re-tag each album folder.

    Parameters
    ----------
    only_missing:
        Skip albums whose tracks already have MusicBrainz IDs and sane track numbers.
    artist_filter:
        Only process albums under this top-level artist folder name.
    limit:
        Stop after this many albums (useful for testing).
    resume:
        Skip album paths recorded in state/retag_albums.json.
    dry_run:
        List albums that would be processed without calling beets.
    on_progress:
        Optional callback(index, total, RetagAlbumResult) after each album.
    """
    state_path = settings.state_dir / "retag_albums.json"
    state = RetagState.load(state_path) if resume else RetagState()

    report = RetagBatchReport(started_at=datetime.now(UTC).isoformat())
    albums = discover_album_directories(settings.library_dir)

    if artist_filter:
        needle = artist_filter.casefold()
        albums = [a for a in albums if a.artist.casefold() == needle]

    if only_missing:
        albums = [a for a in albums if album_needs_retag(a)]

    if resume:
        albums = [a for a in albums if not state.is_done(a.path)]

    if limit is not None:
        albums = albums[:limit]

    total = len(albums)
    for index, album in enumerate(albums, start=1):
        if dry_run:
            result = RetagAlbumResult(
                album=album,
                status=RetagStatus.SKIPPED,
                message="dry run",
            )
        else:
            log.info(
                "Retagging album %d/%d: %s / %s (%d tracks)",
                index, total, album.artist, album.album_label, album.track_count,
            )
            result = retag_album_directory(album, settings)
            state.completed[str(album.path.resolve())] = result.status.value
            state.save(state_path)

        report.results.append(result)
        if on_progress:
            on_progress(index, total, result)

    report.finished_at = datetime.now(UTC).isoformat()
    return report


def write_retag_log(settings: Settings, report: RetagBatchReport) -> Path:
    """Append a human-readable summary to logs/retag-albums.log."""
    log_path = settings.logs_dir / "retag-albums.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"\n=== retag batch {report.started_at} ===",
        f"tagged={report.tagged} no_match={report.no_match} errors={report.errors} "
        f"total={len(report.results)}",
    ]
    for result in report.results:
        album = result.album
        sim = f" {result.similarity:.1f}%" if result.similarity is not None else ""
        lines.append(
            f"{result.status.value:8}  {album.artist} / {album.album_label}{sim}"
            f"  ({result.message})"
        )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return log_path
