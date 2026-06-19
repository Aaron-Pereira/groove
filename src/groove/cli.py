"""
groove CLI.

Commands (grouped by category):

  SETUP
    groove init [DRIVE_PATH]      Interactive first-run wizard
    groove install-agents         Install launchd plist files

  REQUESTING
    groove request QUERY          Add a track/album to the queue
    groove request-discography ARTIST  Browse and queue an artist's discography

  MAINTENANCE
    groove serve                  Start web UI + background worker
    groove worker                 Run only the background worker
    groove import-cds             Import CD rips from inbox/cds/
    groove metadata refresh       Run `beet mbsync` on the whole library
    groove metadata rescan        Run `beet update` to re-sync index with disk
    groove metadata retag-albums  Re-match every album against MusicBrainz
    groove scrape charts          Run chart scrapers now
    groove scrape new-releases    Run MusicBrainz new-releases check now

  DIAGNOSTICS
    groove doctor                 Health-check the whole system
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional


def _find_beet() -> str:
    """Find beet inside the active venv, falling back to PATH."""
    venv_beet = Path(sys.executable).parent / "beet"
    return str(venv_beet) if venv_beet.exists() else "beet"

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="groove",
    help="Self-hosted music library manager.\n\nRun `groove COMMAND --help` for usage examples.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)
metadata_app = typer.Typer(help="Metadata maintenance commands.", no_args_is_help=True)
scrape_app = typer.Typer(help="Chart and release scrapers.", no_args_is_help=True)
app.add_typer(metadata_app, name="metadata")
app.add_typer(scrape_app, name="scrape")

console = Console()
err_console = Console(stderr=True, style="red")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _get_settings(config: Path | None = None):
    from groove.config import load_settings
    return load_settings(config)


# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------

@app.command()
def init(
    drive_path: Optional[Path] = typer.Argument(
        None,
        help="Path to the drive root (e.g. /Volumes/Music). Defaults to /Volumes/Music.",
    ),
    force: bool = typer.Option(False, "--force", help="Skip the alien-files safety check."),
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="Skip prompts, use defaults."),
):
    """
    [bold]First-run wizard.[/bold]

    Creates the groove/ folder layout on your drive, prompts for API keys,
    writes groove.toml, and runs a health check.

    [dim]Example: groove init /Volumes/Music[/dim]
    """
    from groove.init_hdd import init_hdd, InitError, check_drive
    from groove.doctor import run_doctor

    root = drive_path or Path("/Volumes/Music")

    console.print(f"\n[bold cyan]groove init[/bold cyan] → [dim]{root}/groove/[/dim]\n")

    # Step 1: Create directory layout
    console.print("[bold]Step 1/4[/bold] Creating directory layout…")
    try:
        groove_root = init_hdd(root, force=force)
        console.print(f"  [green]✓[/green] Layout created at {groove_root}")
    except InitError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    # Step 2: API keys
    console.print("\n[bold]Step 2/4[/bold] API keys")
    acoustid_key = ""
    lastfm_key = ""
    lastfm_secret = ""

    if not non_interactive:
        console.print("  [dim]Get a free AcoustID key at https://acoustid.org/new-application[/dim]")
        acoustid_key = typer.prompt("  AcoustID API key (press Enter to skip)", default="")
        console.print("  [dim]Get a free Last.fm key at https://www.last.fm/api/account/create[/dim]")
        lastfm_key = typer.prompt("  Last.fm API key (press Enter to skip)", default="")
        if lastfm_key:
            lastfm_secret = typer.prompt("  Last.fm API secret", default="")

    # Step 3: Write groove.toml
    console.print("\n[bold]Step 3/4[/bold] Writing groove.toml…")
    config_path = groove_root / "groove.toml"
    _write_groove_toml(config_path, root=groove_root, acoustid=acoustid_key,
                       lastfm_key=lastfm_key, lastfm_secret=lastfm_secret)
    console.print(f"  [green]✓[/green] Config written to {config_path}")

    # Copy beets.yaml if not present, then rewrite paths to match groove_root
    beets_dst = groove_root / "beets.yaml"
    if not beets_dst.exists():
        beets_src = Path(__file__).parent.parent.parent / "config" / "beets.yaml"
        if beets_src.exists():
            content = beets_src.read_text(encoding="utf-8")
            # Replace hardcoded template paths with the actual installation root
            content = content.replace(
                "/Volumes/Music/groove/library", str(groove_root / "library")
            )
            content = content.replace(
                "/Volumes/Music/groove/db/musiclib.db", str(groove_root / "db" / "musiclib.db")
            )
            content = content.replace(
                "/Volumes/Music/groove/logs/beets-import.log",
                str(groove_root / "logs" / "beets-import.log"),
            )
            beets_dst.write_text(content, encoding="utf-8")
            console.print(f"  [green]✓[/green] beets.yaml written to {beets_dst}")

    # Step 4: Doctor
    console.print("\n[bold]Step 4/4[/bold] Running doctor…")
    from groove.config import reload_settings
    settings = reload_settings(config_path)
    report = run_doctor(settings)
    _print_doctor_report(report)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print(f"  → Start the server:  [cyan]groove serve[/cyan]")
    console.print(f"  → Open the UI:       [cyan]http://{settings.web.host}:{settings.web.port}[/cyan]")
    console.print(f"  → Run health check:  [cyan]groove doctor[/cyan]")
    console.print()


def _write_groove_toml(
    path: Path, *, root: Path, acoustid: str, lastfm_key: str, lastfm_secret: str
) -> None:
    content = f"""hdd_root = "{root}"

