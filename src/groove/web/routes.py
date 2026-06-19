"""
All FastAPI route handlers.

Pages:
  GET  /                 – queue + add form
  GET  /bulk             – bulk add
  POST /bulk/preview     – parse + dedup preview
  POST /bulk/confirm     – enqueue after preview
  GET  /bulk/discography – artist discography picker
  POST /bulk/discography/queue – enqueue selected albums
  GET  /discoveries      – chart findings
  GET  /watchlist        – artist watchlist
  GET  /library          – browse beets library
  GET  /review           – inbox/review/ items
  GET  /help             – aggregated help

API endpoints (HTMX targets):
  POST   /api/request                   – add one request
  GET    /api/queue                     – queue table partial
  POST   /api/request/{id}/retry        – retry failed
  DELETE /api/request/{id}              – remove
  POST   /api/discovery/{id}/queue      – queue a discovery
  DELETE /api/discovery/{id}            – dismiss
  POST   /api/watchlist                 – add artist
  DELETE /api/watchlist/{name}          – remove artist
  POST   /api/watchlist/{name}/toggle-auto
  GET    /api/library/{id}/edit-form    – tag edit form partial
  POST   /api/library/{id}/update-tags  – save tags via beet modify
  POST   /api/review/accept             – accept MB candidate
  POST   /api/review/manual             – manual tag re-import
  POST   /api/scrape                    – trigger chart scrape
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from groove.store import DownloadRequest, Discovery, WatchedArtist

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_beet() -> str:
    """Locate the beet executable inside the active venv, falling back to PATH.

    Routes use bare subprocess calls, so we must resolve beet explicitly rather
    than assuming it is on the system PATH (it may only exist inside the venv).
    """
    venv_beet = Path(sys.executable).parent / "beet"
    return str(venv_beet) if venv_beet.exists() else "beet"

def _templates(request: Request):
    from groove.web.app import get_templates
    return get_templates()


def _stores(request: Request):
    return request.app.state.stores


def _settings(request: Request):
    return request.app.state.settings


def _render(request: Request, template: str, ctx: dict) -> HTMLResponse:
    tmpl = _templates(request)
    # Inject review count for sidebar badge
    review_dir = _settings(request).inbox_review_dir
    review_count = len(list(review_dir.glob("*"))) if review_dir.exists() else 0
    ctx.setdefault("review_count", review_count)
    return tmpl.TemplateResponse(request, template, ctx)


def _review_items(settings) -> list[dict]:
    """Scan inbox/review/ and return metadata for each file."""
    review_dir = settings.inbox_review_dir
    if not review_dir.exists():
        return []
    items = []
    for f in sorted(review_dir.iterdir()):
        if f.is_file() and f.suffix.lstrip(".") in ("mp3", "flac", "m4a", "ogg", "wav", "opus"):
            items.append({
                "filename": f.name,
                "path": str(f),
                "candidates": [],  # populated by a beet call in a real impl
                "tag_artist": "",
                "tag_album": "",
                "tag_title": f.stem,
            })
    return items


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def queue_page(request: Request):
    stores = _stores(request)
    requests = list(reversed(stores.requests.all()))
    stats = stores.requests.count_by_status()
    return _render(request, "index.html", {
        "active_page": "queue",
        "requests": requests,
        "stats": stats,
    })


@router.get("/bulk", response_class=HTMLResponse)
async def bulk_page(request: Request):
    return _render(request, "bulk.html", {"active_page": "bulk"})


@router.post("/bulk/preview", response_class=HTMLResponse)
async def bulk_preview(
    request: Request,
    text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
):
    from groove.bulk_parser import parse_input

    content = text
    filename = None
    if file and file.filename:
        raw = await file.read()
        content = raw.decode("utf-8-sig", errors="replace")
        filename = file.filename

    result = parse_input(content, filename=filename)

    stores = _stores(request)
    settings = _settings(request)

    # Build pending-queue key set
    pending_keys: set[str] = set()
    for req in stores.requests.all():
        if req.status not in ("done", "failed"):
            if req.artist and req.title:
                pending_keys.add(_norm(f"{req.artist} {req.title}"))
            elif req.artist and req.album:
                pending_keys.add(_norm(f"{req.artist} {req.album}"))
            elif req.raw_query:
                pending_keys.add(_norm(req.raw_query))

    # Build library key set from beets (so the UI can show "already in library")
    library_keys = _beet_library_keys(settings)

    from groove.bulk_parser import dedup_entries
    to_queue, in_library, already_pending = dedup_entries(
        result.entries,
        existing_queries=library_keys,
        pending_queries=pending_keys,
    )

    batch_id = str(uuid.uuid4())
    bulk_dir = settings.state_dir / "bulk_batches"
    bulk_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = bulk_dir / f"groove_bulk_{batch_id}.json"
    tmp_path.write_text(json.dumps([
        {"raw_query": e.raw_query, "kind": e.kind, "artist": e.artist,
         "title": e.title, "album": e.album, "source_url": e.source_url,
         "track_number": e.track_number}
        for e in to_queue
    ]))

    return _render(request, "bulk.html", {
        "active_page": "bulk",
        "preview": {
            "to_queue": to_queue,
            "in_library": in_library,
            "already_pending": already_pending,
            "errors": result.errors,
            "batch_id": batch_id,
        },
    })


@router.post("/bulk/confirm")
async def bulk_confirm(request: Request, batch_id: str = Form(...)):
    settings = _settings(request)
    tmp_path = settings.state_dir / "bulk_batches" / f"groove_bulk_{batch_id}.json"
    if not tmp_path.exists():
        return RedirectResponse("/bulk", status_code=303)

    entries = json.loads(tmp_path.read_text())
    stores = _stores(request)
    for e in entries:
        req = DownloadRequest(
            raw_query=e["raw_query"],
            kind=e.get("kind", "track"),
            artist=e.get("artist"),
            title=e.get("title"),
            album=e.get("album"),
            source_url=e.get("source_url"),
            track_number=e.get("track_number"),
            priority="low",
            batch_id=batch_id,
        )
        stores.requests.append(req)

    try:
        tmp_path.unlink()
    except OSError:
        pass

    return RedirectResponse(f"/?batch={batch_id}", status_code=303)


@router.get("/bulk/discography", response_class=HTMLResponse)
async def discography_page(request: Request, artist: str = ""):
    if not artist:
        return RedirectResponse("/bulk", status_code=303)

    from groove.discovery.new_releases import search_artist_discography

    error = None
    albums = []
    mb_artist_id = ""

    try:
        albums = search_artist_discography(artist)
        if albums:
            mb_artist_id = albums[0].get("mb_artist_id", "")
    except Exception as exc:
        error = str(exc)

    return _render(request, "discography.html", {
        "active_page": "bulk",
        "artist_name": artist,
        "albums": albums,
        "mb_artist_id": mb_artist_id,
        "error": error,
    })


@router.post("/bulk/discography/queue")
async def discography_queue(
    request: Request,
    artist_name: str = Form(...),
    mb_artist_id: str = Form(default=""),
):
    form = await request.form()
    album_ids = form.getlist("album_ids")

    if not album_ids:
        return RedirectResponse(f"/bulk/discography?artist={quote_plus(artist_name)}", status_code=303)

    from groove.discovery.new_releases import search_artist_discography

    try:
        albums = search_artist_discography(artist_name)
        album_map = {a["id"]: a for a in albums}
    except Exception:
        album_map = {}

    stores = _stores(request)
    for aid in album_ids:
        album = album_map.get(aid, {})
        raw = f"{artist_name} - {album.get('title', aid)}"
        req = DownloadRequest(
            raw_query=raw,
            kind="album",
            artist=artist_name,
            album=album.get("title"),
            priority="low",
        )
        stores.requests.append(req)

    return RedirectResponse("/", status_code=303)


@router.get("/discoveries", response_class=HTMLResponse)
async def discoveries_page(request: Request, source: str = ""):
    stores = _stores(request)
    settings = _settings(request)
    all_disc = [d for d in stores.discoveries.all() if not d.dismissed]
    sources = sorted({d.source for d in all_disc})
    if source:
        all_disc = [d for d in all_disc if d.source == source]
    all_disc.sort(key=lambda d: (d.chart_rank or 999, d.seen_at), reverse=False)

    # Last run time
    runs = stores.chart_runs.all()
    last_run = max((r.run_at for r in runs), default=None)
    last_run_str = last_run.strftime("%Y-%m-%d %H:%M") if last_run else None

    return _render(request, "discoveries.html", {
        "active_page": "discoveries",
        "discoveries": all_disc,
        "sources": sources,
        "active_source": source,
        "last_run": last_run_str,
        "min_appearances": settings.auto_queue.min_chart_appearances,
    })


@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    stores = _stores(request)
    wl = stores.watchlist.get()
    return _render(request, "watchlist.html", {
        "active_page": "watchlist",
        "artists": wl.artists,
    })


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, q: str = "", page: int = 1):
    settings = _settings(request)
    items, total_tracks, total_albums, total_artists = _query_beets(settings, q, page)
    per_page = 100
    total_pages = max(1, (total_tracks + per_page - 1) // per_page)
    return _render(request, "library.html", {
        "active_page": "library",
        "items": items,
        "query": q,
        "page": page,
        "total_pages": total_pages,
        "total_tracks": total_tracks,
        "total_albums": total_albums,
        "total_artists": total_artists,
    })


@router.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    settings = _settings(request)
    items = _review_items(settings)
    return _render(request, "review.html", {
        "active_page": "review",
        "items": items,
    })


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return _render(request, "help.html", {"active_page": "help"})


# ---------------------------------------------------------------------------
# API – queue
# ---------------------------------------------------------------------------

@router.post("/api/request")
async def api_add_request(
    request: Request,
    query: str = Form(...),
    kind: str = Form(default="track"),
    priority: str = Form(default="normal"),
):
    stores = _stores(request)
    from groove.bulk_parser import _parse_text_line as parse_line

    if _is_url(query):
        req = DownloadRequest(
            raw_query=query,
            kind=kind,
            source_url=query,
            priority=priority,
        )
    else:
        entry = parse_line(query)
        # If the form explicitly requests kind=album but the heuristic parser
        # only found a title (e.g. "Arctic Monkeys - AM"), promote title→album
        # so worker._search() builds the correct full-album YouTube query.
        if kind == "album" and entry.title and not entry.album:
            entry.album = entry.title
            entry.title = None
        req = DownloadRequest(
            raw_query=query,
            kind=kind,
            artist=entry.artist,
            title=entry.title,
            album=entry.album,
            priority=priority,
        )
    stores.requests.append(req)
    return await _queue_partial(request)


@router.get("/api/queue", response_class=HTMLResponse)
async def api_queue(request: Request):
    return await _queue_partial(request)


async def _queue_partial(request: Request) -> HTMLResponse:
    stores = _stores(request)
    requests = list(reversed(stores.requests.all()))
    tmpl = _templates(request)
    return tmpl.TemplateResponse(request, "partials/queue_table.html", {
        "requests": requests,
    })


@router.post("/api/request/{req_id}/retry")
async def api_retry(request: Request, req_id: str):
    stores = _stores(request)
    stores.requests.update_one(req_id, {"status": "pending", "error": None, "attempts": 0})
    return await _queue_partial(request)


@router.delete("/api/request/{req_id}")
async def api_delete_request(request: Request, req_id: str):
    stores = _stores(request)
    stores.requests.remove(req_id)
    return await _queue_partial(request)


# ---------------------------------------------------------------------------
# API – discoveries
# ---------------------------------------------------------------------------

@router.post("/api/discovery/{disc_id}/queue")
async def api_queue_discovery(request: Request, disc_id: str):
    stores = _stores(request)
    disc = stores.discoveries.get(disc_id)
    if disc:
        req = DownloadRequest(
            raw_query=f"{disc.artist} - {disc.title or disc.album or ''}",
            kind="track",
            artist=disc.artist,
            title=disc.title,
            album=disc.album,
            priority="normal",
        )
        stores.requests.append(req)
        stores.discoveries.update_one(disc_id, {"auto_queued": True})
    return _disc_row_removed(disc_id)


@router.delete("/api/discovery/{disc_id}")
async def api_dismiss_discovery(request: Request, disc_id: str):
    stores = _stores(request)
    stores.discoveries.update_one(disc_id, {"dismissed": True})
    return _disc_row_removed(disc_id)


def _disc_row_removed(disc_id: str) -> Response:
    return Response(
        content=f'<tr id="disc-{disc_id}" style="display:none"></tr>',
        media_type="text/html",
    )


# ---------------------------------------------------------------------------
# API – watchlist
# ---------------------------------------------------------------------------

@router.post("/api/watchlist", response_class=HTMLResponse)
async def api_add_watchlist(
    request: Request,
    name: str = Form(...),
    mb_artist_id: str = Form(default=""),
    auto_download: str = Form(default=""),
):
    stores = _stores(request)
    artist = WatchedArtist(
        name=name.strip(),
        mb_artist_id=mb_artist_id.strip() or None,
        auto_download_new_albums=bool(auto_download),
    )
    stores.watchlist.add_artist(artist)
    wl = stores.watchlist.get()
    tmpl = _templates(request)
    return tmpl.TemplateResponse(request, "watchlist.html", {
        "active_page": "watchlist",
        "artists": wl.artists,
        "review_count": 0,
    })


@router.delete("/api/watchlist/{name}", response_class=HTMLResponse)
async def api_remove_watchlist(request: Request, name: str):
    stores = _stores(request)
    stores.watchlist.remove_artist(name)
    wl = stores.watchlist.get()
    tmpl = _templates(request)
    return tmpl.TemplateResponse(request, "watchlist.html", {
        "active_page": "watchlist",
        "artists": wl.artists,
        "review_count": 0,
    })


@router.post("/api/watchlist/{name}/toggle-auto")
async def api_toggle_auto(request: Request, name: str):
    stores = _stores(request)
    wl = stores.watchlist.get()
    for a in wl.artists:
        if a.name.lower() == name.lower():
            stores.watchlist.update_artist(name, {"auto_download_new_albums": not a.auto_download_new_albums})
            break
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# API – library / tags
# ---------------------------------------------------------------------------

@router.get("/api/library/{item_id}/edit-form", response_class=HTMLResponse)
async def api_edit_form(request: Request, item_id: int):
    settings = _settings(request)
    item = _beet_get_item(settings, item_id)
    if not item:
        return HTMLResponse("<p>Item not found</p>")
    tmpl = _templates(request)
    return tmpl.TemplateResponse(request, "partials/edit_form.html", {
        "item_id": item_id,
        "title": item.get("title", ""),
        "artist": item.get("artist", ""),
        "album": item.get("album", ""),
        "year": item.get("year", ""),
        "genre": item.get("genre", ""),
    })


@router.post("/api/library/{item_id}/update-tags")
async def api_update_tags(
    request: Request,
    item_id: int,
    title: str = Form(default=""),
    artist: str = Form(default=""),
    album: str = Form(default=""),
    year: str = Form(default=""),
    genre: str = Form(default=""),
):
    settings = _settings(request)
    _beet_modify(settings, item_id, title=title, artist=artist, album=album, year=year, genre=genre)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# API – review
# ---------------------------------------------------------------------------

@router.post("/api/review/accept", response_class=HTMLResponse)
async def api_review_accept(
    request: Request,
    file_path: str = Form(...),
    mb_release_id: str = Form(...),
):
    settings = _settings(request)
    safe = _safe_review_path(settings, file_path)
    if safe is None:
        return HTMLResponse("<p>Invalid file path.</p>", status_code=400)
    _beet_import_with_release(settings, str(safe), mb_release_id)
    return HTMLResponse('<tr style="display:none"></tr>')


@router.post("/api/review/manual", response_class=HTMLResponse)
async def api_review_manual(
    request: Request,
    file_path: str = Form(...),
    artist: str = Form(default=""),
    album: str = Form(default=""),
    title: str = Form(default=""),
):
    settings = _settings(request)
    safe = _safe_review_path(settings, file_path)
    if safe is None:
        return HTMLResponse("<p>Invalid file path.</p>", status_code=400)
    _beet_import_manual(settings, safe, artist=artist, album=album, title=title)
    return HTMLResponse('<tr style="display:none"></tr>')


# ---------------------------------------------------------------------------
# API – scrape
# ---------------------------------------------------------------------------

@router.post("/api/scrape")
async def api_scrape(request: Request):
    """Trigger an immediate chart scrape in a background thread."""
    import threading
    settings = _settings(request)
    stores = _stores(request)

    def _run():
        try:
            _run_scrape(settings, stores)
        except Exception:
            log.exception("Manual scrape failed")

    threading.Thread(target=_run, daemon=True).start()
    return Response(status_code=202)


# ---------------------------------------------------------------------------
# Beets helpers
# ---------------------------------------------------------------------------

def _query_beets(settings, query: str, page: int) -> tuple[list[dict], int, int, int]:
    """Query beets library and return (items, total_tracks, total_albums, total_artists)."""
    per_page = 100
    offset = (page - 1) * per_page
    try:
        config = str(settings.beets_config)
        beet_query = query if query else ""
        result = subprocess.run(
            [_find_beet(), "--config", config, "ls", "-f",
             "$id\t$title\t$artist\t$album\t$year\t$genre\t$track",
             *([beet_query] if beet_query else [])],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("beet ls failed (exit %d): %s", result.returncode, result.stderr[:500])
            return [], 0, 0, 0
        items = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 6:
                items.append({
                    "id": parts[0],
                    "title": parts[1],
                    "artist": parts[2],
                    "album": parts[3],
                    "year": parts[4],
                    "genre": parts[5],
                    "track": parts[6] if len(parts) > 6 else "",
                })
        total_tracks = len(items)
        artists = {i["artist"] for i in items}
        albums = {(i["artist"], i["album"]) for i in items}
        return items[offset:offset + per_page], total_tracks, len(albums), len(artists)
    except Exception as exc:
        log.warning("beets query failed: %s", exc)
        return [], 0, 0, 0


def _beet_library_keys(settings) -> set[str]:
    """Return a set of normalised 'artist title' and 'artist album' keys from the beets library.

    Used by bulk preview to identify tracks/albums already in the library.
    """
    try:
        result = subprocess.run(
            [_find_beet(), "--config", str(settings.beets_config), "ls", "-f",
             "$artist\t$title\t$album"],
            capture_output=True, text=True, timeout=30,
        )
        keys: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                artist, title, album = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if artist and title:
                    keys.add(_norm(f"{artist} {title}"))
                if artist and album:
                    keys.add(_norm(f"{artist} {album}"))
        return keys
    except Exception as exc:
        log.warning("beets library key query failed: %s", exc)
        return set()


def _beet_get_item(settings, item_id: int) -> dict | None:
    try:
        result = subprocess.run(
            [_find_beet(), "--config", str(settings.beets_config), "ls", "-f",
             "$id\t$title\t$artist\t$album\t$year\t$genre",
             f"id:{item_id}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 6:
                return {"id": parts[0], "title": parts[1], "artist": parts[2],
                        "album": parts[3], "year": parts[4], "genre": parts[5]}
    except Exception:
        pass
    return None


def _beet_modify(settings, item_id: int, **fields) -> None:
    args = [f"{k}={v}" for k, v in fields.items() if v]
    if not args:
        return
    try:
        subprocess.run(
            [_find_beet(), "--config", str(settings.beets_config), "modify", "--yes", f"id:{item_id}"] + args,
            capture_output=True, timeout=30,
        )
    except Exception as exc:
        log.warning("beet modify failed: %s", exc)


def _beet_import_with_release(settings, file_path: str, mb_release_id: str) -> None:
    try:
        subprocess.run(
            [_find_beet(), "--config", str(settings.beets_config), "import",
             "--noincremental", f"--set=mb_albumid={mb_release_id}", file_path],
            capture_output=True, timeout=120,
        )
    except Exception as exc:
        log.warning("beet import (accept) failed: %s", exc)


def _beet_import_manual(settings, path: Path, artist: str, album: str, title: str) -> None:
    args = [_find_beet(), "--config", str(settings.beets_config), "import", "--noincremental"]
    if artist:
        args += [f"--set=artist={artist}"]
    if album:
        args += [f"--set=album={album}"]
    if title:
        args += [f"--set=title={title}"]
    args.append(str(path))
    try:
        subprocess.run(args, capture_output=True, timeout=120)
    except Exception as exc:
        log.warning("beet import (manual) failed: %s", exc)


def _run_scrape(settings, stores) -> None:
    from groove.discovery import billboard, uk_top40
    from groove.discovery.lastfm import scrape_global_top
    from groove.discovery.genre import scrape_all_genres
    from groove.autoqueue import run_autoqueue

    scrapers = []
    if settings.discovery.billboard:
        scrapers.append(lambda: billboard.scrape())
    if settings.discovery.uk_top40:
        scrapers.append(lambda: uk_top40.scrape())
    if settings.discovery.lastfm_global and settings.api_keys.lastfm_api_key:
        scrapers.append(lambda: scrape_global_top(
            settings.api_keys.lastfm_api_key,
            settings.api_keys.lastfm_api_secret,
        ))

    for fn in scrapers:
        disc, run = fn()
        _merge_discoveries(stores, disc)
        stores.chart_runs.append(run)

    if settings.discovery.genres and settings.api_keys.lastfm_api_key:
        disc_list, runs = scrape_all_genres(
            settings.discovery.genres,
            settings.api_keys.lastfm_api_key,
            settings.api_keys.lastfm_api_secret,
        )
        _merge_discoveries(stores, disc_list)
        for r in runs:
            stores.chart_runs.append(r)

    added = run_autoqueue(stores, settings)
    log.info("Scrape complete. Auto-queued %d items.", added)


def _merge_discoveries(stores, new_items) -> None:
    """
    Add new discoveries, incrementing the appearances counter for
    items already seen today.

    Re-reads the store after each update so that appearance counts
    are always based on the latest persisted value, even when multiple
    scrapers return the same track in a single run.
    """
    from datetime import UTC, datetime

    today = datetime.now(UTC).date()

    def _build_today_map():
        """Return {norm_key: (discovery_id, appearances)} for today's entries."""
        result: dict[tuple[str, str], tuple[str, int]] = {}
        for d in stores.discoveries.all():
            if d.seen_at.date() == today and not d.dismissed:
                k = (_norm(d.artist), _norm(d.title or ""))
                result[k] = (d.id, d.appearances)
        return result

    for item in new_items:
        key = (_norm(item.artist), _norm(item.title or ""))
        today_map = _build_today_map()
        if key in today_map:
            disc_id, current_appearances = today_map[key]
            stores.discoveries.update_one(disc_id, {"appearances": current_appearances + 1})
        else:
            stores.discoveries.append(item)


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))


def _safe_review_path(settings, file_path: str) -> Path | None:
    """Resolve file_path and confirm it is inside inbox_review_dir.

    Returns the resolved Path on success, or None if the path escapes
    the expected directory (path traversal guard).
    """
    try:
        resolved = Path(file_path).resolve()
        review_dir = settings.inbox_review_dir.resolve()
        resolved.relative_to(review_dir)  # raises ValueError if outside
        return resolved
    except (ValueError, OSError):
        log.warning("Rejected unsafe review file_path: %s", file_path)
        return None
