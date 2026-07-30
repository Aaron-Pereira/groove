"""Tests for auto-queue approval behavior and the year-end album source."""

import json
from datetime import UTC, datetime

import pytest

from groove.autoqueue import run_autoqueue
from groove.config import Settings
from groove.store import Discovery, Stores


@pytest.fixture
def stores(tmp_path):
    return Stores(tmp_path / "state")


def _chart_hit(source: str) -> Discovery:
    return Discovery(
        source=source,
        chart_rank=1,
        artist="Chappell Roan",
        title="Good Luck, Babe!",
        seen_at=datetime.now(UTC),
    )


def test_autoqueue_requires_approval_by_default(stores):
    """Chart hits meeting the threshold become proposals, not downloads."""
    settings = Settings()
    assert settings.auto_queue.require_approval is True

    stores.discoveries.append(_chart_hit("billboard"))
    stores.discoveries.append(_chart_hit("uk_top40"))

    added = run_autoqueue(stores, settings)

    assert added == 1
    assert stores.requests.all() == []  # nothing queued for download
    proposed = [d for d in stores.discoveries.all() if d.proposed]
    assert len(proposed) == 1
    assert not proposed[0].auto_queued


def test_autoqueue_direct_when_approval_disabled(stores):
    settings = Settings.model_validate({"auto_queue": {"require_approval": False}})

    stores.discoveries.append(_chart_hit("billboard"))
    stores.discoveries.append(_chart_hit("uk_top40"))

    added = run_autoqueue(stores, settings)

    assert added == 1
    requests = stores.requests.all()
    assert len(requests) == 1
    assert requests[0].kind == "track"
    assert requests[0].artist == "Chappell Roan"
    assert any(d.auto_queued for d in stores.discoveries.all())


def test_autoqueue_below_threshold_does_nothing(stores):
    settings = Settings()
    stores.discoveries.append(_chart_hit("billboard"))  # only 1 chart

    added = run_autoqueue(stores, settings)

    assert added == 0
    assert stores.requests.all() == []
    assert not any(d.proposed for d in stores.discoveries.all())


def test_autoqueue_does_not_repropose(stores):
    """A proposal awaiting user decision is not counted again on the next run."""
    settings = Settings()
    stores.discoveries.append(_chart_hit("billboard"))
    stores.discoveries.append(_chart_hit("uk_top40"))

    assert run_autoqueue(stores, settings) == 1
    assert run_autoqueue(stores, settings) == 0
    assert len([d for d in stores.discoveries.all() if d.proposed]) == 1


def test_merge_keeps_distinct_albums_by_same_artist(stores):
    """Album discoveries (no title) must not collide on the (artist, '') key."""
    from groove.web.routes import _merge_discoveries

    a1 = Discovery(source="billboard_200", chart_rank=1,
                   artist="Taylor Swift", album="Midnights")
    a2 = Discovery(source="billboard_200", chart_rank=2,
                   artist="Taylor Swift", album="Folklore")

    _merge_discoveries(stores, [a1, a2])

    stored = stores.discoveries.all()
    assert len(stored) == 2
    assert {d.album for d in stored} == {"Midnights", "Folklore"}
    assert all(d.appearances == 1 for d in stored)


def test_fetch_year_albums_uses_cache(tmp_path):
    """A cached year is served from disk without any network access."""
    from groove.discovery.year_end import fetch_year_albums

    cache_dir = tmp_path / "yearend"
    cache_dir.mkdir()
    cached = [{"rank": 1, "artist": "Nirvana", "album": "Nevermind",
               "source": "billboard_yearend"}]
    (cache_dir / "albums-1992.json").write_text(json.dumps(cached))

    albums = fetch_year_albums(1992, cache_dir)
    assert albums == cached