[web]
host = "127.0.0.1"
port = 8765

[audio]
codec = "mp3"
bitrate = "192"

[discovery]
billboard = true
uk_top40 = true
lastfm_global = true
genres = ["rock", "hip-hop", "electronic"]

[auto_queue]
min_chart_appearances = 2

[api_keys]
acoustid = "{acoustid}"
lastfm_api_key = "{lastfm_key}"
lastfm_api_secret = "{lastfm_secret}"

[worker]
poll_interval_seconds = 5
max_retries = 3
min_free_space_gb = 5.0
"""
    path.write_text(content, encoding="utf-8")


@app.command("install-agents")
def install_agents(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to groove.toml"),
):
    """
    Install launchd plist files into ~/Library/LaunchAgents/ and load them.

    [dim]Example: groove install-agents[/dim]
    """
    launchd_src = Path(__file__).parent.parent.parent / "launchd"
    repo_root = launchd_src.parent.resolve()
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    plists = list(launchd_src.glob("*.plist"))
    if not plists:
        err_console.print("No plist files found in launchd/")
        raise typer.Exit(1)

    for plist in plists:
        dst = agents_dir / plist.name
        content = plist.read_text(encoding="utf-8").replace("__GROOVE_REPO__", str(repo_root))
        dst.write_text(content, encoding="utf-8")
        console.print(f"  [green]✓[/green] Installed {plist.name}")
        try:
            subprocess.run(["launchctl", "load", "-w", str(dst)], check=True)
            console.print(f"  [green]✓[/green] Loaded {plist.name}")
        except Exception as exc:
            console.print(f"  [yellow]![/yellow] Could not load {plist.name}: {exc}")

    console.print("\n[bold green]Agents installed.[/bold green]")
    console.print("Manage with:")
    console.print("  launchctl list | grep groove")
    console.print("  launchctl unload ~/Library/LaunchAgents/com.groove.server.plist")


# ---------------------------------------------------------------------------
# REQUESTING
# ---------------------------------------------------------------------------

@app.command()
def request(
    query: Optional[str] = typer.Argument(None, help="'Artist - Title' or a YouTube URL."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Text or CSV file with entries."),
    spotify_export: Optional[Path] = typer.Option(None, "--spotify-export", help="Exportify CSV file."),
    youtube_playlist: Optional[str] = typer.Option(None, "--youtube-playlist", help="YouTube playlist URL."),
    kind: str = typer.Option("track", "--kind", "-k", help="track | album | playlist"),
    priority: str = typer.Option("normal", "--priority", "-p", help="high | normal | low"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and preview without queuing."),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Add music to the download queue.

    [dim]Examples:
      groove request "Arctic Monkeys - AM"
      groove request "https://youtu.be/xyz123"
      groove request --kind album "The Beatles - Abbey Road"
      groove request --file list.txt
      groove request --spotify-export exportify.csv
      groove request --youtube-playlist https://youtube.com/playlist?list=...
      groove request --dry-run --file list.txt[/dim]
    """
    from groove.bulk_parser import parse_input, ParsedEntry
    from groove.store import DownloadRequest, Stores

    settings = _get_settings(config)
    stores = Stores(settings.state_dir)

    entries: list[ParsedEntry] = []

    if youtube_playlist:
        result = parse_input(youtube_playlist)
        entries = result.entries
    elif spotify_export:
        result = parse_input(bytes_content=spotify_export.read_bytes(), filename=spotify_export.name)
        entries = result.entries
    elif file:
        result = parse_input(file.read_text(encoding="utf-8-sig"), filename=file.name)
        entries = result.entries
    elif query:
        from groove.bulk_parser import _parse_text_line
        if query.startswith(("http://", "https://")):
            entries = [ParsedEntry(raw_query=query, source_url=query, kind=kind)]
        else:
            e = _parse_text_line(query)
            e.kind = kind
            entries = [e]
    else:
        err_console.print("Provide a query, --file, --spotify-export, or --youtube-playlist.")
        raise typer.Exit(1)

    if not entries:
        console.print("[yellow]No entries parsed.[/yellow]")
        raise typer.Exit(0)

    console.print(f"  Parsed [bold]{len(entries)}[/bold] entries.")

    if dry_run:
        table = Table("Query", "Kind", "Artist", "Title/Album")
        for e in entries[:50]:
            table.add_row(e.raw_query[:60], e.kind, e.artist or "—", e.title or e.album or "—")
        console.print(table)
        if len(entries) > 50:
            console.print(f"  … and {len(entries) - 50} more")
        return

    batch_id = str(uuid.uuid4()) if len(entries) > 1 else None
    for e in entries:
        req = DownloadRequest(
            raw_query=e.raw_query,
            kind=e.kind,
            artist=e.artist,
            title=e.title,
            album=e.album,
            source_url=e.source_url,
            track_number=e.track_number,
            priority=priority,
            batch_id=batch_id,
        )
        stores.requests.append(req)

    console.print(f"  [green]✓[/green] Queued {len(entries)} item(s) (priority: {priority})")


