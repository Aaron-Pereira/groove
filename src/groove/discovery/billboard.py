"""
Billboard Hot 100 scraper.

Scrapes https://www.billboard.com/charts/hot-100/ using httpx + BeautifulSoup.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from groove.store import ChartRun, Discovery

log = logging.getLogger(__name__)

CHART_URL = "https://www.billboard.com/charts/hot-100/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape(client: httpx.Client | None = None) -> tuple[list[Discovery], ChartRun]:
    """
    Scrape the Billboard Hot 100.

    Returns (discoveries, chart_run). The caller is responsible for
    persisting these to the store.
    """
    run_at = datetime.now(UTC)
    errors: list[str] = []
    discoveries: list[Discovery] = []

    _owned_client = client is None
    try:
        if _owned_client:
            client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)
        resp = client.get(CHART_URL)
        resp.raise_for_status()
        entries = _parse(resp.text)
    except Exception as exc:
        log.exception("Billboard scrape failed: %s", exc)
        errors.append(str(exc))
        entries = []
    finally:
        if _owned_client and client is not None:
            client.close()

    for rank, artist, title in entries:
        discoveries.append(Discovery(
            source="billboard",
            chart_rank=rank,
            artist=artist,
            title=title,
            seen_at=run_at,
        ))

    run = ChartRun(
        source="billboard",
        run_at=run_at,
        items_found=len(discoveries),
        errors=errors,
    )
    log.info("Billboard: %d entries, %d errors", len(discoveries), len(errors))
    return discoveries, run


def _parse(html: str) -> list[tuple[int, str, str]]:
    """Return list of (rank, artist, title) tuples."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[int, str, str]] = []

    # Billboard uses a React-hydrated page; entries are in <li> elements with
    # data attributes or in structured divs. Try multiple selectors for resilience.
    entries = soup.select("li.o-chart-results-list__item")
    if not entries:
        # Fallback: look for JSON-LD or structured data
        entries = soup.select("div.o-chart-results-list-row")

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
        rank_el = item.select_one("span.c-label.a-font-primary-bold-l")

        if title_el and artist_el:
            try:
                actual_rank = int(rank_el.get_text(strip=True)) if rank_el else rank
            except (ValueError, AttributeError):
                actual_rank = rank

            title = title_el.get_text(strip=True)
            artist = artist_el.get_text(strip=True)
            if title and artist:
                results.append((actual_rank, artist, title))
            rank += 1

    if not results:
        # Last-resort: look for JSON-LD embedded in the page
        results = _parse_jsonld(soup)

    return results[:100]  # cap at 100


def _parse_jsonld(soup: BeautifulSoup) -> list[tuple[int, str, str]]:
    import json
    results = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else data.get("itemListElement", [])
            for i, item in enumerate(items, 1):
                name = item.get("name") or item.get("item", {}).get("name", "")
                creator = item.get("item", {}).get("byArtist", {}).get("name", "")
                if name:
                    results.append((i, creator or "Unknown", name))
        except (json.JSONDecodeError, AttributeError):
            pass
    return results
