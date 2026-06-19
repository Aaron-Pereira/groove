"""Tiered audio downloader with backend fallback chain.

Download backend priority (first success wins):

  Tier 1 — Spotify → YouTube Music
    Uses the Spotify API (spotipy) to get exact track/album metadata including
    durations, then ytmusicapi to find the matching YouTube Music video by
    artist + title + duration (±10 s tolerance).  Best accuracy for mainstream
    music.  Requires ``spotify_client_id`` and ``spotify_client_secret`` in
    groove.toml → [api_keys].

  Tier 2 — YouTube Music search
    Uses ytmusicapi to search music.youtube.com directly.  For albums it fetches
    the full album page and downloads every track in order.  No credentials
    needed.  Much better results than regular YouTube because the index is
    music-specific.

  Tier 3 — Regular YouTube search (last resort)
    The original yt-dlp ``ytsearch15:`` approach with per-result fallback.
    Kept as a safety net for anything not found on YouTube Music.

Special-cased inputs (bypass all search):
  - Any YouTube URL  → yt-dlp directly
  - Any Spotify URL  → Tier 1 directly (fails loudly if Spotify not configured)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from groove.config import Settings

log = logging.getLogger(__name__)

# How many regular-YouTube search results to try before giving up
_YT_SEARCH_POOL = 15

# Duration tolerance (seconds) when matching a Spotify track to YouTube Music
_DURATION_TOLERANCE_S = 10


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class DownloadResult:
    def __init__(
        self,
        *,
        success: bool,
        dest_dir: Path,
        files: list[Path],
        error: str | None = None,
        backend: str | None = None,
    ):
        self.success = success
        self.dest_dir = dest_dir
        self.files = files
        self.error = error
        self.backend = backend  # e.g. "spotify_ytmusic", "ytmusic", "youtube"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def download(
    query_or_url: str,
    settings: Settings,
    *,
    request_id: str | None = None,
    kind: str = "track",        # "track" | "album" | "playlist"
    artist: str | None = None,
    album: str | None = None,
    title: str | None = None,
    is_playlist: bool = False,
) -> DownloadResult:
    """
    Download audio using the best available backend.

    Tries Tier 1 → 2 → 3 in order, returning on the first success.
    Direct YouTube and Spotify URLs bypass the search tier selection.
    """
    dest_dir = settings.inbox_downloads_dir / (request_id or str(uuid.uuid4()))
    dest_dir.mkdir(parents=True, exist_ok=True)

    codec = settings.audio.codec
    bitrate = settings.audio.bitrate

    # --- Direct URL shortcuts ---

    if _is_ytmusic_album_playlist_url(query_or_url):
        log.info("YouTube Music album playlist detected — using ytmusicapi for metadata")
        result = _download_ytmusic_playlist_url(query_or_url, dest_dir, codec, bitrate)
        if result is not None:
            return result
        log.info("ytmusicapi playlist download failed — falling back to direct yt-dlp")

    if _is_youtube_url(query_or_url):
        log.info("Direct YouTube URL detected — downloading with yt-dlp")
        return _run_ytdlp(
            query_or_url, dest_dir, codec, bitrate,
            is_playlist=is_playlist, backend="youtube_direct",
        )

    if _is_spotify_url(query_or_url):
        log.info("Spotify URL detected — attempting Tier 1 (Spotify→YouTube Music)")
        result = _try_spotify_ytmusic(
            query_or_url, dest_dir, settings,
            kind=kind, artist=artist, album=album, title=title,
        )
        if result is not None:
            return result
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=(
                "Spotify URL supplied but Spotify API keys are not configured. "
                "Add spotify_client_id and spotify_client_secret to groove.toml → [api_keys]."
            ),
        )

    # --- Text / unknown URL: try tiers in order ---

    # Build the cleanest query we can for each backend
    text_query = _build_text_query(query_or_url, kind=kind, artist=artist, album=album, title=title)

    # Tier 1 — Spotify → YouTube Music
    result = _try_spotify_ytmusic(
        text_query, dest_dir, settings,
        kind=kind, artist=artist, album=album, title=title,
    )
    if result is not None:
        if result.success:
            return result
        log.info("Tier 1 (Spotify→YTMusic) failed: %s — trying Tier 2", result.error)
        _clear_staging_dir(dest_dir)

    # Tier 2 — YouTube Music search
    result = _try_ytmusic(
        text_query, dest_dir, settings,
        kind=kind, artist=artist, album=album,
    )
    if result.success:
        return result
    log.info("Tier 2 (YouTube Music) failed: %s — falling back to Tier 3", result.error)
    _clear_staging_dir(dest_dir)

    # Tier 3 — Regular YouTube search (last resort)
    log.info("Tier 3 (YouTube search): %r", text_query)
    return _try_youtube_search(text_query, dest_dir, codec, bitrate)


# ---------------------------------------------------------------------------
# Tier 1 — Spotify metadata → YouTube Music audio
# ---------------------------------------------------------------------------


def _try_spotify_ytmusic(
    query_or_url: str,
    dest_dir: Path,
    settings: Settings,
    *,
    kind: str,
    artist: str | None,
    album: str | None,
    title: str | None,
) -> DownloadResult | None:
    """
    Use Spotify for precise metadata then find on YouTube Music by
    artist + title + duration (±10 s).

    Returns None if Spotify credentials are not configured.
    Returns a DownloadResult (success or failure) if credentials exist.
    """
    client_id = settings.api_keys.spotify_client_id
    client_secret = settings.api_keys.spotify_client_secret
    if not (client_id and client_secret):
        return None

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ImportError:
        log.warning("spotipy is not installed — Tier 1 unavailable")
        return None

    try:
        from ytmusicapi import YTMusic
        ytmusic = YTMusic()
    except ImportError:
        log.warning("ytmusicapi is not installed — Tier 1 unavailable")
        return None

    try:
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
        )
    except Exception as exc:
        log.warning("Spotify auth failed: %s", exc)
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"Spotify auth failed: {exc}",
        )

    codec = settings.audio.codec
    bitrate = settings.audio.bitrate

    if _is_spotify_url(query_or_url):
        # Route by URL type
        if "/album/" in query_or_url:
            return _spotify_album_to_ytmusic(
                sp, ytmusic, query_or_url, dest_dir, codec, bitrate,
            )
        elif "/track/" in query_or_url:
            return _spotify_track_to_ytmusic(
                sp, ytmusic, query_or_url, dest_dir, codec, bitrate,
            )
        elif "/playlist/" in query_or_url:
            return _spotify_playlist_to_ytmusic(
                sp, ytmusic, query_or_url, dest_dir, codec, bitrate,
            )
        # Unrecognised Spotify URL type — fall through to text search
        log.warning("Unrecognised Spotify URL type: %s", query_or_url)

    # Text query — search Spotify first to get canonical metadata
    if kind == "album":
        search_q = (
            f"artist:{artist} album:{album}" if (artist and album)
            else query_or_url
        )
        try:
            sp_results = sp.search(q=search_q, type="album", limit=1)
            items = sp_results.get("albums", {}).get("items", [])
        except Exception as exc:
            return DownloadResult(
                success=False, dest_dir=dest_dir, files=[],
                error=f"Spotify album search failed: {exc}",
            )
        if not items:
            return DownloadResult(
                success=False, dest_dir=dest_dir, files=[],
                error=f"Spotify: no album results for '{search_q}'",
            )
        spotify_album_url = items[0]["external_urls"]["spotify"]
        return _spotify_album_to_ytmusic(
            sp, ytmusic, spotify_album_url, dest_dir, codec, bitrate,
        )
    else:
        # Track / single
        search_q = (
            f"artist:{artist} track:{title or album}" if artist and (title or album)
            else query_or_url
        )
        try:
            sp_results = sp.search(q=search_q, type="track", limit=1)
            items = sp_results.get("tracks", {}).get("items", [])
        except Exception as exc:
            return DownloadResult(
                success=False, dest_dir=dest_dir, files=[],
                error=f"Spotify track search failed: {exc}",
            )
        if not items:
            return DownloadResult(
                success=False, dest_dir=dest_dir, files=[],
                error=f"Spotify: no track results for '{search_q}'",
            )
        spotify_track_url = items[0]["external_urls"]["spotify"]
        return _spotify_track_to_ytmusic(
            sp, ytmusic, spotify_track_url, dest_dir, codec, bitrate,
        )


def _spotify_album_to_ytmusic(
    sp: Any,
    ytmusic: Any,
    album_url: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Download every track of a Spotify album via YouTube Music."""
    try:
        album_id = _spotify_id_from_url(album_url)
        sp_album = sp.album(album_id)
        sp_tracks = sp_album.get("tracks", {}).get("items", [])
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"Spotify album fetch failed: {exc}",
        )

    if not sp_tracks:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="Spotify album returned no tracks",
        )

    album_name = sp_album.get("name", "")
    artist_name = (sp_album.get("artists") or [{}])[0].get("name", "")
    log.info(
        "Spotify→YTMusic: downloading album '%s - %s' (%d tracks)",
        artist_name, album_name, len(sp_tracks),
    )

    downloaded: list[Path] = []
    failed: list[str] = []

    for sp_track in sp_tracks:
        track_title = sp_track.get("name", "")
        track_artists = ", ".join(a["name"] for a in sp_track.get("artists", []))
        duration_ms = sp_track.get("duration_ms")
        duration_s = int(duration_ms / 1000) if duration_ms else None

        video_id = _ytmusic_find_video(
            ytmusic, track_artists or artist_name, track_title, duration_s,
        )
        if not video_id:
            log.warning("YTMusic: no match for '%s - %s'", track_artists, track_title)
            failed.append(track_title)
            continue

        url = f"https://music.youtube.com/watch?v={video_id}"
        res = _run_ytdlp(
            url, dest_dir, codec, bitrate, is_playlist=False, backend="spotify_ytmusic",
        )
        if res.success:
            downloaded.extend(res.files)
        else:
            log.warning("Download failed for '%s': %s", track_title, res.error)
            failed.append(track_title)

    if downloaded:
        if failed:
            log.warning("Album download partial — failed tracks: %s", failed)
        return DownloadResult(
            success=True, dest_dir=dest_dir, files=downloaded, backend="spotify_ytmusic",
        )
    return DownloadResult(
        success=False, dest_dir=dest_dir, files=[],
        error=f"All {len(sp_tracks)} tracks failed. Examples: {failed[:3]}",
    )


