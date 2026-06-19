"""
Last.fm per-genre top tracks scraper.

Uses chart.getTopTracks filtered by tag (Last.fm tag = genre).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pylast

from groove.store import ChartRun, Discovery

log = logging.getLogger(__name__)


def scrape_genre(
    genre: str,
    api_key: str,
    api_secret: str,
    limit: int = 50,
) -> tuple[list[Discovery], ChartRun]:
    """Fetch top tracks for a specific genre/tag from Last.fm."""
    source = f"lastfm_genre:{genre}"
    run_at = datetime.now(UTC)
    errors: list[str] = []
    discoveries: list[Discovery] = []

    if not api_key:
        errors.append("Last.fm API key not configured")
        return discoveries, ChartRun(source=source, run_at=run_at, errors=errors)

    try:
        network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
        tag = network.get_tag(genre)
        tracks = tag.get_top_tracks(limit=limit)
        for rank, item in enumerate(tracks, 1):
            track = item.item
            artist_name = track.artist.name if track.artist else "Unknown"
            title = track.title
            discoveries.append(Discovery(
                source=source,
                chart_rank=rank,
                artist=artist_name,
                title=title,
                seen_at=run_at,
            ))
    except Exception as exc:
        log.exception("Last.fm genre '%s' scrape failed: %s", genre, exc)
        errors.append(str(exc))

    run = ChartRun(
        source=source,
        run_at=run_at,
        items_found=len(discoveries),
        errors=errors,
    )
    log.info("Last.fm genre '%s': %d tracks, %d errors", genre, len(discoveries), len(errors))
    return discoveries, run


def scrape_all_genres(
    genres: list[str],
    api_key: str,
    api_secret: str,
) -> tuple[list[Discovery], list[ChartRun]]:
    all_discoveries: list[Discovery] = []
    all_runs: list[ChartRun] = []
    for genre in genres:
        disc, run = scrape_genre(genre, api_key, api_secret)
        all_discoveries.extend(disc)
        all_runs.append(run)
    return all_discoveries, all_runs
