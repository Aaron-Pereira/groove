"""
Pydantic data models and JSON file store.

Every write goes through:
  1. Acquire filelock on state/.locks/<name>.lock
  2. Serialize to <name>.json.tmp
  3. os.replace() over the real file (atomic on POSIX / exFAT)

This makes the store safe to use concurrently from the web process,
the background worker thread, and launchd scraper processes.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import uuid as _uuid

from filelock import FileLock
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def new_id() -> str:
    return str(_uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class DownloadRequest(BaseModel):
    id: str = Field(default_factory=new_id)
    raw_query: str
    kind: str = "track"  # "track" | "album" | "playlist"
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    source_url: str | None = None
    track_number: int | None = None
    status: str = "pending"
    # "pending" | "searching" | "downloading" | "tagging" | "done" | "failed"
    priority: str = "normal"  # "high" | "normal" | "low"
    attempts: int = 0
    error: str | None = None
    batch_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    # Resolved video/playlist URL after yt-dlp search
    resolved_url: str | None = None
    # Path inside inbox/downloads/ where files landed
    download_dir: str | None = None


# ---------------------------------------------------------------------------
# Discovery model
# ---------------------------------------------------------------------------


class Discovery(BaseModel):
    id: str = Field(default_factory=new_id)
    source: str  # "billboard" | "uk_top40" | "lastfm" | "lastfm_genre" | "new_release"
    chart_rank: int | None = None
    artist: str
    title: str | None = None
    album: str | None = None
    mb_recording_id: str | None = None
    mb_release_id: str | None = None
    seen_at: datetime = Field(default_factory=utcnow)
    auto_queued: bool = False
    dismissed: bool = False
    appearances: int = 1  # how many chart runs have seen this entry


# ---------------------------------------------------------------------------
# Watchlist model
# ---------------------------------------------------------------------------


class WatchedArtist(BaseModel):
    name: str
    mb_artist_id: str | None = None
    auto_download_new_albums: bool = False
    added_at: datetime = Field(default_factory=utcnow)


class Watchlist(BaseModel):
    artists: list[WatchedArtist] = []


# ---------------------------------------------------------------------------
# Chart run model
# ---------------------------------------------------------------------------


class ChartRun(BaseModel):
    id: str = Field(default_factory=new_id)
    source: str
    run_at: datetime = Field(default_factory=utcnow)
    items_found: int = 0
    new_discoveries: int = 0
    errors: list[str] = []


# ---------------------------------------------------------------------------
# Import log model
# ---------------------------------------------------------------------------


class ImportLogEntry(BaseModel):
    id: str = Field(default_factory=new_id)
    source: str  # "youtube" | "cd" | "manual"
    input_path: str
    final_path: str | None = None
    mb_match_confidence: float | None = None
    status: str  # "imported" | "skipped" | "review"
    beet_output: str | None = None
    imported_at: datetime = Field(default_factory=utcnow)
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Generic locked JSON store
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


class JsonStore(Generic[T]):
    """
    Thread-safe, process-safe JSON array store backed by a file.

    Usage:
        store = JsonStore(state_dir / "requests.json", DownloadRequest)
        store.all()          -> list[DownloadRequest]
        store.append(item)
        store.replace_all(items)
        store.update_one(id, patch_dict)
    """

    def __init__(self, path: Path, model: type[T]) -> None:
        self.path = path
        self.model = model
        self._lock_path = path.parent / ".locks" / f"{path.stem}.lock"
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self._lock_path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def all(self) -> list[T]:
        if not self.path.exists():
            return []
        with self._lock:
            return self._read()

    def append(self, item: T) -> None:
        with self._lock:
            items = self._read()
            items.append(item)
            self._write(items)

    def append_many(self, new_items: list[T]) -> None:
        with self._lock:
            items = self._read()
            items.extend(new_items)
            self._write(items)

    def replace_all(self, items: list[T]) -> None:
        with self._lock:
            self._write(items)

    def get(self, item_id: str) -> T | None:
        for item in self.all():
            if getattr(item, "id", None) == item_id:
                return item
        return None

    def update_one(self, item_id: str, patch: dict[str, Any]) -> T | None:
        """Update a single record by id. Returns the updated record or None."""
        with self._lock:
            items = self._read()
            updated: T | None = None
            new_items = []
            for item in items:
                if getattr(item, "id", None) == item_id:
                    data = item.model_dump()
                    data.update(patch)
                    if "updated_at" in self.model.model_fields:
                        data["updated_at"] = utcnow()
                    item = self.model.model_validate(data)
                    updated = item
                new_items.append(item)
            self._write(new_items)
        return updated

    def remove(self, item_id: str) -> bool:
        with self._lock:
            items = self._read()
            filtered = [i for i in items if getattr(i, "id", None) != item_id]
            if len(filtered) == len(items):
                return False
            self._write(filtered)
        return True

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.all():
            s = getattr(item, "status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold the lock)
    # ------------------------------------------------------------------

    def _read(self) -> list[T]:
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return []
        return [self.model.model_validate(d) for d in data]

    def _write(self, items: list[T]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = json.dumps(
            [item.model_dump(mode="json") for item in items],
            indent=2,
            ensure_ascii=False,
        )
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Object-keyed store (for watchlist.json which is a dict, not array)
# ---------------------------------------------------------------------------


class WatchlistStore:
    """Locked store for watchlist.json (stored as a JSON object, not array)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_path = path.parent / ".locks" / f"{path.stem}.lock"
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self._lock_path))

    def get(self) -> Watchlist:
        if not self.path.exists():
            return Watchlist()
        with self._lock:
            return self._read()

    def save(self, wl: Watchlist) -> None:
        with self._lock:
            self._write(wl)

    def add_artist(self, artist: WatchedArtist) -> None:
        with self._lock:
            wl = self._read()
            if not any(a.name.lower() == artist.name.lower() for a in wl.artists):
                wl.artists.append(artist)
            self._write(wl)

    def remove_artist(self, name: str) -> bool:
        with self._lock:
            wl = self._read()
            before = len(wl.artists)
            wl.artists = [a for a in wl.artists if a.name.lower() != name.lower()]
            self._write(wl)
        return len(wl.artists) < before

    def update_artist(self, name: str, patch: dict[str, Any]) -> bool:
        with self._lock:
            wl = self._read()
            found = False
            for a in wl.artists:
                if a.name.lower() == name.lower():
                    for k, v in patch.items():
                        setattr(a, k, v)
                    found = True
            if found:
                self._write(wl)
        return found

    def _read(self) -> Watchlist:
        try:
            raw = self.path.read_text(encoding="utf-8")
            return Watchlist.model_validate(json.loads(raw))
        except (json.JSONDecodeError, OSError):
            return Watchlist()

    def _write(self, wl: Watchlist) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(wl.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Convenience: open all stores given a Settings object
# ---------------------------------------------------------------------------


class Stores:
    """Thin namespace that holds all open stores for a given state directory."""

    def __init__(self, state_dir: Path) -> None:
        self.requests = JsonStore(state_dir / "requests.json", DownloadRequest)
        self.discoveries = JsonStore(state_dir / "discoveries.json", Discovery)
        self.chart_runs = JsonStore(state_dir / "chart_runs.json", ChartRun)
        self.import_log = JsonStore(state_dir / "import_log.json", ImportLogEntry)
        self.watchlist = WatchlistStore(state_dir / "watchlist.json")

    def rotate(self, today: date | None = None) -> None:
        """
        Archive old (done/failed/dismissed) records to state/archive/.
        Called nightly by the server.
        """
        if today is None:
            today = date.today()
        suffix = today.strftime("%Y-%m-%d")

        archive = self.requests.path.parent / "archive"
        archive.mkdir(parents=True, exist_ok=True)

        # Rotate requests: keep active, archive the rest
        all_req = self.requests.all()
        active = [r for r in all_req if r.status not in ("done", "failed")]
        finished = [r for r in all_req if r.status in ("done", "failed")]
        if finished:
            dst = archive / f"requests-{suffix}.json"
            dst.write_text(
                json.dumps([r.model_dump(mode="json") for r in finished], indent=2),
                encoding="utf-8",
            )
            self.requests.replace_all(active)

        # Rotate discoveries: keep un-dismissed, archive dismissed
        all_disc = self.discoveries.all()
        active_disc = [d for d in all_disc if not d.dismissed]
        dismissed = [d for d in all_disc if d.dismissed]
        if dismissed:
            dst = archive / f"discoveries-{suffix}.json"
            dst.write_text(
                json.dumps([d.model_dump(mode="json") for d in dismissed], indent=2),
                encoding="utf-8",
            )
            self.discoveries.replace_all(active_disc)