def _spotify_track_to_ytmusic(
    sp: Any,
    ytmusic: Any,
    track_url: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Download a single Spotify track via YouTube Music."""
    try:
        track_id = _spotify_id_from_url(track_url)
        sp_track = sp.track(track_id)
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"Spotify track fetch failed: {exc}",
        )

    track_title = sp_track.get("name", "")
    artist_name = ", ".join(a["name"] for a in sp_track.get("artists", []))
    duration_ms = sp_track.get("duration_ms")
    duration_s = int(duration_ms / 1000) if duration_ms else None

    log.info("Spotify→YTMusic: '%s - %s' (%ss)", artist_name, track_title, duration_s)

    video_id = _ytmusic_find_video(ytmusic, artist_name, track_title, duration_s)
    if not video_id:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YouTube Music: no match for '{artist_name} - {track_title}'",
        )

    url = f"https://music.youtube.com/watch?v={video_id}"
    result = _run_ytdlp(url, dest_dir, codec, bitrate, is_playlist=False, backend="spotify_ytmusic")
    return result


def _spotify_playlist_to_ytmusic(
    sp: Any,
    ytmusic: Any,
    playlist_url: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Download all tracks of a Spotify playlist via YouTube Music."""
    try:
        playlist_id = _spotify_id_from_url(playlist_url)
        sp_playlist = sp.playlist(playlist_id)
        raw_items = sp_playlist.get("tracks", {}).get("items", [])
        sp_tracks = [item["track"] for item in raw_items if item.get("track")]
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"Spotify playlist fetch failed: {exc}",
        )

    if not sp_tracks:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="Spotify playlist returned no tracks",
        )

    log.info("Spotify→YTMusic: playlist '%s' (%d tracks)", playlist_url, len(sp_tracks))

    downloaded: list[Path] = []
    failed: list[str] = []

    for sp_track in sp_tracks:
        if not sp_track:
            continue
        track_title = sp_track.get("name", "")
        artist_name = ", ".join(a["name"] for a in sp_track.get("artists", []))
        duration_ms = sp_track.get("duration_ms")
        duration_s = int(duration_ms / 1000) if duration_ms else None

        video_id = _ytmusic_find_video(ytmusic, artist_name, track_title, duration_s)
        if not video_id:
            failed.append(track_title)
            continue

        url = f"https://music.youtube.com/watch?v={video_id}"
        res = _run_ytdlp(
            url, dest_dir, codec, bitrate, is_playlist=False, backend="spotify_ytmusic",
        )
        if res.success:
            downloaded.extend(res.files)
        else:
            failed.append(track_title)

    if downloaded:
        return DownloadResult(
            success=True, dest_dir=dest_dir, files=downloaded, backend="spotify_ytmusic",
        )
    return DownloadResult(
        success=False, dest_dir=dest_dir, files=[],
        error=f"All playlist tracks failed. Examples: {failed[:3]}",
    )


