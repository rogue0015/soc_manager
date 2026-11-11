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

from .config import Settings, set_env_values
from .db import Database
from .platforms import Publisher, build_publishers
from .platforms.base import set_token_saver
from .routes.ai import router as ai_router
from .routes.api import router
from .services.activity import ActivityLog
from .services.analytics import AnalyticsService
from .services.article import ArticleReader
from .services.attachments import Attachments
from .services.blog import BlogService
from .services.errors import Conflict, InvalidInput, NotFound
from .services.ai_writer import AIWriter
from .services.gemini import Gemini
from .services.live import LiveService
from .services.personas import PersonaService
from .services.media import MediaService
from .services.monitor import MonitorService
from .services.posts import PostService
from .services.post_import import ScheduledPostImport
from .services.profiles import ProfileService
from .services.reports import ReportService
from .services.scheduler import SchedulerState, run_scheduler
from .services.signin import SignInService
from .services.social import SocialService
from .services.stats_import import StatsImport
from .timeutil import now_utc

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, publishers: dict[str, Publisher] | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and fake publishers."""
    settings = settings or Settings.from_environment()
    db = Database(settings.db_path)
    db.init()
    activity = ActivityLog(db)
    media = MediaService(db, settings.media_dir)
    posts = PostService(db, activity, image_exists=media.exists)
    articles = ArticleReader(db)
    attachments = Attachments(db, media, articles=articles)
    publishers = build_publishers(settings.secrets) if publishers is None else publishers

    def save_tokens(values: dict[str, str]) -> None:
        # Refreshed sign-in tokens (TikTok replaces its refresh token on every use) must survive a restart.
        settings.secrets.update(values)
        try:
            set_env_values(settings.env_file, values)
        except OSError as exc:
            log.error("Could not save refreshed tokens to %s: %s", settings.env_file, exc)
    set_token_saver(save_tokens)
    scheduler = SchedulerState(settings.scheduler_enabled, settings.scheduler_interval_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        activity.record("started", "Backend started." if settings.scheduler_enabled
                        else "Backend started with the scheduler off.")
        if settings.scheduler_enabled:
            if n := posts.recover_interrupted():
                log.warning("%s post(s) were interrupted mid-publish and marked failed", n)
            task = asyncio.create_task(
                run_scheduler(posts, publishers, settings.scheduler_interval_seconds, stop, scheduler, attachments,
                              refresh_stats=app.state.live.refresh_stats, stats_every=settings.stats_interval_seconds)
            )
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(title="Social Manager", lifespan=lifespan)
    app.state.settings = settings
    app.state.publishers = publishers
    app.state.posts = posts
    app.state.post_import = ScheduledPostImport(posts)
    app.state.activity = activity
    app.state.blog = BlogService(db, posts, settings.blog_base_url)
    app.state.analytics = AnalyticsService(db)
    app.state.social = SocialService(db, app.state.analytics)
    app.state.profiles = ProfileService(publishers)
    app.state.reports = ReportService(app.state.analytics)
    app.state.monitor = MonitorService(db, settings, scheduler, publishers, started_at=now_utc())
    app.state.signin = SignInService(settings, publishers, activity)
    app.state.media = media
    app.state.attachments = attachments
    app.state.articles = articles
    app.state.live = LiveService(posts, publishers, activity, attachments)
    app.state.stats_import = StatsImport(posts)
    app.state.personas = PersonaService(db)
    # Gemini is built per use from the shared secrets, so a new key needs no rebuild here.
    app.state.ai = AIWriter(db, app.state.personas, articles, activity, lambda: Gemini(settings.secrets))

    # Map domain errors to HTTP in one place. The UI shows `detail` to the user.
    for exc_type, status in ((NotFound, 404), (Conflict, 409), (InvalidInput, 422)):
        def handler(request: Request, exc: Exception, status: int = status):
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        app.add_exception_handler(exc_type, handler)

    app.include_router(router)
    app.include_router(ai_router)
    if settings.serve_frontend:
        # Mounted last so /api routes win. html=True serves index.html at "/".
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# No module-level `app`: uvicorn runs create_app() via --factory, so importing
# this module (e.g. in tests) never touches the real database.
