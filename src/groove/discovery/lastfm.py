"""
Last.fm global top tracks scraper (using pylast).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pylast

from groove.store import ChartRun, Discovery

log = logging.getLogger(__name__)


def scrape_global_top(
    api_key: str,
    api_secret: str,
    limit: int = 100,
) -> tuple[list[Discovery], ChartRun]:
    """Fetch Last.fm global top tracks chart."""
    run_at = datetime.now(UTC)
    errors: list[str] = []
    discoveries: list[Discovery] = []

    if not api_key:
        errors.append("Last.fm API key not configured (set api_keys.lastfm_api_key in groove.toml)")
        return discoveries, ChartRun(source="lastfm", run_at=run_at, errors=errors)

    try:
        network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
        tracks = network.get_top_tracks(limit=limit)
        for rank, item in enumerate(tracks, 1):
            track = item.item
            artist_name = track.artist.name if track.artist else "Unknown"
            title = track.title
            discoveries.append(Discovery(
                source="lastfm",
                chart_rank=rank,
                artist=artist_name,
                title=title,
                seen_at=run_at,
            ))
    except Exception as exc:
        log.exception("Last.fm global top scrape failed: %s", exc)
        errors.append(str(exc))

    run = ChartRun(
        source="lastfm",
        run_at=run_at,
        items_found=len(discoveries),
        errors=errors,
    )
    log.info("Last.fm global: %d tracks, %d errors", len(discoveries), len(errors))
    return discoveries, run
