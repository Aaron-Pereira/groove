"""
Auto-queue rules.

Promotes discoveries to the requests queue when they meet configured thresholds:
  1. min_chart_appearances: a track that appears on N or more charts this week
     is automatically queued.
  2. watchlist auto_download_new_albums: new releases for watched artists with
     auto_download_new_albums=True are automatically queued.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from groove.config import Settings
from groove.store import Discovery, DownloadRequest, Stores

log = logging.getLogger(__name__)


def run_autoqueue(stores: Stores, settings: Settings) -> int:
    """
    Check discoveries and watchlist; auto-queue eligible items.
    Returns the number of requests added.
    """
    added = 0
    added += _queue_chart_appearances(stores, settings)
    added += _queue_watchlist_releases(stores, settings)
    return added


def _queue_chart_appearances(stores: Stores, settings: Settings) -> int:
    """Queue tracks that appeared on >= min_chart_appearances charts this week."""
    min_appearances = settings.auto_queue.min_chart_appearances
    if min_appearances <= 0:
        return 0

    week_ago = datetime.now(UTC) - timedelta(days=7)
    recent = [
        d for d in stores.discoveries.all()
        if not d.auto_queued and not d.dismissed and d.seen_at >= week_ago
    ]

    # Count how many distinct chart sources each (artist, title) appears on
    key_to_sources: dict[tuple[str, str], set[str]] = {}
    key_to_discovery: dict[tuple[str, str], Discovery] = {}

    for d in recent:
        if not d.title:
            continue
        key = (_norm(d.artist), _norm(d.title))
        if key not in key_to_sources:
            key_to_sources[key] = set()
            key_to_discovery[key] = d
        key_to_sources[key].add(d.source)

    pending_keys = _get_pending_keys(stores)
    added = 0

    for key, sources in key_to_sources.items():
        if len(sources) >= min_appearances:
            disc = key_to_discovery[key]
            if key in pending_keys:
                log.debug("Auto-queue skip (already pending): %s - %s", disc.artist, disc.title)
                continue

            req = DownloadRequest(
                raw_query=f"{disc.artist} - {disc.title}",
                kind="track",
                artist=disc.artist,
                title=disc.title,
                priority="low",
            )
            stores.requests.append(req)
            stores.discoveries.update_one(disc.id, {"auto_queued": True})
            log.info(
                "Auto-queued (appeared on %d charts): %s - %s",
                len(sources), disc.artist, disc.title,
            )
            added += 1

    return added


def _queue_watchlist_releases(stores: Stores, settings: Settings) -> int:
    """Queue new album releases for watched artists with auto_download_new_albums=True."""
    watchlist = stores.watchlist.get()
    auto_artists = {
        a.name.lower()
        for a in watchlist.artists
        if a.auto_download_new_albums
    }
    if not auto_artists:
        return 0

    pending_keys = _get_pending_keys(stores)
    added = 0

    new_release_discoveries = [
        d for d in stores.discoveries.all()
        if d.source == "new_release"
        and not d.auto_queued
        and not d.dismissed
        and d.artist.lower() in auto_artists
    ]

    for disc in new_release_discoveries:
        if not disc.album:
            continue
        key = (_norm(disc.artist), _norm(disc.album))
        if key in pending_keys:
            continue
        req = DownloadRequest(
            raw_query=f"{disc.artist} - {disc.album}",
            kind="album",
            artist=disc.artist,
            album=disc.album,
            priority="low",
        )
        stores.requests.append(req)
        stores.discoveries.update_one(disc.id, {"auto_queued": True})
        log.info("Auto-queued new album: %s - %s", disc.artist, disc.album)
        added += 1

    return added


def _get_pending_keys(stores: Stores) -> set[tuple[str, str]]:
    """Return a set of (artist_norm, title_or_album_norm) for active requests."""
    keys: set[tuple[str, str]] = set()
    for req in stores.requests.all():
        if req.status in ("done", "failed"):
            continue
        if req.artist and req.title:
            keys.add((_norm(req.artist), _norm(req.title)))
        if req.artist and req.album:
            keys.add((_norm(req.artist), _norm(req.album)))
    return keys


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()
