"""
Bulk input parser.

Auto-detects format and returns a list of ParsedEntry objects.
Supported formats:
  - Plain text (one entry per line: "Artist - Album" or a URL)
  - Generic CSV with flexible column headers
  - Spotify/Exportify CSV (distinctive header set)
  - YouTube playlist URL (expanded via yt-dlp --flat-playlist)

Used by both the /bulk web page and the CLI `groove request --file`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

InputFormat = Literal["plain_text", "csv", "spotify_csv", "youtube_playlist", "unknown"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ParsedEntry:
    raw_query: str
    kind: str = "track"  # "track" | "album" | "playlist"
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    year: str | None = None
    track_number: int | None = None
    source_url: str | None = None
    source_format: str = "plain_text"


@dataclass
class ParseResult:
    entries: list[ParsedEntry]
    format_detected: InputFormat
    errors: list[str] = field(default_factory=list)
    raw_line_count: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_input(
    text: str | None = None,
    *,
    filename: str | None = None,
    bytes_content: bytes | None = None,
) -> ParseResult:
    """
    Parse bulk input and return a ParseResult.

    Pass `text` for string input, or `bytes_content` + `filename` for uploaded files.
    """
    if bytes_content is not None:
        text = bytes_content.decode("utf-8-sig", errors="replace")

    if not text or not text.strip():
        return ParseResult(entries=[], format_detected="unknown", raw_line_count=0)

    # Detect format
    fmt = _detect_format(text, filename=filename)
    log.info("Detected bulk input format: %s", fmt)

    if fmt == "spotify_csv":
        return _parse_spotify_csv(text)
    elif fmt == "csv":
        return _parse_generic_csv(text)
    elif fmt == "youtube_playlist":
        return _parse_youtube_playlist(text.strip())
    else:
        return _parse_plain_text(text)


def dedup_entries(
    entries: list[ParsedEntry],
    *,
    existing_queries: set[str] | None = None,
    pending_queries: set[str] | None = None,
) -> tuple[list[ParsedEntry], list[ParsedEntry], list[ParsedEntry]]:
    """
    Split entries into (to_queue, already_in_library, already_pending).

    Callers supply sets of normalised query strings from the beets library
    and the pending request queue to check against.
    """
    existing = existing_queries or set()
    pending = pending_queries or set()

    to_queue: list[ParsedEntry] = []
    in_library: list[ParsedEntry] = []
    already_pending: list[ParsedEntry] = []

    seen: set[str] = set()
    for entry in entries:
        key = _normalise_key(entry)
        if key in seen:
            continue
        seen.add(key)
        if key in existing:
            in_library.append(entry)
        elif key in pending:
            already_pending.append(entry)
        else:
            to_queue.append(entry)

    return to_queue, in_library, already_pending


def normalised_library_keys(beet_items: list[dict]) -> set[str]:
    """Build a lookup set from beet library items."""
    keys: set[str] = set()
    for item in beet_items:
        artist = item.get("artist") or item.get("albumartist") or ""
        title = item.get("title") or ""
        album = item.get("album") or ""
        if artist and title:
            keys.add(_norm(f"{artist} {title}"))
        if artist and album:
            keys.add(_norm(f"{artist} {album}"))
    return keys


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

# Exportify / Spotify CSV has these distinctive headers
_SPOTIFY_HEADERS = {"track name", "artist name(s)", "album name", "added at"}
_SPOTIFY_ALT_HEADERS = {"track name", "artist name", "album name"}


def _detect_format(text: str, filename: str | None = None) -> InputFormat:
    stripped = text.strip()

    # YouTube playlist URL on a single line
    if _is_youtube_playlist_url(stripped.splitlines()[0].strip()):
        return "youtube_playlist"

    # CSV detection: check first non-comment line for comma-separated headers
    lines = [l for l in stripped.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return "plain_text"

    first = lines[0]
    if "," in first and not _is_url(first.split(",")[0]):
        headers = {h.strip().strip('"').lower() for h in first.split(",")}
        if _SPOTIFY_HEADERS.issubset(headers) or _SPOTIFY_ALT_HEADERS.issubset(headers):
            return "spotify_csv"
        # Generic CSV if at least one recognisable column
        _KNOWN_CSV_COLS = {"artist", "album", "title", "track name", "track", "year"}
        if headers & _KNOWN_CSV_COLS:
            return "csv"

    # Extension hints
    if filename and filename.lower().endswith(".csv"):
        return "csv"

    return "plain_text"


def _is_youtube_playlist_url(s: str) -> bool:
    return bool(re.match(r"https?://(www\.|music\.)?youtube\.com/playlist\?", s))


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_plain_text(text: str) -> ParseResult:
    entries: list[ParsedEntry] = []
    errors: list[str] = []
    lines = text.splitlines()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _is_url(line):
            entries.append(ParsedEntry(raw_query=line, source_url=line, kind="track", source_format="plain_text"))
        else:
            entry = _parse_text_line(line)
            entries.append(entry)

    return ParseResult(entries=entries, format_detected="plain_text", raw_line_count=len(lines))


def _parse_text_line(line: str) -> ParsedEntry:
    """Parse 'Artist - Title' or 'Artist - Album' into a ParsedEntry."""
    # Split on first " - " (with spaces)
    m = re.split(r"\s+-\s+", line, maxsplit=1)
    if len(m) == 2:
        artist, rest = m[0].strip(), m[1].strip()
        # Heuristic: if rest has "album" keyword or parens with year, treat as album
        if re.search(r"\(\d{4}\)$|\[album\]", rest, re.I):
            return ParsedEntry(raw_query=line, kind="album", artist=artist, album=rest, source_format="plain_text")
        return ParsedEntry(raw_query=line, kind="track", artist=artist, title=rest, source_format="plain_text")
    return ParsedEntry(raw_query=line, kind="track", source_format="plain_text")


def _parse_spotify_csv(text: str) -> ParseResult:
    entries: list[ParsedEntry] = []
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    # Normalise headers
    for row in rows:
        normalised = {k.strip().lower(): v.strip() for k, v in row.items()}
        try:
            artist = (
                normalised.get("artist name(s)")
                or normalised.get("artist name")
                or normalised.get("artist")
                or ""
            )
            # Exportify may list multiple artists comma-separated; take first
            artist = artist.split(",")[0].strip()

            title = (
                normalised.get("track name")
                or normalised.get("title")
                or normalised.get("track")
                or ""
            )
            album = normalised.get("album name") or normalised.get("album") or ""
            year = normalised.get("year") or normalised.get("added at", "")[:4] or ""

            if not artist and not title:
                continue

            raw = f"{artist} - {title}" if artist and title else (artist or title)
            entries.append(ParsedEntry(
                raw_query=raw,
                kind="track",
                artist=artist or None,
                title=title or None,
                album=album or None,
                year=year or None,
                source_format="spotify_csv",
            ))
        except Exception as exc:
            errors.append(f"Row parse error: {exc}")

    return ParseResult(
        entries=entries,
        format_detected="spotify_csv",
        raw_line_count=len(rows),
        errors=errors,
    )


def _parse_generic_csv(text: str) -> ParseResult:
    entries: list[ParsedEntry] = []
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    for row in rows:
        normalised = {k.strip().lower(): v.strip() for k, v in row.items()}
        try:
            artist = normalised.get("artist") or normalised.get("artist name") or ""
            title = normalised.get("title") or normalised.get("track name") or normalised.get("track") or ""
            album = normalised.get("album") or normalised.get("album name") or ""
            year = normalised.get("year") or ""
            url = normalised.get("url") or normalised.get("source_url") or ""

            if not (artist or title or album or url):
                continue

            raw = f"{artist} - {title}" if artist and title else (
                f"{artist} - {album}" if artist and album else (artist or title or album or url)
            )
            kind = "album" if album and not title else "track"
            entries.append(ParsedEntry(
                raw_query=raw,
                kind=kind,
                artist=artist or None,
                title=title or None,
                album=album or None,
                year=year or None,
                source_url=url or None,
                source_format="csv",
            ))
        except Exception as exc:
            errors.append(f"Row parse error: {exc}")

    return ParseResult(entries=entries, format_detected="csv", raw_line_count=len(rows), errors=errors)


def _parse_youtube_playlist(url: str) -> ParseResult:
    """Parse a YouTube/YouTube Music playlist.

    Tries ytmusicapi first (rich metadata: artist, album, track number),
    falling back to yt-dlp --flat-playlist when ytmusicapi is unavailable
    or the playlist is a regular YouTube playlist without music metadata.
    """
    if _is_ytmusic_url(url):
        result = _parse_ytmusic_playlist(url)
        if result.entries:
            return result
        log.info("ytmusicapi returned no entries — falling back to yt-dlp")

    return _parse_youtube_playlist_ytdlp(url)


def _is_ytmusic_url(url: str) -> bool:
    return "music.youtube.com" in url


def _extract_playlist_id(url: str) -> str | None:
    """Extract the 'list' query parameter from a YouTube playlist URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    ids = params.get("list", [])
    return ids[0] if ids else None