def _ytmusic_find_video(
    ytmusic: Any,
    artist: str,
    title: str,
    duration_s: int | None,
) -> str | None:
    """
    Search YouTube Music for a song and return the best-matching videoId.

    Prefers results whose duration is within ±10 s of the Spotify duration.
    Falls back to the first result if no duration info is available.
    """
    query = f"{artist} {title}".strip()
    try:
        results = ytmusic.search(query, filter="songs", limit=10)
    except Exception as exc:
        log.warning("ytmusicapi search failed for '%s': %s", query, exc)
        return None

    if not results:
        return None

    best_id: str | None = None
    for result in results:
        vid = result.get("videoId")
        if not vid:
            continue
        yt_dur = result.get("duration_seconds")
        if duration_s and yt_dur and abs(yt_dur - duration_s) <= _DURATION_TOLERANCE_S:
            return vid  # strong duration match — take it immediately
        if best_id is None:
            best_id = vid  # fallback: first result with a videoId

    return best_id


# ---------------------------------------------------------------------------
# YouTube Music playlist URL handler
# ---------------------------------------------------------------------------


def _is_ytmusic_album_playlist_url(s: str) -> bool:
    """Detect YouTube Music album playlist URLs (OLAK5uy_ prefix)."""
    return (
        _is_url(s)
        and "music.youtube.com" in s
        and "playlist" in s
        and "OLAK5uy_" in s
    )


