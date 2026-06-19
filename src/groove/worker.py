"""
Queue worker.

Polls requests.json and drives each request through the state machine:

  pending → searching → downloading → tagging → done
                                              ↘ failed (after max_retries)

Designed to run in a background thread inside the FastAPI process,
or as a standalone process (groove worker).
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import UTC, datetime, timedelta

from groove.config import Settings
from groove.downloader import download, strip_track_numbers, tag_downloaded_files
from groove.importer import import_directory
from groove.store import DownloadRequest, Stores

log = logging.getLogger(__name__)

_ACTIVE_STATUSES = {"pending", "searching", "downloading", "tagging"}
_TERMINAL_STATUSES = {"done", "failed"}
_INFLIGHT_STALE_AFTER = timedelta(minutes=20)


# ---------------------------------------------------------------------------
# Worker class
# ---------------------------------------------------------------------------


class Worker:
    def __init__(self, settings: Settings, stores: Stores) -> None:
        self.settings = settings
        self.stores = stores
        self._running = False

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """Block and poll indefinitely."""
        self._running = True
        self._reset_stale_requests()
        log.info("Worker started; polling every %ds", self.settings.worker.poll_interval_seconds)
        while self._running:
            try:
                self._process_one()
            except Exception:
                log.exception("Unhandled error in worker loop")
            time.sleep(self.settings.worker.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    def run_once(self) -> bool:
        """Process one pending request. Returns True if work was done."""
        return self._process_one()

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _process_one(self) -> bool:
        """Pick the highest-priority pending request and work on it."""
        self._recover_stale_inflight_requests()
        if not self._check_free_space():
            return False

        request = self._pick_next()
        if request is None:
            return False

        log.info("Processing request %s: %s", request.id, request.raw_query)
        self._handle(request)
        return True

    def _pick_next(self) -> DownloadRequest | None:
        all_requests = self.stores.requests.all()
        pending = [r for r in all_requests if r.status == "pending"]
        if not pending:
            return None
        # Sort by priority (high > normal > low), then by retry attempt count
        # so one flaky request does not starve newer pending items.
        priority_order = {"high": 0, "normal": 1, "low": 2}
        pending.sort(
            key=lambda r: (
                priority_order.get(r.priority, 1),
                r.attempts,
                r.created_at,
            )
        )
        return pending[0]

    def _handle(self, request: DownloadRequest) -> None:
        try:
            self._transition(request.id, "searching")
            resolved = self._resolve(request)

            self._transition(request.id, "downloading", resolved_url=resolved)
            result = download(
                resolved or request.raw_query,
                self.settings,
                request_id=request.id,
                kind=request.kind,
                artist=request.artist,
                album=request.album,
                title=request.title,
                is_playlist=(request.kind == "playlist"),
            )
            if not result.success:
                raise RuntimeError(result.error or "Download failed")

            download_dir = str(result.dest_dir)
            source_kind = result.backend or "unknown"
            self._transition(request.id, "tagging", download_dir=download_dir)

            trust_source_metadata = source_kind in (
                "ytmusic_playlist", "ytmusic", "spotify_ytmusic",
            )

            if result.files and not trust_source_metadata:
                strip_track_numbers(result.files)

            if result.files and any((request.artist, request.title, request.album,
                                     request.track_number is not None)):
                n = tag_downloaded_files(
                    result.files,
                    artist=request.artist,
                    title=request.title,
                    album=request.album,
                    track_number=request.track_number,
                )
                log.info("Tagged %d/%d file(s) with request metadata", n, len(result.files))

            import_result = import_directory(
                result.dest_dir,
                self.settings,
                source_kind=source_kind,
                skip_autotag=trust_source_metadata,
                request_id=request.id,
                import_log=self.stores.import_log,
            )
            if not import_result.success and not import_result.imported:
                raise RuntimeError(import_result.error or "beet import failed")

            self._transition(request.id, "done", completed_at=datetime.now(UTC))
            log.info("Request %s done via %s", request.id, source_kind)

        except Exception as exc:
            log.exception("Request %s failed: %s", request.id, exc)
            current = self.stores.requests.get(request.id)
            attempts = (current.attempts if current else 0) + 1
            max_retries = self.settings.worker.max_retries
            if attempts >= max_retries:
                self._transition(
                    request.id,
                    "failed",
                    error=str(exc),
                    attempts=attempts,
                    completed_at=datetime.now(UTC),
                )
            else:
                # Back to pending for retry
                self._transition(
                    request.id,
                    "pending",
                    error=str(exc),
                    attempts=attempts,
                )

    def _reset_stale_requests(self) -> None:
        """Reset any requests stuck in intermediate states back to pending.

        Requests can get stranded in 'searching', 'downloading', or 'tagging'
        if the worker process is killed mid-flight.  On startup we reset them
        so they are retried rather than left frozen forever.
        """
        stale_statuses = {"searching", "downloading", "tagging"}
        for req in self.stores.requests.all():
            if req.status in stale_statuses:
                log.warning(
                    "Resetting stale request %s ('%s') from '%s' → pending",
                    req.id,
                    req.raw_query,
                    req.status,
                )
                self.stores.requests.update_one(
                    req.id,
                    {"status": "pending", "error": f"Reset after restart (was: {req.status})"},
                )

    def _recover_stale_inflight_requests(self) -> None:
        """
        During normal runtime, recover requests stuck in in-flight states for
        too long (e.g. interrupted import/download that never raised cleanly).
        """
        now = datetime.now(UTC)
        stale_statuses = {"searching", "downloading", "tagging"}
        for req in self.stores.requests.all():
            if req.status not in stale_statuses:
                continue
            if req.updated_at >= (now - _INFLIGHT_STALE_AFTER):
                continue
            log.warning(
                "Recovering stale in-flight request %s ('%s') from '%s' → pending",
                req.id,
                req.raw_query,
                req.status,
            )
            self.stores.requests.update_one(
                req.id,
                {
                    "status": "pending",
                    "error": (
                        f"Auto-reset stale in-flight request after "
                        f"{int(_INFLIGHT_STALE_AFTER.total_seconds() / 60)}m"
                    ),
                },
            )

    def _resolve(self, request: DownloadRequest) -> str:
        """
        Return a concrete URL if one was supplied directly, otherwise return
        the raw query so the downloader's backend chain can handle it.

        The downloader builds its own search queries from the structured
        artist/album/title fields — no need to embed ytsearch: prefixes here.
        """
        if request.source_url and _is_url(request.source_url):
            return request.source_url
        if _is_url(request.raw_query):
            return request.raw_query
        return request.raw_query

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _transition(self, request_id: str, new_status: str, **extra: object) -> None:
        patch: dict = {"status": new_status, **extra}
        self.stores.requests.update_one(request_id, patch)
        log.debug("Request %s → %s", request_id, new_status)

    # ------------------------------------------------------------------
    # Safeguards
    # ------------------------------------------------------------------

    def _check_free_space(self) -> bool:
        """Refuse to start downloads if the drive is nearly full or unreachable."""
        try:
            usage = shutil.disk_usage(self.settings.hdd_root)
            free_gb = usage.free / (1024 ** 3)
            threshold = self.settings.worker.min_free_space_gb
            if free_gb < threshold:
                log.warning(
                    "Free space %.1f GB is below threshold %.1f GB – worker paused",
                    free_gb,
                    threshold,
                )
                return False
        except OSError:
            log.warning(
                "Could not check free space for %s – worker paused until drive is accessible",
                self.settings.hdd_root,
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))
