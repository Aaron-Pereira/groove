"""
beets import wrapper.

Shells out to `beet import` and parses its output to detect
success/failure/review outcomes for each imported directory.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from groove.config import Settings
from groove.store import ImportLogEntry, JsonStore

log = logging.getLogger(__name__)

_AUDIO_SUFFIXES = frozenset({"mp3", "m4a", "flac", "ogg", "opus", "wav"})

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


# beets quiet singleton import still exits 0 when the track is a duplicate of
# something already in the library — audio is left behind in inbox/downloads.
_DUP_ALREADY_IN_LIB_RE = re.compile(
    r"already\s+in\s+(?:the\s+)?library",
    re.IGNORECASE,
)
_SKIPPED_PATH_RE = re.compile(r"Skipped\s+\d+\s+paths?\.", re.IGNORECASE)
_SKIPPING_RE = re.compile(r"^\s*Skipping\.?\s*$", re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ImportResult:
    def __init__(
        self,
        *,
        success: bool,
        imported: list[str],
        skipped: list[str],
        review: list[str],
        output: str,
        error: str | None = None,
    ):
        self.success = success
        self.imported = imported
        self.skipped = skipped
        self.review = review
        self.output = output
        self.error = error

    def __repr__(self) -> str:
        return (
            f"ImportResult(imported={len(self.imported)}, "
            f"skipped={len(self.skipped)}, review={len(self.review)}, "
            f"success={self.success})"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_directory(
    directory: Path,
    settings: Settings,
    *,
    interactive: bool = False,
    timid: bool = False,
    source_kind: str = "youtube",
    skip_autotag: bool = False,
    request_id: str | None = None,
    import_log: JsonStore | None = None,
) -> ImportResult:
    """
    Run `beet import` on a directory.

    - `interactive=False` (default): quiet mode (-q), auto-accepts strong
      matches, moves weak ones to inbox/review/.
    - `interactive=True`: timid mode (-t), prompts on the terminal.
    - `timid=True`: adds -t flag (for CD ingest).
    - `skip_autotag=True`: import as-is without MusicBrainz matching (-A).
      Use when metadata is already trusted (e.g. from YouTube Music).
    """
    beet_config = settings.beets_config
    beet_exe = _find_beet()

    # Inject API keys as environment variables beets can read
    env = os.environ.copy()
    if settings.api_keys.acoustid:
        env["ACOUSTID_APIKEY"] = settings.api_keys.acoustid
    if settings.api_keys.lastfm_api_key:
        env["LASTFM_APIKEY"] = settings.api_keys.lastfm_api_key

    cmd = [beet_exe, "--config", str(beet_config), "import"]

    if skip_autotag:
        cmd.append("-A")  # no autotag: trust existing metadata
    elif interactive or timid:
        cmd.append("-t")  # timid: ask on weak matches
    else:
        cmd.append("-q")  # quiet: auto-accept strong, skip weak

    # Disable incremental tracking for worker-driven imports so retries on the
    # same request folder are always reconsidered (prevents "Skipped 1 paths").
    if source_kind != "cd":
        cmd.append("-I")  # --noincremental

    # Always move from inbox into the library; local beets.yaml may use copy and
    # would leave duplicate/stale files under inbox/downloads.
    cmd.append("-m")

    # Single-track downloads (any source): use singleton mode so beets doesn't
    # cluster a lone track with other singles into a phantom "album".
    if source_kind != "cd" and _count_audio_files(directory) <= 1:
        cmd.append("-s")  # singleton: import as individual track

    cmd.append(str(directory))

    log.info("Running: %s", " ".join(cmd))

    proc, run_error = _run_beet(cmd, env=env)
    if run_error is not None:
        return ImportResult(
            success=False,
            imported=[],
            skipped=[],
            review=[],
            output="",
            error=run_error,
        )

    output = proc.stdout + proc.stderr
    output_scan = _strip_ansi(output)
    imported, skipped, review = _parse_output(output)

    success = proc.returncode == 0
    leftover_error: str | None = None

    # beet can exit 0 while leaving files: duplicate singleton, or other skips.
    if success and source_kind != "cd" and _count_audio_files(directory) > 0:
        if _DUP_ALREADY_IN_LIB_RE.search(output_scan):
            # Same song already in the library — treat as success and remove the
            # redundant inbox copy so the worker does not retry forever.
            _remove_audio_files_in_dir(directory)
            if _count_audio_files(directory) > 0:
                log.error(
                    "Could not clear duplicate inbox files in %s\n%s",
                    directory,
                    output[-1500:],
                )
                success = False
                leftover_error = "beets reported duplicate but inbox files could not be removed"
            else:
                log.info(
                    "beets skipped duplicate (already in library); removed inbox copy under %s",
                    directory,
                )
        elif _looks_like_quiet_skip(output_scan):
            # quiet_fallback: skip => beets exits 0, leaves files in place.
            # Auto-retry with as-is import so requests do not get stuck forever.
            log.warning(
                "beets skipped import for %s; retrying with quiet as-is import "
                "(-q -A -I --quiet-fallback=asis)",
                directory,
            )
            # Retry must stay non-interactive: without -q, beet blocks on stdin
            # (duplicate resolution, matching prompts) while we capture output.
            retry_cmd = [
                beet_exe,
                "--config",
                str(beet_config),
                "import",
                "-q",
                "-A",
                "-I",
                "-m",
                "--quiet-fallback=asis",
            ]
            if source_kind != "cd" and _count_audio_files(directory) <= 1:
                retry_cmd.append("-s")
            retry_cmd.append(str(directory))
            retry_proc, retry_error = _run_beet(retry_cmd, env=env)
            if retry_error is not None:
                success = False
                leftover_error = f"beets skip fallback failed: {retry_error}"
            else:
                retry_output = retry_proc.stdout + retry_proc.stderr
                output += "\n\n--- as-is fallback ---\n" + retry_output
                remaining = _count_audio_files(directory)
                retry_scan = _strip_ansi(retry_output)
                if retry_proc.returncode == 0 and remaining == 0:
                    success = True
                    imported, skipped, review = _parse_output(output)
                    log.info("As-is fallback import succeeded for %s", directory)
                elif _inbox_should_clear_after_beets_ok(
                    retry_proc.returncode, remaining, retry_scan
                ):
                    # Duplicates / quiet skips often omit machine-readable text
                    # (import.log file handler, log line prefixes), or beets exits 0
                    # without moving when config uses copy — clear stale inbox audio.
                    _remove_audio_files_in_dir(directory)
                    if _count_audio_files(directory) == 0:
                        success = True
                        imported, skipped, review = _parse_output(output)
                        log.info(
                            "As-is fallback: removed redundant inbox copy under %s "
                            "(beets declined to take files; see combined output if needed)",
                            directory,
                        )
                    else:
                        success = False
                        leftover_error = (
                            "beets reported duplicate but inbox files could not be removed"
                        )
                else:
                    success = False
                    leftover_error = (
                        "beets skipped confident match, then as-is fallback did not clear inbox files"
                    )
                    log.error(
                        "As-is fallback left audio in %s (rc=%s files_left=%s)\n%s",
                        directory,
                        retry_proc.returncode,
                        remaining,
                        retry_output[-2500:],
                    )
        else:
            log.error(
                "beet import returned success but audio files remain in %s\n%s",
                directory,
                output[-1500:],
            )
            success = False
            leftover_error = (
                "beet import left files in the download folder; see beets output in log"
            )

    if import_log is not None:
        for path in imported:
            import_log.append(
                ImportLogEntry(
                    source=source_kind,
                    input_path=str(directory),
                    final_path=path,
                    status="imported",
                    beet_output=output[:2000],
                    request_id=request_id,
                )
            )
        for path in review:
            import_log.append(
                ImportLogEntry(
                    source=source_kind,
                    input_path=str(directory),
                    final_path=None,
                    status="review",
                    beet_output=output[:2000],
                    request_id=request_id,
                )
            )

    return ImportResult(
        success=success,
        imported=imported,
        skipped=skipped,
        review=review,
        output=output,
        error=None if success else (leftover_error or _extract_error(output)),
    )


def import_cds(settings: Settings, directory: Path | None = None) -> ImportResult:
    """
    Interactive import for CD rips (timid mode so beets asks for confirmation).
    """
    target = directory or settings.inbox_cds_dir
    return import_directory(target, settings, interactive=True, timid=True, source_kind="cd")


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

# beets output patterns (approximate - beets doesn't have a stable machine-readable output)
_IMPORTED_RE = re.compile(r"Tagging:\s+(.+)", re.IGNORECASE)
_SKIPPED_RE = re.compile(r"Skipping\s+(.+)", re.IGNORECASE)
_REVIEW_RE = re.compile(r"(No match found|low confidence|moved to review)\s*[:\-]?\s*(.+)?", re.IGNORECASE)
_SENT_TO_REVIEW_RE = re.compile(r"Moved.*?review", re.IGNORECASE)
_IMPORT_TASK_RE = re.compile(r"importing\s+(.+\.(?:mp3|flac|m4a|ogg|opus|wav))", re.IGNORECASE)


def _parse_output(output: str) -> tuple[list[str], list[str], list[str]]:
    imported: list[str] = []
    skipped: list[str] = []
    review: list[str] = []

    for line in output.splitlines():
        line = line.strip()
        if m := _IMPORTED_RE.match(line):
            imported.append(m.group(1).strip())
        elif m := _SKIPPED_RE.match(line):
            skipped.append(m.group(1).strip())
        elif _REVIEW_RE.search(line) or _SENT_TO_REVIEW_RE.search(line):
            review.append(line)

    return imported, skipped, review


def _extract_error(output: str) -> str | None:
    for line in output.splitlines():
        if "error" in line.lower() or "traceback" in line.lower():
            return line.strip()[:500]
    return output.strip()[-500:] if output.strip() else None


def _run_beet(cmd: list[str], *, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    """Run beet command and normalize timeout/not-found errors."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        return proc, None
    except subprocess.TimeoutExpired:
        return None, "beet import timed out after 5 minutes"
    except FileNotFoundError:
        return None, f"beet not found at {cmd[0]}; run: uv sync"