def _download_ytmusic_playlist_url(
    url: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult | None:
    """Download a YouTube Music album playlist with correct metadata from ytmusicapi.

    Uses ytmusicapi to get the album track listing (with correct track numbers),
    then downloads each track individually and tags it with the YouTube Music metadata.
    Returns None if ytmusicapi is unavailable or the playlist can't be parsed.
    """
    try:
        from ytmusicapi import YTMusic
        ytmusic = YTMusic()
    except (ImportError, Exception) as exc:
        log.warning("ytmusicapi unavailable for playlist download: %s", exc)
        return None

    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    playlist_id = (params.get("list") or [None])[0]
    if not playlist_id:
        log.warning("Could not extract playlist ID from %s", url)
        return None

    try:
        playlist_data = ytmusic.get_playlist(playlist_id, limit=None)
    except Exception as exc:
        log.warning("ytmusicapi get_playlist failed: %s", exc)
        return None

    tracks = playlist_data.get("tracks") or []
    if not tracks:
        return None

    album_name = playlist_data.get("title") or ""
    album_artist = ""
    if playlist_data.get("author"):
        album_artist = playlist_data["author"].get("name", "")

    # Fetch album data to get correct track numbers
    album_track_numbers: dict[str, int] = {}
    seen_browse_ids: set[str] = set()
    for track in tracks:
        album_info = track.get("album")
        if album_info and album_info.get("id") and album_info["id"] not in seen_browse_ids:
            seen_browse_ids.add(album_info["id"])
            try:
                album_data = ytmusic.get_album(album_info["id"])
                for album_track in album_data.get("tracks") or []:
                    vid = album_track.get("videoId")
                    raw_num = album_track.get("trackNumber")
                    if vid and raw_num is not None:
                        album_track_numbers[vid] = int(raw_num)
                if not album_name and album_data.get("title"):
                    album_name = album_data["title"]
                if not album_artist:
                    artists = album_data.get("artists") or []
                    if artists:
                        album_artist = artists[0].get("name", "")
            except Exception as exc:
                log.debug("get_album failed for browse_id %s: %s", album_info["id"], exc)

    log.info(
        "YouTube Music album: '%s - %s' (%d tracks)",
        album_artist, album_name, len(tracks),
    )

    downloaded: list[Path] = []
    track_metadata: list[dict[str, Any]] = []
    failed: list[str] = []

    for idx, track in enumerate(tracks):
        video_id = track.get("videoId")
        if not video_id:
            continue

        title = track.get("title") or ""
        artists = track.get("artists") or []
        artist = ", ".join(a["name"] for a in artists if a.get("name")) if artists else album_artist

        track_number = album_track_numbers.get(video_id, idx + 1)

        track_url = f"https://music.youtube.com/watch?v={video_id}"
        res = _run_ytdlp(
            track_url, dest_dir, codec, bitrate,
            is_playlist=False, backend="ytmusic_playlist",
        )
        if res.success:
            downloaded.extend(res.files)
            for fp in res.files:
                track_metadata.append({
                    "file": fp,
                    "artist": artist,
                    "title": title,
                    "album": album_name,
                    "track_number": track_number,
                })
        else:
            log.warning("Download failed for '%s': %s", title, res.error)
            failed.append(title)

    # Tag all downloaded files with correct YouTube Music metadata
    for meta in track_metadata:
        try:
            _tag_one_file(
                meta["file"],
                artist=meta["artist"],
                title=meta["title"],
                album=meta["album"],
                track_number=meta["track_number"],
            )
        except Exception as exc:
            log.warning("Failed to tag %s: %s", meta["file"], exc)

    if downloaded:
        if failed:
            log.warning("Album download partial — failed tracks: %s", failed)
        return DownloadResult(
            success=True, dest_dir=dest_dir, files=downloaded, backend="ytmusic_playlist",
        )

    return DownloadResult(
        success=False, dest_dir=dest_dir, files=[],
        error=f"All {len(tracks)} tracks failed. Examples: {failed[:3]}",
    )


# ---------------------------------------------------------------------------
# Tier 2 — YouTube Music search (ytmusicapi)
# ---------------------------------------------------------------------------


def _try_ytmusic(
    query: str,
    dest_dir: Path,
    settings: Settings,
    *,
    kind: str,
    artist: str | None,
    album: str | None,
) -> DownloadResult:
    """
    Search music.youtube.com via ytmusicapi and download the result(s).

    For albums: fetches the full album page and downloads every track.
    For tracks: downloads the best song match.
    """
    try:
        from ytmusicapi import YTMusic
        ytmusic = YTMusic()
    except ImportError:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="ytmusicapi not installed",
        )
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YTMusic init failed: {exc}",
        )

    codec = settings.audio.codec
    bitrate = settings.audio.bitrate

    if kind == "album":
        return _ytmusic_album(ytmusic, query, artist, album, dest_dir, codec, bitrate)
    else:
        return _ytmusic_track(ytmusic, query, dest_dir, codec, bitrate)


