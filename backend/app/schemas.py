"""Request bodies for the API. Responses are plain dicts from the services."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

PostText = Annotated[str, Field(max_length=70000)]


class TargetOptionsIn(BaseModel):
    """Per-platform options. Platforms honour what PlatformInfo.features lists (LinkedIn: all)."""
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    allow_reshare: bool = True
    attachment: Literal["none", "image", "link_card"] = "none"
    image_id: str | None = Field(None, max_length=64)       # from POST /api/media
    image_alt: str = Field("", max_length=300)
    first_comment: str = Field("", max_length=1250)         # LinkedIn's comment limit
    draft: bool = False                                     # dev.to: save a draft instead of publishing
    handoff: bool = False                                   # LinkedIn's own scheduler posts it, not the app


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=70000)
    scheduled_at: AwareDatetime  # must include a timezone offset, e.g. 2026-11-09T09:00:00+02:00
    platforms: list[str] = Field(min_length=1)
    blog_url: str | None = None
    variants: dict[str, PostText] = Field(default_factory=dict)       # platform -> its own text
    options: dict[str, TargetOptionsIn] = Field(default_factory=dict)  # platform -> options


class TestPostIn(BaseModel):
    text: str = Field(min_length=1, max_length=70000)


class LiveEditIn(BaseModel):
    text: str = Field(min_length=1, max_length=70000)


class BlogPlanIn(BaseModel):
    urls: list[str] = Field(min_length=1)
    start: AwareDatetime
    every_days: int = Field(ge=1, le=60)
    platforms: list[str] = Field(min_length=1)
