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
