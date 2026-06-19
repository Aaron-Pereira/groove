"""Tests for metadata_retagger album discovery and track-number heuristics."""

from __future__ import annotations

from pathlib import Path

from groove.metadata_retagger import (
    AlbumFolder,
    album_needs_retag,
    audio_files_in_dir,
    discover_album_directories,
    has_suspicious_track_numbers,
)


def test_discover_leaf_album_directories(tmp_path: Path) -> None:
    library = tmp_path / "library"
    album = library / "Ariana Grande" / "Dangerous Woman (2016)"
    album.mkdir(parents=True)
    (album / "01 - Moonlight.mp3").write_bytes(b"x")
    (album / "02 - Dangerous Woman.mp3").write_bytes(b"x")
    # Parent artist folder must not be treated as its own album.
    (library / "Ariana Grande" / "notes.txt").write_text("x", encoding="utf-8")

    found = discover_album_directories(library)
    assert len(found) == 1
    assert found[0].artist == "Ariana Grande"
    assert found[0].album_label == "Dangerous Woman (2016)"
    assert found[0].track_count == 2


def test_has_suspicious_track_numbers_detects_playlist_index(tmp_path: Path) -> None:
    from mutagen.easyid3 import EasyID3

    album = tmp_path / "album"
    album.mkdir()
    for name in ("a.mp3", "b.mp3"):
        path = album / name
        path.write_bytes(b"")
        tags = EasyID3()
        tags["title"] = name
        tags["tracknumber"] = "63/0"
        tags.save(path)

    files = audio_files_in_dir(album)
    assert has_suspicious_track_numbers(files) is True


def test_album_needs_retag_when_mbids_missing(tmp_path: Path) -> None:
    from mutagen.easyid3 import EasyID3

    album = tmp_path / "album"
    album.mkdir()
    path = album / "song.mp3"
    path.write_bytes(b"")
    tags = EasyID3()
    tags["title"] = "Song"
    tags["tracknumber"] = "1/1"
    tags.save(path)

    folder = AlbumFolder(path=album, artist="Artist", album_label="album", track_count=1)
    assert album_needs_retag(folder) is True
