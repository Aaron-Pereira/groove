"""
groove doctor – health check for the whole system.

Checks:
  - Drive free space and filesystem type
  - Write access to hdd_root
  - beets installation and plugin loading
  - yt-dlp installation
  - ffmpeg installation
  - API key presence (AcoustID, Last.fm)
  - State files are valid JSON
  - Nightly state rsync to ~/groove-state-backup/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


def _find_beet() -> str:
    venv_beet = Path(sys.executable).parent / "beet"
    return str(venv_beet) if venv_beet.exists() else "beet"

Status = Literal["ok", "warning", "error", "skip"]


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    detail: str | None = None


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status in ("ok", "skip") for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warning" for c in self.checks)

    @property
    def has_errors(self) -> bool:
        return any(c.status == "error" for c in self.checks)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)


def run_doctor(settings) -> DoctorReport:
    """Run all health checks and return a DoctorReport."""
    from groove.init_hdd import check_drive

    report = DoctorReport()

    # -- Drive checks ---------------------------------------------------
    if settings.hdd_root.exists():
        drive_info = check_drive(settings.hdd_root)

        free_gb = drive_info.get("free_gb", 0)
        if drive_info.get("disk_error"):
            report.add(CheckResult("free_space", "error", f"Cannot read disk: {drive_info['disk_error']}"))
        elif free_gb < 1.0:
            report.add(CheckResult("free_space", "error", f"Critical: only {free_gb:.1f} GB free"))
        elif free_gb < 5.0:
            report.add(CheckResult("free_space", "warning", f"Low: {free_gb:.1f} GB free (threshold: 5 GB)"))
        else:
            report.add(CheckResult("free_space", "ok", f"{free_gb:.1f} GB free of {drive_info.get('total_gb', '?')} GB"))

        fs = drive_info.get("filesystem", "unknown")
        if drive_info.get("filesystem_warning"):
            report.add(CheckResult("filesystem", "warning", drive_info["filesystem_warning"]))
        elif fs == "unknown":
            report.add(CheckResult("filesystem", "warning", "Could not determine filesystem type"))
        else:
            report.add(CheckResult("filesystem", "ok", f"Filesystem: {fs}"))

        write_ok = drive_info.get("write_ok", False)
        report.add(CheckResult(
            "write_access",
            "ok" if write_ok else "error",
            "Drive is writable" if write_ok else f"Drive at {settings.hdd_root} is not writable",
        ))
    else:
        report.add(CheckResult(
            "hdd_root",
            "error",
            f"Drive root not found: {settings.hdd_root}\nRun `groove init` first.",
        ))

    # -- Binaries -------------------------------------------------------
    report.add(_check_binary("yt-dlp", ["yt-dlp", "--version"]))
    report.add(_check_binary("ffmpeg", ["ffmpeg", "-version"]))
    report.add(_check_binary("beets", [_find_beet(), "--version"]))

    # -- Beets plugins --------------------------------------------------
    beets_check = _check_beets_plugins(settings)
    report.add(beets_check)

    # -- API keys -------------------------------------------------------
    if settings.api_keys.acoustid:
        report.add(CheckResult("acoustid_key", "ok", "AcoustID API key present"))
    else:
        report.add(CheckResult(
            "acoustid_key", "warning",
            "AcoustID API key not set – audio fingerprinting disabled. "
            "Get a free key at https://acoustid.org/new-application",
        ))

    if settings.api_keys.lastfm_api_key:
        report.add(CheckResult("lastfm_key", "ok", "Last.fm API key present"))
    else:
        report.add(CheckResult(
            "lastfm_key", "warning",
            "Last.fm API key not set – genre tagging and Last.fm charts disabled. "
            "Get a free key at https://www.last.fm/api/account/create",
        ))

    # -- State files ----------------------------------------------------
    state_dir = settings.state_dir
    if state_dir.exists():
        for name in ["requests.json", "discoveries.json", "watchlist.json", "chart_runs.json"]:
            path = state_dir / name
            if not path.exists():
                report.add(CheckResult(f"state:{name}", "warning", f"Missing state file: {path.name} (run groove init)"))
            else:
                try:
                    json.loads(path.read_text())
                    report.add(CheckResult(f"state:{name}", "ok", f"{name} is valid JSON"))
                except json.JSONDecodeError as exc:
                    report.add(CheckResult(f"state:{name}", "error", f"{name} contains invalid JSON: {exc}"))
    else:
        report.add(CheckResult("state_dir", "error", f"State directory not found: {state_dir}"))

    return report


def backup_state(settings) -> CheckResult:
    """rsync state/ to ~/groove-state-backup/."""
    backup_dir = Path.home() / "groove-state-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["rsync", "-av", "--delete", str(settings.state_dir) + "/", str(backup_dir) + "/"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return CheckResult("state_backup", "ok", f"State synced to {backup_dir}")
        else:
            return CheckResult("state_backup", "warning", f"rsync returned {result.returncode}: {result.stderr[:200]}")
    except FileNotFoundError:
        return CheckResult("state_backup", "warning", "rsync not found – skipping backup")
    except Exception as exc:
        return CheckResult("state_backup", "error", f"Backup failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_binary(name: str, cmd: list[str]) -> CheckResult:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_line = (result.stdout or result.stderr).splitlines()[0][:80]
            return CheckResult(name, "ok", f"{name} found: {version_line}")
        else:
            return CheckResult(name, "error", f"{name} returned non-zero exit code")
    except FileNotFoundError:
        install_hints = {
            "yt-dlp": "brew install yt-dlp",
            "ffmpeg": "brew install ffmpeg",
            "beets": "uv add beets",
        }
        hint = install_hints.get(name, f"install {name}")
        return CheckResult(name, "error", f"{name} not found. Install: {hint}")
    except Exception as exc:
        return CheckResult(name, "error", f"Error checking {name}: {exc}")


def _check_beets_plugins(settings) -> CheckResult:
    expected_plugins = [
        "musicbrainz", "fetchart", "embedart", "lyrics", "replaygain",
        "lastgenre", "scrub", "duplicates", "chroma", "mbsync",
    ]
    try:
        result = subprocess.run(
            [_find_beet(), "--config", str(settings.beets_config), "config", "-p"],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout + result.stderr
        missing = [p for p in expected_plugins if p not in output]
        if missing:
            return CheckResult(
                "beets_plugins",
                "warning",
                f"Possibly missing beets plugins: {', '.join(missing)}\n"
                f"Install with: pip install beets[{','.join(missing)}]",
            )
        return CheckResult("beets_plugins", "ok", "All expected beets plugins configured")
    except Exception as exc:
        return CheckResult("beets_plugins", "warning", f"Could not verify beets plugins: {exc}")
