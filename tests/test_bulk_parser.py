"""Tests for the bulk input parser."""

import pytest

from groove.bulk_parser import (
    _detect_format,
    _parse_plain_text,
    _parse_spotify_csv,
    dedup_entries,
    parse_input,
)

SPOTIFY_CSV = """\
Spotify URI,Track Name,Artist Name(s),Album Name,Album Artist Name(s),Disc Number,Track Number,Track Duration (ms),Added By,Added At
spotify:track:abc,Espresso,Sabrina Carpenter,Short n' Sweet,Sabrina Carpenter,1,1,173000,user,2024-08-01T00:00:00Z
spotify:track:def,APT.,ROSÉ,rosie,ROSÉ,1,2,215000,user,2024-11-01T00:00:00Z
"""

PLAIN_TEXT = """\
# this is a comment
Arctic Monkeys - AM
Radiohead - OK Computer
https://youtu.be/abc123
"""


def test_detect_spotify_csv():
    assert _detect_format(SPOTIFY_CSV, filename="liked.csv") == "spotify_csv"


def test_detect_plain_text():
    assert _detect_format(PLAIN_TEXT) == "plain_text"


def test_parse_plain_text():
    result = _parse_plain_text(PLAIN_TEXT)
    assert len(result.entries) == 3  # comment skipped, URL included
    assert result.entries[0].artist == "Arctic Monkeys"
    assert result.entries[0].album == "AM" or result.entries[0].title == "AM"
    assert result.entries[2].source_url == "https://youtu.be/abc123"


def test_parse_spotify_csv():
    result = _parse_spotify_csv(SPOTIFY_CSV)
    assert len(result.entries) == 2
    assert result.entries[0].artist == "Sabrina Carpenter"
    assert result.entries[0].title == "Espresso"
    assert result.format_detected == "spotify_csv"


def test_parse_input_auto_detect_spotify():
    result = parse_input(SPOTIFY_CSV, filename="export.csv")
    assert result.format_detected == "spotify_csv"
    assert len(result.entries) == 2


YTM_ALBUM_URL = "https://music.youtube.com/playlist?list=OLAK5uy_kNhM2yaBTOVwrcZJepB1C9P3-n5_Sfy5c"
YTM_PLAYLIST_URL = "https://music.youtube.com/playlist?list=PLabc123"
YT_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLabc123"


def test_is_ytmusic_album_url():
    from groove.bulk_parser import _is_ytmusic_album_url

    assert _is_ytmusic_album_url(YTM_ALBUM_URL)
    assert _is_ytmusic_album_url(YTM_ALBUM_URL + "&si=share_token")
    # Regular (non-album) playlists and non-music URLs are not albums
    assert not _is_ytmusic_album_url(YTM_PLAYLIST_URL)
    assert not _is_ytmusic_album_url(YT_PLAYLIST_URL)
    assert not _is_ytmusic_album_url("https://music.youtube.com/watch?v=abc")
    assert not _is_ytmusic_album_url("Arctic Monkeys - AM")


def test_ytm_album_url_parses_as_single_album_entry(monkeypatch):
    """An album share URL must become ONE kind=album entry, never expanded
    into per-track requests (which import as singletons under Non-Album/)."""
    import groove.bulk_parser as bp

    class FakeYTMusic:
        def get_playlist(self, playlist_id, limit=None):
            assert playlist_id.startswith("OLAK5uy_")
            return {
                "title": "Random Access Memories",
                "author": {"name": "Daft Punk"},
                "year": 2013,
                "tracks": [{"artists": [{"name": "Daft Punk"}],
                            "album": {"name": "Random Access Memories", "id": "MPREb_x"}}],
            }

    import sys, types
    fake_mod = types.SimpleNamespace(YTMusic=FakeYTMusic)
    monkeypatch.setitem(sys.modules, "ytmusicapi", fake_mod)

    result = bp.parse_input(YTM_ALBUM_URL)
    assert result.format_detected == "youtube_playlist"
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.kind == "album"
    assert e.artist == "Daft Punk"
    assert e.album == "Random Access Memories"
    assert e.year == "2013"
    assert e.source_url == YTM_ALBUM_URL


def test_ytm_album_url_still_queued_when_metadata_lookup_fails(monkeypatch):
    """If ytmusicapi is unavailable, the album entry is queued by URL alone —
    the downloader refetches metadata itself."""
    import groove.bulk_parser as bp
    import sys, types

    class BrokenYTMusic:
        def __init__(self):
            raise RuntimeError("no network")

    monkeypatch.setitem(sys.modules, "ytmusicapi", types.SimpleNamespace(YTMusic=BrokenYTMusic))

    result = bp.parse_input(YTM_ALBUM_URL)
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.kind == "album"
    assert e.source_url == YTM_ALBUM_URL
    assert result.errors  # lookup failure surfaced as a parse warning


def test_plain_text_ytm_album_url_gets_album_kind():
    result = _parse_plain_text(f"Arctic Monkeys - AM\n{YTM_ALBUM_URL}\n")
    assert result.entries[1].kind == "album"
    assert result.entries[1].source_url == YTM_ALBUM_URL


def test_dedup_entries():
    from groove.bulk_parser import ParsedEntry
    entries = [
        ParsedEntry(raw_query="a - b", artist="a", title="b"),
        ParsedEntry(raw_query="c - d", artist="c", title="d"),
    ]
    # "a b" is already in library
    existing = {"a b"}
    to_queue, in_lib, pending = dedup_entries(entries, existing_queries=existing)
    assert len(to_queue) == 1
    assert to_queue[0].artist == "c"
    assert len(in_lib) == 1