def _parse_ytmusic_playlist(url: str) -> ParseResult:
    """Parse a YouTube Music playlist via ytmusicapi for rich metadata."""
    try:
        from ytmusicapi import YTMusic
        ytmusic = YTMusic()
    except ImportError:
        log.warning("ytmusicapi not installed — cannot parse YouTube Music playlist")
        return ParseResult(entries=[], format_detected="youtube_playlist",
                           errors=["ytmusicapi not installed"])
    except Exception as exc:
        log.warning("YTMusic init failed: %s", exc)
        return ParseResult(entries=[], format_detected="youtube_playlist",
                           errors=[f"YTMusic init: {exc}"])

    playlist_id = _extract_playlist_id(url)
    if not playlist_id:
        log.warning("Could not extract playlist ID from %s", url)
        return ParseResult(entries=[], format_detected="youtube_playlist",
                           errors=["Could not extract playlist ID from URL"])

    try:
        playlist_data = ytmusic.get_playlist(playlist_id, limit=None)
    except Exception as exc:
        log.warning("ytmusicapi get_playlist failed: %s", exc)
        return ParseResult(entries=[], format_detected="youtube_playlist",
                           errors=[f"ytmusicapi: {exc}"])

    tracks = playlist_data.get("tracks") or []
    if not tracks:
        return ParseResult(entries=[], format_detected="youtube_playlist")

    # Fetch track numbers by looking up each unique album once
    album_track_numbers = _fetch_album_track_numbers(ytmusic, tracks)

    entries: list[ParsedEntry] = []
    for track in tracks:
        video_id = track.get("videoId")
        if not video_id:
            continue

        title = track.get("title") or ""
        artists = track.get("artists") or []
        artist = ", ".join(a["name"] for a in artists if a.get("name")) if artists else ""
        album_info = track.get("album")
        album_name = album_info.get("name", "") if album_info else ""

        track_number = album_track_numbers.get(video_id)

        source_url = f"https://music.youtube.com/watch?v={video_id}"
        raw = f"{artist} - {title}" if artist else title

        entries.append(ParsedEntry(
            raw_query=raw,
            kind="track",
            artist=artist or None,
            title=title or None,
            album=album_name or None,
            track_number=track_number,
            source_url=source_url,
            source_format="youtube_playlist",
        ))

    log.info("Parsed %d tracks from YouTube Music playlist via ytmusicapi", len(entries))
    return ParseResult(
        entries=entries,
        format_detected="youtube_playlist",
        raw_line_count=len(tracks),
    )


