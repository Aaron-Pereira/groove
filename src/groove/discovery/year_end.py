"""
Year-end album charts — data source for the "Random Album by Year" feature.

Primary source: Billboard's Year-End Top 200 Albums chart
(https://www.billboard.com/charts/year-end/YYYY/top-billboard-200-albums/),
which covers roughly 1968 onwards with the most popular albums of each year.

Fallback: MusicBrainz release-group search (official albums first released in
the requested year) for years Billboard doesn't cover or when the scrape fails.

Results are cached on disk (state/yearend/) so each year is only fetched once.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

FIRST_BILLBOARD_YEAR = 1968

_YEAR_END_URL = "https://www.billboard.com/charts/year-end/{year}/top-billboard-200-albums/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_year_albums(year: int, cache_dir: Path) -> list[dict]:
    """
    Return the top albums for a year as a list of dicts:
      {"rank": int | None, "artist": str, "album": str, "source": str}

    Cached on disk after the first successful fetch.  Raises RuntimeError if
    no source produced any albums.
    """
    cache_file = cache_dir / f"albums-{year}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached:
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    albums: list[dict] = []
    errors: list[str] = []

    if year >= FIRST_BILLBOARD_YEAR:
        try:
            albums = _fetch_billboard_year_end(year)
        except Exception as exc:
            log.warning("Billboard year-end scrape failed for %d: %s", year, exc)
            errors.append(f"Billboard: {exc}")

    if not albums:
        try:
            albums = _fetch_musicbrainz_year(year)
        except Exception as exc:
            log.warning("MusicBrainz year lookup failed for %d: %s", year, exc)
            errors.append(f"MusicBrainz: {exc}")

    if not albums:
        raise RuntimeError(
            f"No albums found for {year}. " + "; ".join(errors) if errors else
            f"No albums found for {year}."
        )

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(albums))
    except OSError as exc:
        log.warning("Could not cache year-end albums for %d: %s", year, exc)

    return albums


def _fetch_billboard_year_end(year: int) -> list[dict]:
    """Scrape Billboard's year-end Top 200 Albums chart for a year."""
    url = _YEAR_END_URL.format(year=year)
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        resp = client.get(url)
        resp.raise_for_status()
        # Billboard silently redirects out-of-range years to a different chart
        # year — treat that as "not available" so the fallback kicks in.
        if str(year) not in str(resp.url):
            raise RuntimeError(f"Billboard has no year-end album chart for {year}")
        html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    entries = (
        soup.select("li.o-chart-results-list__item")
        or soup.select("div.o-chart-results-list-row")
    )

    albums: list[dict] = []
    rank = 1
    for item in entries:
        title_el = (
            item.select_one("h3#title-of-a-story")
            or item.select_one("h3.c-title")
            or item.select_one("h3")
        )
        artist_el = (
            item.select_one("span.c-label.a-no-trucate")
            or item.select_one("span.a-truncate-ellipsis-2line")
            or item.select_one("span.a-font-primary-s")
        )
        if title_el and artist_el:
            album = title_el.get_text(strip=True)
            artist = artist_el.get_text(strip=True)
            if album and artist:
                albums.append({
                    "rank": rank,
                    "artist": artist,
                    "album": album,
                    "source": "billboard_yearend",
                })
                rank += 1

    log.info("Billboard year-end %d: %d albums", year, len(albums))
    return albums


def _fetch_musicbrainz_year(year: int, limit: int = 100) -> list[dict]:
    """Fetch official albums first released in a year from MusicBrainz."""
    from groove.ssl_support import configure_default_ssl_context
    configure_default_ssl_context()
    import musicbrainzngs

    musicbrainzngs.set_useragent("groove", "0.1", "https://github.com/user/groove")

    result = musicbrainzngs.search_release_groups(
        query=(
            f'firstreleasedate:[{year}-01-01 TO {year}-12-31] '
            f'AND primarytype:album AND status:official'
        ),
        limit=limit,
    )

    albums: list[dict] = []
    for rg in result.get("release-group-list", []):
        title = rg.get("title")
        credits = rg.get("artist-credit") or []
        artist = ""
        for c in credits:
            if isinstance(c, dict) and c.get("artist"):
                artist = c["artist"].get("name", "")
                break
        if title and artist:
            albums.append({
                "rank": None,
                "artist": artist,
                "album": title,
                "source": "musicbrainz",
            })

    log.info("MusicBrainz %d: %d albums", year, len(albums))
    return albums


def current_max_year() -> int:
    """Latest year with a published year-end chart (previous calendar year)."""
    return datetime.now(UTC).year - 1
