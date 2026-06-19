"""Tests for the JSON file store."""

import tempfile
from pathlib import Path

import pytest

from groove.store import DownloadRequest, JsonStore, Stores, WatchlistStore, WatchedArtist


@pytest.fixture
def tmp_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".locks").mkdir()
    return state


def test_json_store_append_and_read(tmp_state):
    store: JsonStore[DownloadRequest] = JsonStore(tmp_state / "requests.json", DownloadRequest)
    req = DownloadRequest(raw_query="Test Artist - Test Track")
    store.append(req)
    items = store.all()
    assert len(items) == 1
    assert items[0].raw_query == "Test Artist - Test Track"
    assert items[0].status == "pending"


def test_json_store_update_one(tmp_state):
    store: JsonStore[DownloadRequest] = JsonStore(tmp_state / "requests.json", DownloadRequest)
    req = DownloadRequest(raw_query="Test")
    store.append(req)
    updated = store.update_one(req.id, {"status": "done"})
    assert updated is not None
    assert updated.status == "done"
    assert store.all()[0].status == "done"


def test_json_store_remove(tmp_state):
    store: JsonStore[DownloadRequest] = JsonStore(tmp_state / "requests.json", DownloadRequest)
    req = DownloadRequest(raw_query="Test")
    store.append(req)
    removed = store.remove(req.id)
    assert removed is True
    assert store.all() == []


def test_json_store_count_by_status(tmp_state):
    store: JsonStore[DownloadRequest] = JsonStore(tmp_state / "requests.json", DownloadRequest)
    store.append(DownloadRequest(raw_query="a", status="pending"))
    store.append(DownloadRequest(raw_query="b", status="done"))
    store.append(DownloadRequest(raw_query="c", status="done"))
    counts = store.count_by_status()
    assert counts["pending"] == 1
    assert counts["done"] == 2


def test_watchlist_store(tmp_state):
    store = WatchlistStore(tmp_state / "watchlist.json")
    artist = WatchedArtist(name="Arctic Monkeys")
    store.add_artist(artist)
    wl = store.get()
    assert len(wl.artists) == 1
    assert wl.artists[0].name == "Arctic Monkeys"

    store.update_artist("Arctic Monkeys", {"auto_download_new_albums": True})
    wl = store.get()
    assert wl.artists[0].auto_download_new_albums is True

    removed = store.remove_artist("Arctic Monkeys")
    assert removed is True
    assert store.get().artists == []


def test_stores_rotate(tmp_state):
    from datetime import date
    stores = Stores(tmp_state)
    stores.requests.append(DownloadRequest(raw_query="done song", status="done"))
    stores.requests.append(DownloadRequest(raw_query="pending song", status="pending"))
    stores.rotate(today=date(2026, 4, 23))
    # Only pending should remain
    active = stores.requests.all()
    assert len(active) == 1
    assert active[0].raw_query == "pending song"
    # Archive should exist
    archive_files = list((tmp_state / "archive").glob("requests-*.json"))
    assert len(archive_files) == 1
