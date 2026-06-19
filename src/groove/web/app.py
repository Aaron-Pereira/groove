"""
FastAPI application factory.

The app is created once and shared between the web server and the background
worker thread (both live in the same process under `groove serve`).
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from groove.config import Settings, get_settings
from groove.ssl_support import configure_default_ssl_context
from groove.store import Stores

log = logging.getLogger(__name__)

_templates: Jinja2Templates | None = None
_stores: Stores | None = None
_worker_thread: threading.Thread | None = None


def get_templates() -> Jinja2Templates:
    global _templates
    if _templates is None:
        tmpl_dir = Path(__file__).parent / "templates"
        _templates = Jinja2Templates(directory=str(tmpl_dir))
    return _templates


def get_stores(settings: Settings | None = None) -> Stores:
    global _stores
    if _stores is None:
        cfg = settings or get_settings()
        _stores = Stores(cfg.state_dir)
    return _stores


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_default_ssl_context()
    cfg = settings or get_settings()
    stores = get_stores(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        if start_worker:
            _start_background_worker(cfg, stores)
        _schedule_nightly_rotate(stores)
        yield
        # Shutdown
        _stop_background_worker()

    app = FastAPI(
        title="groove",
        description="Self-hosted music library",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach settings / stores to app state so routes can reach them
    app.state.settings = cfg
    app.state.stores = stores

    # Register all routes
    from groove.web.routes import router
    app.include_router(router)

    return app


# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

_worker_instance = None


def _start_background_worker(settings: Settings, stores: Stores) -> None:
    global _worker_thread, _worker_instance
    from groove.worker import Worker

    _worker_instance = Worker(settings, stores)
    _worker_thread = threading.Thread(
        target=_worker_instance.run_forever,
        daemon=True,
        name="groove-worker",
    )
    _worker_thread.start()
    log.info("Background worker thread started")


def _stop_background_worker() -> None:
    global _worker_instance
    if _worker_instance:
        _worker_instance.stop()


# ---------------------------------------------------------------------------
# Nightly rotate
# ---------------------------------------------------------------------------

def _schedule_nightly_rotate(stores: Stores) -> None:
    """Schedule a daily archive rotation using a daemon thread."""
    import time

    def _loop():
        import datetime
        while True:
            now = datetime.datetime.now()
            # Sleep until next midnight + 1 min
            tomorrow = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=1, second=0, microsecond=0
            )
            sleep_s = (tomorrow - now).total_seconds()
            time.sleep(sleep_s)
            try:
                stores.rotate()
                log.info("Nightly archive rotation complete")
            except Exception:
                log.exception("Nightly archive rotation failed")

    t = threading.Thread(target=_loop, daemon=True, name="groove-rotate")
    t.start()