def _ytmusic_album(
    ytmusic: Any,
    query: str,
    artist: str | None,
    album: str | None,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Search YouTube Music for an album and download all tracks."""
    search_q = query
    if artist and album:
        search_q = f"{artist} {album}"

    try:
        results = ytmusic.search(search_q, filter="albums", limit=5)
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YouTube Music album search failed: {exc}",
        )

    if not results:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YouTube Music: no album results for '{search_q}'",
        )

    # Try each album result until we get one with downloadable tracks
    for album_result in results:
        browse_id = album_result.get("browseId")
        if not browse_id:
            continue

        try:
            album_data = ytmusic.get_album(browse_id)
        except Exception as exc:
            log.warning("YTMusic get_album failed for %s: %s", browse_id, exc)
            continue

        tracks = album_data.get("tracks", [])
        if not tracks:
            continue

        album_display = album_result.get("title", search_q)
        log.info("YouTube Music: downloading album '%s' (%d tracks)", album_display, len(tracks))

        downloaded: list[Path] = []
        failed: list[str] = []

        for track in tracks:
            video_id = track.get("videoId")
            if not video_id:
                continue
            url = f"https://music.youtube.com/watch?v={video_id}"
            res = _run_ytdlp(url, dest_dir, codec, bitrate, is_playlist=False, backend="ytmusic")
            if res.success:
                downloaded.extend(res.files)
            else:
                failed.append(track.get("title", video_id))
                log.warning("Track download failed: %s — %s", track.get("title"), res.error)

        if downloaded:
            return DownloadResult(
                success=True, dest_dir=dest_dir, files=downloaded, backend="ytmusic",
            )

    return DownloadResult(
        success=False, dest_dir=dest_dir, files=[],
        error=f"YouTube Music: could not download any tracks for album '{search_q}'",
    )


def _ytmusic_track(
    ytmusic: Any,
    query: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Search YouTube Music for a song and download the best match."""
    try:
        results = ytmusic.search(query, filter="songs", limit=5)
    except Exception as exc:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YouTube Music song search failed: {exc}",
        )

    if not results:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"YouTube Music: no song results for '{query}'",
        )

    last_err = "No playable result"
    for result in results:
        video_id = result.get("videoId")
        if not video_id:
            continue
        url = f"https://music.youtube.com/watch?v={video_id}"
        res = _run_ytdlp(url, dest_dir, codec, bitrate, is_playlist=False, backend="ytmusic")
        if res.success:
            return res
        last_err = res.error or last_err

    return DownloadResult(success=False, dest_dir=dest_dir, files=[], error=last_err)