@app.command("request-discography")
def request_discography(
    artist: str = typer.Argument(..., help="Artist name to look up on MusicBrainz."),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    release_types: str = typer.Option("album", "--types", help="Comma-separated: album,ep,single"),
    priority: str = typer.Option("low", "--priority", "-p"),
    all_: bool = typer.Option(False, "--all", help="Queue all albums without asking."),
):
    """
    Browse an artist's discography on MusicBrainz and queue selected albums.

    [dim]Example: groove request-discography "Arctic Monkeys"[/dim]
    """
    from groove.discovery.new_releases import search_artist_discography
    from groove.store import DownloadRequest, Stores

    settings = _get_settings(config)
    stores = Stores(settings.state_dir)
    types = [t.strip() for t in release_types.split(",")]

    console.print(f"  Looking up [bold]{artist}[/bold] on MusicBrainz…")
    albums = search_artist_discography(artist, release_types=types)

    if not albums:
        console.print("[yellow]No albums found.[/yellow]")
        raise typer.Exit(0)

    table = Table("№", "Title", "Year", "Type")
    for i, a in enumerate(albums, 1):
        table.add_row(str(i), a["title"], (a.get("first_release_date") or "")[:4] or "?", a.get("type", ""))
    console.print(table)

    if all_:
        selected = albums
    else:
        chosen = typer.prompt(
            f"  Enter album numbers to queue (e.g. 1,3,5) or 'all'",
            default="all",
        )
        if chosen.strip().lower() == "all":
            selected = albums
        else:
            idxs = [int(n.strip()) - 1 for n in chosen.split(",") if n.strip().isdigit()]
            selected = [albums[i] for i in idxs if 0 <= i < len(albums)]

    batch_id = str(uuid.uuid4())
    for a in selected:
        req = DownloadRequest(
            raw_query=f"{artist} - {a['title']}",
            kind="album",
            artist=artist,
            album=a["title"],
            priority=priority,
            batch_id=batch_id,
        )
        stores.requests.append(req)

    console.print(f"  [green]✓[/green] Queued {len(selected)} album(s).")


# ---------------------------------------------------------------------------
# MAINTENANCE
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
):
    """
    Start the groove web server + background download worker.

    [dim]Example: groove serve[/dim]
    """
    import uvicorn
    from groove.config import reload_settings

    settings = reload_settings(config)
    os.environ.setdefault("GROOVE_CONFIG", str(config or settings.hdd_root / "groove.toml"))

    console.print(f"\n[bold cyan]groove[/bold cyan] serving at http://{settings.web.host}:{settings.web.port}\n")
    uvicorn.run(
        "groove.web.app:create_app",
        factory=True,
        host=settings.web.host,
        port=settings.web.port,
        log_level="info",
        reload=reload,
    )


