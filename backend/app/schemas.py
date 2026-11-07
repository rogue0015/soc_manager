"""Request bodies for the API. Responses are plain dicts from the services."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=70000)
    scheduled_at: AwareDatetime  # must include a timezone offset, e.g. 2026-11-09T09:00:00+02:00
    platforms: list[str] = Field(min_length=1)
    blog_url: str | None = None


class TestPostIn(BaseModel):
    text: str = Field(min_length=1, max_length=70000)


class BlogPlanIn(BaseModel):
    urls: list[str] = Field(min_length=1)
    start: AwareDatetime
    every_days: int = Field(ge=1, le=60)
    platforms: list[str] = Field(min_length=1)