def _inbox_should_clear_after_beets_ok(returncode: int, files_left: int, scan_text: str) -> bool:
    """
    True when beets exited OK but left audio in an inbox folder in situations
    where keeping those files causes the worker to retry forever — duplicates,
    quiet skips (often only visible inside prefixed log lines), etc.
    """
    if returncode != 0 or files_left <= 0:
        return False
    if _DUP_ALREADY_IN_LIB_RE.search(scan_text):
        return True
    if _looks_like_quiet_skip(scan_text):
        return True
    # e.g. "This album is …" duplicated across lines / color spans
    if re.search(r"\bis\s+already\s+in\s+(?:the\s+)?library", scan_text, re.IGNORECASE):
        return True
    return False


def _looks_like_quiet_skip(output: str) -> bool:
    """True when quiet import skipped paths due to no confident match."""
    if _SKIPPED_PATH_RE.search(output) or _SKIPPING_RE.search(output):
        return True
    # Log-style lines: "Skipping." / "Skipped N paths." with level prefixes or ANSI.
    return bool(re.search(r"\bSkipping\.?", output, re.IGNORECASE))


def _remove_audio_files_in_dir(directory: Path) -> int:
    """Delete audio files anywhere under directory (recursive). Returns number removed."""
    removed = 0
    try:
        for p in list(directory.rglob("*")):
            if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_SUFFIXES:
                try:
                    p.unlink()
                    removed += 1
                except OSError as exc:
                    log.warning("Could not remove %s: %s", p, exc)
    except OSError as exc:
        log.warning("Could not scan %s: %s", directory, exc)
    return removed


def _count_audio_files(directory: Path) -> int:
    """Count audio files anywhere under directory (recursive)."""
    n = 0
    try:
        for p in directory.rglob("*"):
            if p.is_file() and p.suffix.lower().lstrip(".") in _AUDIO_SUFFIXES:
                n += 1
    except OSError:
        return 0
    return n


def _find_beet() -> str:
    """
    Return the path to the beet executable.
    Prefers the one in the same venv as the running Python interpreter,
    then falls back to whatever is on PATH.
    """
    # Same directory as the current Python executable (works inside uv venv)
    venv_beet = Path(sys.executable).parent / "beet"
    if venv_beet.exists():
        return str(venv_beet)
    return "beet"  # fall back to PATH