def _fetch_album_track_numbers(
    ytmusic: object,
    tracks: list[dict],
    max_albums: int = 50,
) -> dict[str, int]:
    """Look up track numbers by fetching unique album pages via ytmusicapi.

    Returns a mapping of videoId -> 1-based track number.
    """
    unique_browse_ids: dict[str, None] = {}
    for track in tracks:
        album_info = track.get("album")
        if album_info and album_info.get("id"):
            unique_browse_ids.setdefault(album_info["id"], None)

    result: dict[str, int] = {}
    fetched = 0
    for browse_id in unique_browse_ids:
        if fetched >= max_albums:
            log.info(
                "Reached album lookup cap (%d) — skipping track numbers for rest", max_albums,
            )
            break
        try:
            album_data = ytmusic.get_album(browse_id)  # type: ignore[union-attr]
            for idx, album_track in enumerate(album_data.get("tracks") or []):
                vid = album_track.get("videoId")
                raw_num = album_track.get("trackNumber")
                if vid and raw_num is not None:
                    result[vid] = int(raw_num)
                elif vid:
                    result[vid] = idx + 1
            fetched += 1
        except Exception as exc:
            log.debug("get_album(%s) failed: %s", browse_id, exc)

    return result


def _parse_youtube_playlist_ytdlp(url: str) -> ParseResult:
    """Expand a YouTube playlist URL via yt-dlp --flat-playlist (fallback)."""
    from groove.downloader import fetch_playlist_entries

    try:
        raw_entries = fetch_playlist_entries(url)
    except RuntimeError as exc:
        return ParseResult(
            entries=[],
            format_detected="youtube_playlist",
            errors=[str(exc)],
        )

    entries: list[ParsedEntry] = []
    for item in raw_entries:
        title = item.get("title") or ""
        uploader = item.get("uploader") or ""
        entry_url = item.get("url") or ""
        raw = f"{uploader} - {title}" if uploader else title
        entries.append(ParsedEntry(
            raw_query=raw,
            kind="track",
            artist=uploader or None,
            title=title or None,
            source_url=entry_url or None,
            source_format="youtube_playlist",
        ))

    return ParseResult(
        entries=entries,
        format_detected="youtube_playlist",
        raw_line_count=len(raw_entries),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_key(entry: ParsedEntry) -> str:
    if entry.artist and entry.title:
        return _norm(f"{entry.artist} {entry.title}")
    if entry.artist and entry.album:
        return _norm(f"{entry.artist} {entry.album}")
    return _norm(entry.raw_query)


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()
