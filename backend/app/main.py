"""App entry point.

Local dev (API + UI in one process):
    ./scripts/dev.sh   (= .venv/bin/uvicorn --factory backend.app.main:create_app --reload)
    then open http://127.0.0.1:8000

Docker (API only, the frontend container proxies /api here):
    docker compose up --build   then open http://127.0.0.1:8080
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import Database
from .platforms import Publisher, build_publishers
from .routes.api import router
from .services.blog import BlogService
from .services.errors import Conflict, InvalidInput, NotFound
from .services.posts import PostService
from .services.scheduler import run_scheduler

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, publishers: dict[str, Publisher] | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and fake publishers."""
    settings = settings or Settings.from_environment()
    db = Database(settings.db_path)
    db.init()
    posts = PostService(db)
    publishers = build_publishers(settings.secrets) if publishers is None else publishers

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        if settings.scheduler_enabled:
            if n := posts.recover_interrupted():
                log.warning("%s post(s) were interrupted mid-publish and marked failed", n)
            task = asyncio.create_task(
                run_scheduler(posts, publishers, settings.scheduler_interval_seconds, stop)
            )
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(title="Social Manager", lifespan=lifespan)
    app.state.settings = settings
    app.state.publishers = publishers
    app.state.posts = posts
    app.state.blog = BlogService(db, posts, settings.blog_base_url)

    # Map domain errors to HTTP in one place. The UI shows `detail` to the user.
    for exc_type, status in ((NotFound, 404), (Conflict, 409), (InvalidInput, 422)):
        def handler(request: Request, exc: Exception, status: int = status):
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        app.add_exception_handler(exc_type, handler)

    app.include_router(router)
    if settings.serve_frontend:
        # Mounted last so /api routes win. html=True serves index.html at "/".
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# No module-level `app`: uvicorn runs create_app() via --factory, so importing
# this module (e.g. in tests) never touches the real database.
