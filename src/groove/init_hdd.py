"""
HDD layout bootstrapper.

`groove init /Volumes/Music` creates the expected directory structure and
empty state files inside /Volumes/Music/groove/.

Safety: refuses to run if the groove/ subfolder already contains unrecognised
files, preventing accidental collision with existing drive contents.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Files that groove itself creates – used for the safety check
GROOVE_KNOWN_DIRS = {
    "library", "inbox", "inbox/cds", "inbox/downloads", "inbox/review",
    "state", "state/.locks", "state/archive", "db", "logs",
}
GROOVE_KNOWN_FILES = {
    "state/requests.json",
    "state/discoveries.json",
    "state/watchlist.json",
    "state/chart_runs.json",
    "state/import_log.json",
    "groove.toml",
    "beets.yaml",  # written by `groove init` after init_hdd() returns
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class InitError(Exception):
    pass


def init_hdd(drive_root: Path, *, force: bool = False) -> Path:
    """
    Bootstrap the groove/ folder on the drive at `drive_root`.
    Returns the path to the groove/ subfolder.
    """
    groove_root = drive_root / "groove"

    # Safety check
    if groove_root.exists() and not force:
        _check_no_alien_files(groove_root)

    # Create directory tree
    dirs = [
        groove_root / "library",
        groove_root / "inbox" / "cds",
        groove_root / "inbox" / "downloads",
        groove_root / "inbox" / "review",
        groove_root / "state" / ".locks",
        groove_root / "state" / "archive",
        groove_root / "db",
        groove_root / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Create empty state files
    state = groove_root / "state"
    _init_json_file(state / "requests.json", [])
    _init_json_file(state / "discoveries.json", [])
    _init_json_file(state / "watchlist.json", {"artists": []})
    _init_json_file(state / "chart_runs.json", [])
    _init_json_file(state / "import_log.json", [])

    # Verify write access
    test_file = groove_root / ".write_test"
    try:
        test_file.write_text("ok")
        test_file.unlink()
    except OSError as exc:
        raise InitError(f"Drive is not writable: {exc}") from exc

    return groove_root


def check_drive(drive_root: Path) -> dict:
    """
    Return a dict of drive health info (used by groove doctor).
    """
    info: dict = {}

    # Free space
    try:
        usage = shutil.disk_usage(drive_root)
        info["total_gb"] = round(usage.total / (1024 ** 3), 1)
        info["used_gb"] = round(usage.used / (1024 ** 3), 1)
        info["free_gb"] = round(usage.free / (1024 ** 3), 1)
        info["free_ok"] = info["free_gb"] >= 5.0
    except OSError as exc:
        info["disk_error"] = str(exc)

    # Filesystem type (macOS diskutil)
    try:
        result = subprocess.run(
            ["diskutil", "info", str(drive_root)],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "File System Personality" in line or "Type (Bundle)" in line:
                info["filesystem"] = line.split(":", 1)[-1].strip()
                break
        if "filesystem" not in info:
            info["filesystem"] = "unknown"
        fs_lower = info["filesystem"].lower()
        info["filesystem_ok"] = "ntfs" not in fs_lower
        if "ntfs" in fs_lower:
            info["filesystem_warning"] = (
                "NTFS detected. macOS can read NTFS but cannot write without third-party drivers. "
                "Reformat to exFAT for reliable read/write."
            )
    except Exception as exc:
        info["filesystem"] = "unknown"
        info["filesystem_ok"] = None
        info["filesystem_error"] = str(exc)

    # Write test
    test = drive_root / ".groove_write_test"
    try:
        test.write_text("ok")
        test.unlink()
        info["write_ok"] = True
    except OSError:
        info["write_ok"] = False

    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_json_file(path: Path, default: object) -> None:
    if not path.exists():
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")


def _check_no_alien_files(groove_root: Path) -> None:
    """Raise InitError if groove_root contains files not managed by groove."""
    for item in groove_root.iterdir():
        name = item.name
        rel = str(item.relative_to(groove_root))
        if name.startswith(".") or rel in GROOVE_KNOWN_DIRS or rel in GROOVE_KNOWN_FILES:
            continue
        if item.is_dir() and name in {"library", "inbox", "state", "db", "logs"}:
            continue
        raise InitError(
            f"Found unrecognised file/directory in groove/: {rel}\n"
            f"Use --force to override this check (existing files will NOT be deleted)."
        )
