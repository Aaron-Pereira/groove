"""
UK Official Singles Top 40 scraper.

Scrapes https://www.officialcharts.com/charts/singles-chart/
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from groove.store import ChartRun, Discovery

log = logging.getLogger(__name__)

CHART_URL = "https://www.officialcharts.com/charts/singles-chart/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.google.com/",
}


def scrape(client: httpx.Client | None = None) -> tuple[list[Discovery], ChartRun]:
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
        log.exception("UK Top 40 scrape failed: %s", exc)
        errors.append(str(exc))
        entries = []
    finally:
        if _owned_client and client is not None:
            client.close()

    for rank, artist, title in entries:
        discoveries.append(Discovery(
            source="uk_top40",
            chart_rank=rank,
            artist=artist,
            title=title,
            seen_at=run_at,
        ))

    run = ChartRun(
        source="uk_top40",
        run_at=run_at,
        items_found=len(discoveries),
        errors=errors,
    )
    log.info("UK Top 40: %d entries, %d errors", len(discoveries), len(errors))
    return discoveries, run


def _parse(html: str) -> list[tuple[int, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[int, str, str]] = []

    # Official Charts uses a table-like structure with chart-item divs
    chart_items = soup.select("div.chart-item")
    if not chart_items:
        chart_items = soup.select("article.chart-item")
    if not chart_items:
        chart_items = soup.select("li.chart-item")

    for item in chart_items:
        rank_el = (
            item.select_one("span.chart-item__position")
            or item.select_one(".chart-item-detail--pos")
            or item.select_one(".position")
        )
        title_el = (
            item.select_one("h2.chart-item__title")
            or item.select_one(".chart-item-title")
            or item.select_one("h2.title")
            or item.select_one("strong.title")
        )
        artist_el = (
            item.select_one("h3.chart-item__artist")
            or item.select_one(".chart-item-artist")
            or item.select_one("h3.artist")
            or item.select_one("span.artist")
        )

        if title_el and artist_el:
            try:
                rank = int(rank_el.get_text(strip=True)) if rank_el else len(results) + 1
            except ValueError:
                rank = len(results) + 1

            title = title_el.get_text(strip=True)
            artist = artist_el.get_text(strip=True)
            if title and artist:
                results.append((rank, artist, title))

    return results[:40]
