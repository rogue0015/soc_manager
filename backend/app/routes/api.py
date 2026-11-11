"""HTTP routes. Kept thin: parse input, call a service, return its result.

Services live on app.state (set up in main.create_app) so tests can swap them.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import AwareDatetime

from ..platforms import PLATFORMS
from ..platforms.connectors import connector_report
from ..schemas import BlogPlanIn, LiveEditIn, PostIn, TestPostIn
from ..services.errors import InvalidInput, NotFound
from ..timeutil import now_utc

router = APIRouter(prefix="/api")
MAX_UPLOAD = 8 * 1024 * 1024   # analytics files; nginx allows a little more
MAX_POST_IMPORT_BYTES = 2 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024   # media library uploads (services/media.py checks per kind)


# ---- health & platforms ---------------------------------------------------

@router.get("/health")
def health():
    return {"ok": True}


@router.get("/platforms")
def list_platforms(request: Request):
    publishers = request.app.state.publishers
    return [
        {**asdict(p), "has_adapter": p.id in publishers}
        for p in PLATFORMS.values()
    ]


def _publisher(request: Request, platform: str):
    if platform not in PLATFORMS:
        raise NotFound(f"Unknown platform: {platform}")
    pub = request.app.state.publishers.get(platform)
    if pub is None:
        raise InvalidInput(f"{PLATFORMS[platform].name} is not connected yet.")
    return pub


@router.get("/platforms/{platform}/status")
def platform_status(platform: str, request: Request):
    """Live check against the platform. Makes network calls; nothing is posted."""
    return asdict(_publisher(request, platform).check())


@router.post("/platforms/{platform}/test")
def send_test_post(platform: str, data: TestPostIn, request: Request):
    """Publish `text` immediately. This is a real, public post; it's kept in the planner."""
    return request.app.state.live.send_now(platform, data.text)


# ---- status tab -----------------------------------------------------------

@router.get("/connectors")
def connectors(request: Request):
    """How far each platform's setup has got. Key names only, never values; no platform calls."""
    state = request.app.state
    return connector_report(PLATFORMS, state.settings.secrets, state.publishers, now_utc(),
                            redirect_uris=state.signin.redirect_uris())


# ---- sign in from the app (browser OAuth) ----------------------------------
# These are visited by the browser, not called with fetch, so every outcome is
# a redirect: to the platform, or back to the Status tab with a message to show.

def _back_to_status(**message: str) -> RedirectResponse:
    return RedirectResponse(f"/?{urlencode(message)}#status", status_code=303)


@router.get("/connect/{platform}")
def connect(platform: str, request: Request):
    try:
        return RedirectResponse(request.app.state.signin.start(platform), status_code=303)
    except (InvalidInput, NotFound) as exc:
        return _back_to_status(signin_error=str(exc))


@router.get("/connect/{platform}/callback")
def connect_callback(platform: str, request: Request, code: str = "", state: str = "",
                     error: str = "", error_description: str = ""):
    try:
        message = request.app.state.signin.finish(platform, code, state, error, error_description)
    except (InvalidInput, NotFound) as exc:
        return _back_to_status(signin_error=str(exc))
    return _back_to_status(signin_ok=message)


@router.get("/system")
def system_status(request: Request):
    """Health of the backend, scheduler, queue and blog cache. Local only: no platform calls."""
    return request.app.state.monitor.snapshot()


@router.get("/activity")
def recent_activity(request: Request, limit: int = Query(20, ge=1, le=100)):
    return request.app.state.activity.recent(limit)


# ---- analytics ------------------------------------------------------------

@router.get("/analytics")
def analytics(request: Request, start: AwareDatetime | None = None, end: AwareDatetime | None = None,
              platform: str | None = None, source: Literal["custom", "blog"] | None = None,
              status: Literal["scheduled", "publishing", "published", "failed"] | None = None):
    return request.app.state.analytics.report(start, end, platform, source, status)


@router.get("/profiles")
def account_profiles(request: Request, refresh: bool = False):
    """Who you're signed in as on each connected platform. Asks the platforms (kept for 10 minutes)."""
    return request.app.state.profiles.all(refresh)


@router.get("/social/{platform}")
def social_overview(platform: str, request: Request, start: AwareDatetime | None = None,
                    end: AwareDatetime | None = None):
    """One platform for the Social tab: upcoming posts, history and stats for the period, top posts."""
    return request.app.state.social.overview(platform, start, end)


@router.get("/reports/posts")
def download_report(request: Request, format: Literal["csv", "xlsx"] = "csv",
                    start: AwareDatetime | None = None, end: AwareDatetime | None = None,
                    platform: str | None = None, source: Literal["custom", "blog"] | None = None,
                    status: Literal["scheduled", "publishing", "published", "failed"] | None = None,
                    utc_offset: int = Query(0, ge=-840, le=840)):
    """Every delivery matching the filters, as a CSV or an Excel workbook with a summary sheet."""
    name, media_type, content = request.app.state.reports.build(format, start, end, platform, source, status,
                                                                utc_offset)
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ---- scheduled posts ------------------------------------------------------

