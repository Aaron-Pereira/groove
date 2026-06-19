"""Configuration loading from groove.toml."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class AudioConfig(BaseModel):
    codec: str = "mp3"
    bitrate: str = "192"  # kbps


class DiscoveryConfig(BaseModel):
    billboard: bool = True
    uk_top40: bool = True
    lastfm_global: bool = True
    genres: list[str] = ["rock", "hip-hop", "electronic"]


class AutoQueueConfig(BaseModel):
    min_chart_appearances: int = 2


class ApiKeysConfig(BaseModel):
    acoustid: str = ""
    lastfm_api_key: str = ""
    lastfm_api_secret: str = ""
    # Spotify API — get a free key at https://developer.spotify.com/dashboard
    # groove uses Client Credentials flow (no user login). Spotify's app form
    # requires a redirect URI — use http://127.0.0.1 (it is never called).
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # Deezer ARL cookie — log into deezer.com, open DevTools → Application →
    # Cookies → copy the value of the `arl` cookie. Reserved for future use.
    deezer_arl: str = ""


class WorkerConfig(BaseModel):
    poll_interval_seconds: int = 5
    max_retries: int = 3
    min_free_space_gb: float = 5.0


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """Loaded from groove.toml – the single source of truth for runtime config."""

    hdd_root: Path = Path("/Volumes/Music/groove")
    web: WebConfig = WebConfig()
    audio: AudioConfig = AudioConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    auto_queue: AutoQueueConfig = AutoQueueConfig()
    api_keys: ApiKeysConfig = ApiKeysConfig()
    worker: WorkerConfig = WorkerConfig()

    # ---------------------------------------------------------------------------
    # Derived paths (all relative to hdd_root)
    # ---------------------------------------------------------------------------

    @property
    def library_dir(self) -> Path:
        return self.hdd_root / "library"

    @property
    def inbox_downloads_dir(self) -> Path:
        return self.hdd_root / "inbox" / "downloads"

    @property
    def inbox_cds_dir(self) -> Path:
        return self.hdd_root / "inbox" / "cds"

    @property
    def inbox_review_dir(self) -> Path:
        return self.hdd_root / "inbox" / "review"

    @property
    def state_dir(self) -> Path:
        return self.hdd_root / "state"

    @property
    def locks_dir(self) -> Path:
        return self.state_dir / ".locks"

    @property
    def archive_dir(self) -> Path:
        return self.state_dir / "archive"

    @property
    def db_dir(self) -> Path:
        return self.hdd_root / "db"

    @property
    def logs_dir(self) -> Path:
        return self.hdd_root / "logs"

    @property
    def beets_db(self) -> Path:
        return self.db_dir / "musiclib.db"

    @property
    def beets_config(self) -> Path:
        # Prefer the drive-local copy written by `groove init` with the correct
        # absolute paths for this installation.  Fall back to the repo template
        # only when the drive copy doesn't exist yet (e.g. before first init).
        drive_config = self.hdd_root / "beets.yaml"
        if drive_config.exists():
            return drive_config
        return _repo_root() / "config" / "beets.yaml"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path("/Volumes/Music/groove/groove.toml")
_REPO_CONFIG_EXAMPLE = Path(__file__).parent.parent.parent / "config" / "groove.toml.example"

_settings_cache: Settings | None = None


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from groove.toml, falling back to defaults."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache

    env_config = os.environ.get("GROOVE_CONFIG", "").strip()
    # Search order:
    #   1–2: explicit path / GROOVE_CONFIG
    #   3:   sibling ../groove-data/groove.toml (workspace-local data + config)
    #   4:   repo config/groove.toml (dev override)
    #   5:   /Volumes/Music/groove/groove.toml (typical external-drive install)
    _workspace_data_config = _repo_root().parent / "groove-data" / "groove.toml"
    candidates = [
        path,
        Path(env_config) if env_config else None,
        _workspace_data_config,
        _repo_root() / "config" / "groove.toml",
        _DEFAULT_CONFIG_PATH,
    ]

    for candidate in candidates:
        if candidate and candidate.is_file():
            with open(candidate, "rb") as f:
                data = tomllib.load(f)
            _settings_cache = Settings.model_validate(data)
            return _settings_cache

    # No config file found – use defaults (useful for first-run / tests)
    _settings_cache = Settings()
    return _settings_cache


def reload_settings(path: Path | None = None) -> Settings:
    """Force reload (mainly for tests)."""
    global _settings_cache
    _settings_cache = None
    return load_settings(path)


def get_settings() -> Settings:
    """FastAPI dependency – returns cached settings."""
    return load_settings()
