"""HTTP routes. Kept thin: parse input, call a service, return its result.

Services live on app.state (set up in main.create_app) so tests can swap them.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import AwareDatetime

from ..platforms import PLATFORMS, PublishError
from ..schemas import BlogPlanIn, PostIn, TestPostIn
from ..services.errors import InvalidInput, NotFound
from ..services.posts import validate_content

router = APIRouter(prefix="/api")


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
    """Publish `text` immediately. This is a real, public post."""
    validate_content(data.text, [platform])
    try:
        result = _publisher(request, platform).publish(data.text)
    except PublishError as exc:
        raise InvalidInput(str(exc)) from exc
    return asdict(result)


# ---- scheduled posts ------------------------------------------------------

@router.get("/posts")
def list_posts(request: Request, start: AwareDatetime | None = None, end: AwareDatetime | None = None):
    return request.app.state.posts.list(start, end)


@router.post("/posts", status_code=201)
def create_post(data: PostIn, request: Request):
    return request.app.state.posts.create(data.body, data.scheduled_at, data.platforms, data.blog_url)


@router.put("/posts/{post_id}")
def update_post(post_id: int, data: PostIn, request: Request):
    return request.app.state.posts.update(post_id, data.body, data.scheduled_at, data.platforms, data.blog_url)


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int, request: Request):
    request.app.state.posts.delete(post_id)


@router.post("/posts/{post_id}/retry")
def retry_post(post_id: int, request: Request):
    return request.app.state.posts.retry(post_id)


# ---- blog -----------------------------------------------------------------

@router.get("/blog/posts")
def list_blog_posts(request: Request):
    return request.app.state.blog.list()


@router.post("/blog/sync")
def sync_blog(request: Request):
    return request.app.state.blog.sync()


@router.post("/blog/plan", status_code=201)
def plan_blog(data: BlogPlanIn, request: Request):
    return request.app.state.blog.plan(data.urls, data.start, data.every_days, data.platforms)
