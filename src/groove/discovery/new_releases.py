"""
MusicBrainz new releases scraper.

For each artist in the watchlist, queries the MusicBrainz release-group
endpoint for releases within the past 30 days.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from groove.ssl_support import configure_default_ssl_context

configure_default_ssl_context()

import musicbrainzngs  # noqa: E402 — after SSL defaults for urllib

from groove.store import ChartRun, Discovery, WatchedArtist

log = logging.getLogger(__name__)

# MusicBrainz requires a descriptive User-Agent
musicbrainzngs.set_useragent("groove", "0.1", "https://github.com/user/groove")

# MB rate limit: 1 request/second
_MB_RATE_LIMIT = 1.1


def scrape_new_releases(
    artists: list[WatchedArtist],
    days_back: int = 30,
) -> tuple[list[Discovery], ChartRun]:
    """
    Check MusicBrainz for new release-groups by each watched artist.
    Returns discoveries for releases issued within `days_back` days.
    """
    run_at = datetime.now(UTC)
    errors: list[str] = []
    discoveries: list[Discovery] = []
    cutoff = (datetime.now(UTC) - timedelta(days=days_back)).date()

    for artist in artists:
        time.sleep(_MB_RATE_LIMIT)
        try:
            releases = _fetch_releases_for_artist(artist, cutoff)
            discoveries.extend(releases)
        except Exception as exc:
            log.warning("MB new releases failed for '%s': %s", artist.name, exc)
            errors.append(f"{artist.name}: {exc}")

    run = ChartRun(
        source="new_releases",
        run_at=run_at,
        items_found=len(discoveries),
        errors=errors,
    )
    log.info("New releases: %d discovered, %d errors", len(discoveries), len(errors))
    return discoveries, run


def _fetch_releases_for_artist(
    artist: WatchedArtist,
    cutoff,
) -> list[Discovery]:
    discoveries: list[Discovery] = []

    if artist.mb_artist_id:
        # Direct lookup by MBID
        result = musicbrainzngs.get_artist_by_id(
            artist.mb_artist_id,
            includes=["release-groups"],
            release_type=["album", "single", "ep"],
        )
        release_groups = result.get("artist", {}).get("release-group-list", [])
    else:
        # Search by name
        result = musicbrainzngs.search_artists(artist=artist.name, limit=1)
        artist_list = result.get("artist-list", [])
        if not artist_list:
            return discoveries

        time.sleep(_MB_RATE_LIMIT)
        mb_id = artist_list[0]["id"]
        result2 = musicbrainzngs.get_artist_by_id(
            mb_id,
            includes=["release-groups"],
            release_type=["album", "single", "ep"],
        )
        release_groups = result2.get("artist", {}).get("release-group-list", [])

    for rg in release_groups:
        first_release = rg.get("first-release-date", "")
        if not first_release:
            continue
        try:
            rel_date = _parse_date(first_release)
            if rel_date and rel_date >= cutoff:
                title = rg.get("title", "")
                rg_type = rg.get("type", "").lower()
                discoveries.append(Discovery(
                    source="new_release",
                    artist=artist.name,
                    album=title,
                    title=None,
                    mb_release_id=rg.get("id"),
                    seen_at=datetime.now(UTC),
                ))
        except ValueError:
            pass

    return discoveries


def search_artist_discography(
    artist_name: str,
    release_types: list[str] | None = None,
) -> list[dict]:
    """
    Return a list of release-groups (dicts) for an artist from MusicBrainz.
    Used by the discography picker in the web UI / CLI.
    """
    if release_types is None:
        release_types = ["album"]

    result = musicbrainzngs.search_artists(artist=artist_name, limit=5)
    artist_list = result.get("artist-list", [])
    if not artist_list:
        return []

    # Take the best-score match
    artist_list.sort(key=lambda a: int(a.get("ext:score", 0)), reverse=True)
    mb_id = artist_list[0]["id"]
    mb_name = artist_list[0]["name"]

    time.sleep(_MB_RATE_LIMIT)
    result2 = musicbrainzngs.get_artist_by_id(
        mb_id,
        includes=["release-groups"],
        release_type=release_types,
    )
    release_groups = result2.get("artist", {}).get("release-group-list", [])

    albums = []
    for rg in release_groups:
        rg_type = rg.get("primary-type") or rg.get("type") or ""
        if rg_type.lower() not in [t.lower() for t in release_types]:
            continue
        albums.append({
            "id": rg.get("id"),
            "title": rg.get("title"),
            "type": rg_type,
            "first_release_date": rg.get("first-release-date"),
            "artist_name": mb_name,
            "mb_artist_id": mb_id,
        })

    # Sort by date
    albums.sort(key=lambda a: a.get("first_release_date") or "", reverse=True)
    return albums


def _parse_date(date_str: str):
    """Parse MB date string (YYYY, YYYY-MM, YYYY-MM-DD) → date or None."""
    from datetime import date
    parts = date_str.split("-")
    try:
        if len(parts) >= 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
        elif len(parts) == 1 and parts[0]:
            return date(int(parts[0]), 1, 1)
    except (ValueError, IndexError):
        pass
    return None
