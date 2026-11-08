"""Request bodies for the API. Responses are plain dicts from the services."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

PostText = Annotated[str, Field(max_length=70000)]


class TargetOptionsIn(BaseModel):
    """Per-platform options. Platforms honour what PlatformInfo.features lists (LinkedIn: all)."""
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    allow_reshare: bool = True
    attachment: Literal["none", "image", "link_card", "video"] = "none"
    image_id: str | None = Field(None, max_length=64)       # from POST /api/media
    image_alt: str = Field("", max_length=300)
    first_comment: str = Field("", max_length=1250)         # LinkedIn's comment limit
    draft: bool = False                                     # dev.to: save a draft instead of publishing
    handoff: bool = False                                   # LinkedIn's own scheduler posts it, not the app
    video_id: str | None = Field(None, max_length=64)       # a video from the media library
    title: str = Field("", max_length=300)                  # video title; each platform checks its own limit
    privacy: Literal["", "public", "unlisted", "private", "friends"] = ""
    tags: str = Field("", max_length=500)                   # comma-separated


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


# ---- AI generation (routes/ai.py, ADR 0012) ----

PersonaText = Annotated[str, Field(max_length=4000)]


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tone: PersonaText = ""
    voice: PersonaText = ""
    language: PersonaText = ""
    format: PersonaText = ""
    hashtags: PersonaText = ""
    emoji: PersonaText = ""
    always: PersonaText = ""
    never: PersonaText = ""
    example: PersonaText = ""


class EventIn(BaseModel):
    """Optional event recap, written into the post."""
    name: str = Field("", max_length=200)
    day: str = Field("", max_length=100)
    venue: str = Field("", max_length=200)
    takeaways: str = Field("", max_length=4000)


class GenerateIn(BaseModel):
    source: Literal["idea", "blog"]
    idea: str = Field("", max_length=8000)       # required for "idea"; extra angle for "blog"
    blog_url: str | None = Field(None, max_length=2000)
    event: EventIn = Field(default_factory=EventIn)
    platforms: list[str] = Field(min_length=1)
    persona_id: int


class RegenerateIn(BaseModel):
    platform: str


class DraftUsedIn(BaseModel):
    post_id: int