# ---------------------------------------------------------------------------
# Tier 3 — Regular YouTube search (last resort)
# ---------------------------------------------------------------------------


def _try_youtube_search(
    query: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
) -> DownloadResult:
    """Try several YouTube search results until one downloads."""
    entries = _ytsearch_flat_entries(query, limit=_YT_SEARCH_POOL)
    if not entries:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=f"No YouTube search results for: {query[:120]}",
        )

    last_err = "No playable search result"
    for i, entry in enumerate(entries, start=1):
        if i > 1:
            _clear_staging_dir(dest_dir)
        vid = entry.get("id") or ""
        if not vid:
            continue
        url = (
            entry.get("url")
            or entry.get("webpage_url")
            or f"https://www.youtube.com/watch?v={vid}"
        )
        if not _is_url(url):
            url = f"https://www.youtube.com/watch?v={vid}"
        log.info(
            "YouTube search try %d/%d: %s (%s)",
            i, len(entries), url, entry.get("title", "")[:60],
        )
        res = _run_ytdlp(url, dest_dir, codec, bitrate, is_playlist=False, backend="youtube")
        if res.success:
            return res
        last_err = res.error or last_err

    return DownloadResult(success=False, dest_dir=dest_dir, files=[], error=last_err)


def _ytsearch_flat_entries(search_term: str, *, limit: int) -> list[dict[str, Any]]:
    """Return flat yt-dlp JSON dicts for the first `limit` YouTube search hits."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        f"ytsearch{limit}:{search_term}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("yt-dlp search listing failed: %s", exc)
        return []

    entries: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ---------------------------------------------------------------------------
# yt-dlp core runner
# ---------------------------------------------------------------------------


def _run_ytdlp(
    source: str,
    dest_dir: Path,
    codec: str,
    bitrate: str,
    *,
    is_playlist: bool = False,
    backend: str = "youtube",
) -> DownloadResult:
    """Run a single yt-dlp extract-audio invocation."""
    output_template = str(dest_dir / "%(title)s.%(ext)s")
    cmd = [
        _find_ytdlp(),
        "--extract-audio",
        "--audio-format", codec,
        "--audio-quality", f"{bitrate}K",
        "--embed-thumbnail",
        "--add-metadata",
        "--output", output_template,
        "--no-playlist" if not is_playlist else "--yes-playlist",
        "--socket-timeout", "30",
        "--retries", "3",
        source,
    ]

    log.info("yt-dlp: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="Download timed out after 10 minutes",
        )
    except FileNotFoundError:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="yt-dlp not found; install it with: brew install yt-dlp",
        )

    if result.returncode != 0:
        log.error("yt-dlp stderr: %s", result.stderr[:2000])
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error=_clean_error(result.stderr),
        )

    files = list(dest_dir.glob(f"*.{codec}"))
    if not files:
        files = [
            p for p in dest_dir.iterdir()
            if p.is_file() and p.suffix.lstrip(".") in ("mp3", "m4a", "ogg", "opus", "flac")
        ]

    if not files:
        return DownloadResult(
            success=False, dest_dir=dest_dir, files=[],
            error="yt-dlp exited successfully but no audio file was produced",
        )

    log.info("Downloaded %d file(s) to %s via %s", len(files), dest_dir, backend)
    return DownloadResult(success=True, dest_dir=dest_dir, files=files, backend=backend)


# ---------------------------------------------------------------------------
# Post-download metadata tagging
# ---------------------------------------------------------------------------


def strip_track_numbers(files: list[Path]) -> int:
    """Remove track number metadata from audio files.

    Called before beets import to prevent yt-dlp's embedded playlist indices
    from being treated as real track numbers during as-is fallback imports.
    Returns the number of files processed.
    """
    try:
        import importlib.util
        if importlib.util.find_spec("mutagen") is None:
            return 0
    except ImportError:
        return 0

    stripped = 0
    for fp in files:
        try:
            stripped += _strip_track_one_file(fp)
        except Exception as exc:
            log.debug("Failed to strip track number from %s: %s", fp, exc)
    return stripped


def _strip_track_one_file(fp: Path) -> int:
    """Strip track number from a single audio file. Returns 1 on success."""
    import mutagen
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    ext = fp.suffix.lower()

    if ext == ".mp3":
        try:
            tags = EasyID3(fp)
        except ID3NoHeaderError:
            return 0
        if "tracknumber" in tags:
            del tags["tracknumber"]
            tags.save()
            return 1
        return 0

    if ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4
        audio = MP4(fp)
        if audio.tags and "trkn" in audio.tags:
            del audio.tags["trkn"]
            audio.save()
            return 1
        return 0

    if ext in (".ogg", ".opus"):
        audio = mutagen.File(fp)
        if audio and "tracknumber" in audio:
            del audio["tracknumber"]
            audio.save()
            return 1
        return 0

    if ext == ".flac":
        from mutagen.flac import FLAC
        audio = FLAC(fp)
        if "tracknumber" in audio:
            del audio["tracknumber"]
            audio.save()
            return 1
        return 0

    return 0


def tag_downloaded_files(
    files: list[Path],
    *,
    artist: str | None = None,
    title: str | None = None,
    album: str | None = None,
    track_number: int | None = None,
) -> int:
    """Write metadata tags to downloaded audio files using mutagen.

    Only overwrites fields for which a non-None value is supplied.
    Returns the number of files successfully tagged.
    """
    if not any((artist, title, album, track_number is not None)):
        return 0

    try:
        import importlib.util
        if importlib.util.find_spec("mutagen") is None:
            raise ImportError("mutagen")
    except ImportError:
        log.warning("mutagen not installed — skipping metadata tagging")
        return 0

    tagged = 0
    for fp in files:
        try:
            tagged += _tag_one_file(
                fp, artist=artist, title=title, album=album,
                track_number=track_number,
            )
        except Exception as exc:
            log.warning("Failed to tag %s: %s", fp, exc)
    return tagged


def _tag_one_file(
    fp: Path,
    *,
    artist: str | None,
    title: str | None,
    album: str | None,
    track_number: int | None,
) -> int:
    """Tag a single audio file. Returns 1 on success, 0 on skip/error."""
    import mutagen
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    ext = fp.suffix.lower()

    if ext == ".mp3":
        try:
            tags = EasyID3(fp)
        except ID3NoHeaderError:
            audio = mutagen.File(fp, easy=True)
            if audio is None:
                return 0
            audio.add_tags()
            tags = audio
        if artist:
            tags["artist"] = artist
        if title:
            tags["title"] = title
        if album:
            tags["album"] = album
        if track_number is not None:
            tags["tracknumber"] = str(track_number)
        tags.save()
        return 1

    if ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4
        audio = MP4(fp)
        if audio.tags is None:
            audio.add_tags()
        if artist:
            audio.tags["\xa9ART"] = [artist]
        if title:
            audio.tags["\xa9nam"] = [title]
        if album:
            audio.tags["\xa9alb"] = [album]
        if track_number is not None:
            audio.tags["trkn"] = [(track_number, 0)]
        audio.save()
        return 1

    if ext in (".ogg", ".opus"):
        audio = mutagen.File(fp)
        if audio is None:
            return 0
        if artist:
            audio["artist"] = [artist]
        if title:
            audio["title"] = [title]
        if album:
            audio["album"] = [album]
        if track_number is not None:
            audio["tracknumber"] = [str(track_number)]
        audio.save()
        return 1

    if ext == ".flac":
        from mutagen.flac import FLAC
        audio = FLAC(fp)
        if artist:
            audio["artist"] = [artist]
        if title:
            audio["title"] = [title]
        if album:
            audio["album"] = [album]
        if track_number is not None:
            audio["tracknumber"] = [str(track_number)]
        audio.save()
        return 1

    log.debug("Unsupported format for tagging: %s", ext)
    return 0


# ---------------------------------------------------------------------------
# Playlist utilities (used by bulk parser / routes)
# ---------------------------------------------------------------------------


def fetch_playlist_entries(url: str) -> list[dict[str, Any]]:
    """
    Return a list of {title, uploader, url, duration} dicts for a YouTube
    playlist without downloading audio.
    """
    cmd = [
        _find_ytdlp(),
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise RuntimeError(f"yt-dlp failed: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp exited with code {result.returncode}: "
            f"{result.stderr.strip()[:300] or 'no error output'}"
        )

    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            entries.append({
                "title": data.get("title", ""),
                "uploader": data.get("uploader", data.get("channel", "")),
                "url": data.get("url") or data.get("webpage_url", ""),
                "duration": data.get("duration"),
            })
        except json.JSONDecodeError:
            pass
    return entries


def search_youtube(query: str) -> str | None:
    """
    Run ytsearch1 and return the resolved video URL, or None on failure.
    Does NOT download audio.
    """
    cmd = [
        _find_ytdlp(),
        "--get-url",
        "--no-playlist",
        "--no-warnings",
        f"ytsearch1:{query}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            url = result.stdout.strip()
            return url if url else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_text_query(
    raw: str,
    *,
    kind: str,
    artist: str | None,
    album: str | None,
    title: str | None,
) -> str:
    """Build the clearest possible search string from available metadata."""
    if artist and album and kind == "album":
        return f"{artist} {album}"
    if artist and title:
        return f"{artist} {title}"
    if artist and album:
        return f"{artist} {album}"
    return raw


def _spotify_id_from_url(url: str) -> str:
    """Extract the Spotify ID from a spotify.com URL."""
    # e.g. https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo?si=...
    parts = url.split("?")[0].rstrip("/").split("/")
    return parts[-1]


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))


def _is_youtube_url(s: str) -> bool:
    return _is_url(s) and (
        "youtube.com" in s
        or "youtu.be" in s
        or "music.youtube.com" in s
    )


def _is_spotify_url(s: str) -> bool:
    return _is_url(s) and "spotify.com" in s


def _clear_staging_dir(dest_dir: Path) -> None:
    """Remove partial outputs before the next backend attempt."""
    try:
        for p in dest_dir.iterdir():
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _clean_error(stderr: str) -> str:
    """Extract the most useful line from yt-dlp's stderr."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if "ERROR" in line or "error" in line.lower():
            return line[:500]
    return lines[-1][:500] if lines else "Unknown error"


def _find_ytdlp() -> str:
    """Return the venv yt-dlp path, falling back to PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    return "yt-dlp"