@app.command()
def worker(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    once: bool = typer.Option(False, "--once", help="Process one item and exit."),
):
    """
    Run only the download worker (no web UI).

    [dim]Example: groove worker[/dim]
    """
    from groove.config import reload_settings
    from groove.store import Stores
    from groove.worker import Worker

    settings = reload_settings(config)
    stores = Stores(settings.state_dir)
    w = Worker(settings, stores)

    if once:
        did_work = w.run_once()
        if not did_work:
            console.print("No pending requests.")
    else:
        console.print("Worker running. Press Ctrl-C to stop.")
        try:
            w.run_forever()
        except KeyboardInterrupt:
            console.print("Stopped.")


@app.command("import-cds")
def import_cds(
    directory: Optional[Path] = typer.Argument(None, help="CD rip directory (default: inbox/cds/)"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Interactively import CD rips via beets.

    Drop ripped albums into inbox/cds/, then run this command.
    beets will prompt for each album – use arrow keys to pick a match.

    [dim]Example: groove import-cds[/dim]
    """
    from groove.importer import import_cds as _import_cds
    settings = _get_settings(config)
    console.print(f"  Running interactive beet import on {directory or settings.inbox_cds_dir}…")
    result = _import_cds(settings, directory)
    if result.imported:
        console.print(f"  [green]✓[/green] Imported: {len(result.imported)} track(s)")
    if result.skipped:
        console.print(f"  [yellow]![/yellow] Skipped: {len(result.skipped)}")
    if result.review:
        console.print(f"  [yellow]![/yellow] Sent to review: {len(result.review)}")
    if not result.success:
        console.print(f"  [red]Error:[/red] {result.error}")


# ---------------------------------------------------------------------------
# METADATA sub-commands
# ---------------------------------------------------------------------------

@metadata_app.command("refresh")
def metadata_refresh(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Re-fetch MusicBrainz metadata for all imported tracks (runs beet mbsync).

    Safe to run weekly. Only updates files that already have an embedded MBID.

    [dim]Example: groove metadata refresh[/dim]
    """
    settings = _get_settings(config)
    console.print("  Running [cyan]beet mbsync[/cyan]…")
    result = subprocess.run(
        [_find_beet(), "--config", str(settings.beets_config), "mbsync"],
        timeout=600,
    )
    if result.returncode == 0:
        console.print("  [green]✓[/green] Metadata refresh complete.")
    else:
        console.print(f"  [red]✗[/red] beet mbsync returned exit code {result.returncode}")


@metadata_app.command("rescan")
def metadata_rescan(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Re-sync beets' index with files on disk (runs beet update).

    Use after editing tags externally (e.g. in Kid3 or MusicBrainz Picard).

    [dim]Example: groove metadata rescan[/dim]
    """
    settings = _get_settings(config)
    console.print("  Running [cyan]beet update[/cyan]…")
    result = subprocess.run(
        [_find_beet(), "--config", str(settings.beets_config), "update"],
        timeout=600,
    )
    if result.returncode == 0:
        console.print("  [green]✓[/green] Rescan complete.")
    else:
        console.print(f"  [red]✗[/red] beet update returned exit code {result.returncode}")


@metadata_app.command("retag-albums")
def metadata_retag_albums(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List albums only; do not import"),
    all_albums: bool = typer.Option(
        False,
        "--all",
        help="Retag every album, not only those missing MusicBrainz IDs",
    ),
    artist: Optional[str] = typer.Option(
        None,
        "--artist",
        help="Only albums under this top-level library artist folder",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        min=1,
        help="Process at most this many albums (for testing)",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Skip albums already recorded in state/retag_albums.json",
    ),
):
    """
    Re-match each album folder against MusicBrainz and fix track listings.

    Walks every leaf album directory under library/, strips bogus playlist track
    numbers (e.g. every track tagged as #63), then runs beets import in place.
    Strong matches are applied automatically; failures are logged for manual review.

    This can take several hours on a large library (MusicBrainz rate limits).
    Use --limit 1 to test on a single album first.

    [dim]Examples:[/dim]
      groove metadata retag-albums --dry-run
      groove metadata retag-albums --artist "Ariana Grande" --limit 1
      groove metadata retag-albums --resume
    """
    from groove.metadata_retagger import RetagStatus, run_retag_batch, write_retag_log

    settings = _get_settings(config)

    if not settings.library_dir.is_dir():
        console.print(f"  [red]✗[/red] Library not found: {settings.library_dir}")
        raise typer.Exit(1)

    console.print(
        f"  Scanning [cyan]{settings.library_dir}[/cyan]"
        + (" [dim](dry run)[/dim]" if dry_run else "")
    )

    def on_progress(index: int, total: int, result) -> None:
        album = result.album
        label = f"{album.artist} / {album.album_label}"
        if dry_run:
            console.print(f"  [dim]{index}/{total}[/dim] would retag: {label}")
            return
        icon = {
            RetagStatus.TAGGED: "[green]✓[/green]",
            RetagStatus.NO_MATCH: "[yellow]?[/yellow]",
            RetagStatus.SKIPPED: "[dim]–[/dim]",
            RetagStatus.ERROR: "[red]✗[/red]",
        }.get(result.status, "?")
        extra = f" ({result.similarity:.0f}%)" if result.similarity is not None else ""
        stripped = (
            f", stripped {result.tracks_stripped} bogus track numbers"
            if result.tracks_stripped
            else ""
        )
        console.print(f"  {icon} [{index}/{total}] {label}{extra}{stripped}")
        if result.status not in (RetagStatus.TAGGED,):
            console.print(f"      [dim]{result.message}[/dim]")

    report = run_retag_batch(
        settings,
        only_missing=not all_albums,
        artist_filter=artist,
        limit=limit,
        resume=resume,
        dry_run=dry_run,
        on_progress=on_progress,
    )

    if dry_run:
        console.print(f"  [dim]{len(report.results)} album(s) would be processed.[/dim]")
        return

    log_path = write_retag_log(settings, report)
    console.print(
        f"\n  [green]Done.[/green] Tagged {report.tagged}, "
        f"no match {report.no_match}, errors {report.errors} "
        f"({len(report.results)} total)."
    )
    console.print(f"  Log: [cyan]{log_path}[/cyan]")
    if report.no_match or report.errors:
        console.print(
            "  [yellow]Tip:[/yellow] Re-run failed albums manually with:\n"
            "    beet --config … import -C -t -I \"library/Artist/Album (Year)\""
        )


# ---------------------------------------------------------------------------
# SCRAPE sub-commands
# ---------------------------------------------------------------------------

@scrape_app.command("charts")
def scrape_charts(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Scrape Billboard Hot 100, UK Top 40, and Last.fm charts now.

    [dim]Example: groove scrape charts[/dim]
    """
    from groove.config import reload_settings
    from groove.store import Stores
    from groove.web.routes import _run_scrape

    settings = reload_settings(config)
    stores = Stores(settings.state_dir)
    console.print("  Running chart scrapers…")
    _run_scrape(settings, stores)
    console.print("  [green]✓[/green] Done.")


@scrape_app.command("new-releases")
def scrape_new_releases(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """
    Check MusicBrainz for new releases from watchlist artists.

    [dim]Example: groove scrape new-releases[/dim]
    """
    from groove.config import reload_settings
    from groove.store import Stores
    from groove.discovery.new_releases import scrape_new_releases as _scrape
    from groove.autoqueue import run_autoqueue

    settings = reload_settings(config)
    stores = Stores(settings.state_dir)
    wl = stores.watchlist.get()

    console.print(f"  Checking {len(wl.artists)} artist(s) on MusicBrainz…")
    discoveries, run = _scrape(wl.artists)
    stores.discoveries.append_many(discoveries)
    stores.chart_runs.append(run)

    added = run_autoqueue(stores, settings)
    console.print(f"  [green]✓[/green] Found {len(discoveries)} new release(s). Auto-queued {added}.")


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@app.command()
def doctor(
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    backup: bool = typer.Option(False, "--backup", help="Also run the state backup to ~/groove-state-backup/"),
):
    """
    Health-check the whole groove installation.

    Checks: drive space, filesystem, write access, binaries, API keys, state files.

    [dim]Example: groove doctor[/dim]
    """
    from groove.doctor import run_doctor, backup_state

    settings = _get_settings(config)
    report = run_doctor(settings)
    _print_doctor_report(report)

    if backup:
        result = backup_state(settings)
        _print_check(result)

    if report.has_errors:
        raise typer.Exit(1)


def _print_doctor_report(report) -> None:
    for check in report.checks:
        _print_check(check)


def _print_check(check) -> None:
    icon = {"ok": "[green]✓[/green]", "warning": "[yellow]![/yellow]", "error": "[red]✗[/red]", "skip": "[dim]–[/dim]"}.get(check.status, "?")
    console.print(f"  {icon} [{check.status.upper()}] {check.name}: {check.message}")
    if check.detail:
        console.print(f"      [dim]{check.detail}[/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