@router.post("/posts/import")
async def import_scheduled_posts(request: Request, preview: bool = True,
                                 tz: str | None = Query(None, max_length=64)):
    """Validate a scheduled-post CSV, or explicitly import it with preview=false.

    `tz` is the browser's IANA timezone, used for times written without an offset."""
    content = await _read_body(request, MAX_POST_IMPORT_BYTES)
    importer = request.app.state.post_import
    return importer.preview(content, tz) if preview else importer.import_csv(content, tz)


@router.get("/posts")
def list_posts(request: Request, start: AwareDatetime | None = None, end: AwareDatetime | None = None):
    return request.app.state.posts.list(start, end)


def _options(data: PostIn) -> dict[str, dict]:
    return {pid: opts.model_dump() for pid, opts in data.options.items()}


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request):
    return request.app.state.posts.get(post_id)


@router.post("/posts", status_code=201)
def create_post(data: PostIn, request: Request):
    return request.app.state.posts.create(data.body, data.scheduled_at, data.platforms, data.blog_url,
                                          variants=data.variants, options=_options(data))


@router.put("/posts/{post_id}")
def update_post(post_id: int, data: PostIn, request: Request):
    return request.app.state.posts.update(post_id, data.body, data.scheduled_at, data.platforms, data.blog_url,
                                          variants=data.variants, options=_options(data))


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int, request: Request):
    request.app.state.posts.delete(post_id)


@router.post("/posts/{post_id}/publish")
def post_now(post_id: int, request: Request):
    """Send the post's waiting platforms right now instead of at its time. Public, can't be unsent."""
    return request.app.state.live.post_now(post_id)


@router.post("/posts/{post_id}/retry")
def retry_post(post_id: int, request: Request):
    return request.app.state.posts.retry(post_id)


# ---- posts that are live on a platform --------------------------------------

@router.put("/posts/{post_id}/published/{platform}")
def edit_live_post(post_id: int, platform: str, data: LiveEditIn, request: Request):
    """Change the text of the live post on the platform (public, right away)."""
    return request.app.state.live.edit(post_id, platform, data.text)


@router.delete("/posts/{post_id}/published/{platform}")
def delete_live_post(post_id: int, platform: str, request: Request):
    """Delete the post on the platform itself. Can't be undone; the planner keeps its record."""
    return request.app.state.live.delete(post_id, platform)


# ---- images ------------------------------------------------------------------

async def _read_body(request: Request, limit: int) -> bytes:
    """The raw request body, refusing anything over `limit` bytes without reading it all."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise InvalidInput(f"That file is larger than {limit // 1048576} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/media", status_code=201)
async def upload_media(request: Request, name: str = ""):
    """Add a picture or video to the library, sent as the raw request body. It is streamed to a
    temporary file (videos can be up to 1 GB), then checked and kept by the media service."""
    media = request.app.state.media
    tmp = media.temp_path()
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise InvalidInput("That file is larger than 1 GB.")
                f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return media.store(tmp, name)


@router.get("/media")
def list_media(request: Request, kind: Literal["image", "video"] | None = None):
    """The media library, newest first, with the posts that use each item."""
    return request.app.state.media.list(kind)


@router.delete("/media/{media_id}", status_code=204)
def delete_media(media_id: str, request: Request):
    request.app.state.media.delete(media_id)


@router.get("/media/{media_id}")
def get_media(media_id: str, request: Request):
    path, mime = request.app.state.media.find(media_id)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


# ---- post statistics -------------------------------------------------------------

@router.get("/stats")
def stats_status(request: Request):
    """Per platform: can statistics be fetched automatically, and if not, why."""
    return request.app.state.live.stats_status()


@router.post("/stats/refresh")
def refresh_stats(request: Request):
    return request.app.state.live.refresh_stats()


@router.post("/stats/import")
async def import_stats(request: Request):
    """Import LinkedIn's analytics export (.xlsx or .csv), sent as the raw request body."""
    return request.app.state.stats_import.run(await _read_body(request, MAX_UPLOAD))


# ---- blog -----------------------------------------------------------------

@router.get("/blog/posts")
def list_blog_posts(request: Request):
    return request.app.state.blog.list()


@router.post("/blog/sync")
def sync_blog(request: Request):
    return request.app.state.blog.sync()


@router.get("/blog/article")
def preview_article(request: Request, url: str = Query(min_length=1, max_length=2000)):
    """A blog post as the article dev.to would get: title, summary, tags and the text as Markdown."""
    return request.app.state.articles.preview(url)


@router.post("/blog/plan", status_code=201)
def plan_blog(data: BlogPlanIn, request: Request):
    return request.app.state.blog.plan(data.urls, data.start, data.every_days, data.platforms)
