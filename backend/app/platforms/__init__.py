"""Platform registry: the single list of platforms the app knows about.

`publisher` is None until a platform has a working adapter. Posts can still
be planned for those platforms; they fail with a clear message when due.
"""

from __future__ import annotations

from . import linkedin
from .base import (
    METRICS, AccountStatus, Article, ImageFile, LinkCard, PlatformInfo, PostExtras, PublishError, PublishResult,
    Publisher,
)
from .bluesky import BlueskyPublisher
from .devto import DevtoPublisher
from .linkedin import LinkedInPublisher
from .x import XPublisher

PLATFORMS: dict[str, PlatformInfo] = {
    p.id: p
    for p in [
        PlatformInfo("x", "X (Twitter)", "#1d9bf0", "#ffffff", 280, url_weight=23),
        PlatformInfo("linkedin", "LinkedIn", "#0f766e", "#ffffff", 3000,
                     features=("edit", "delete", "image", "link_card", "first_comment", "visibility", "stats",
                               "handoff", "stats_import"),
                     # LinkedIn's post box. {text} is filled in when it's honoured; the app also copies the text.
                     compose_url="https://www.linkedin.com/feed/?shareActive=true&text={text}"),
        # Takes the whole blog post, not the post text, so the text limit is only the API's own cap.
        PlatformInfo("devto", "dev.to", "#65a30d", "#0b1a00", 70000, features=("article", "draft", "stats")),
        PlatformInfo("facebook", "Facebook", "#4338ca", "#ffffff", 63206),
        PlatformInfo("instagram", "Instagram", "#db2777", "#ffffff", 2200),
        PlatformInfo("threads", "Threads", "#525252", "#ffffff", 500),
        # 300 is Bluesky's limit in graphemes; counting characters is never more lenient.
        PlatformInfo("bluesky", "Bluesky", "#f59e0b", "#1f1300", 300, features=("link_card", "delete", "stats")),
        PlatformInfo("mastodon", "Mastodon", "#7c3aed", "#ffffff", 500),
    ]
}


ADAPTERS: dict[str, type[Publisher]] = {
    "x": XPublisher, "linkedin": LinkedInPublisher, "devto": DevtoPublisher, "bluesky": BlueskyPublisher,
}

# Platforms you sign in to from the app (browser OAuth). Each module provides
# sign_in_url(secrets, redirect_uri, state) and finish_sign_in(secrets, code, redirect_uri).
SIGN_IN = {"linkedin": linkedin}


def build_publisher(platform: str, secrets: dict[str, str]) -> Publisher | None:
    """The adapter for one platform, or None while its credentials are missing."""
    try:
        return ADAPTERS[platform](secrets)
    except ValueError:
        return None


def build_publishers(secrets: dict[str, str]) -> dict[str, Publisher]:
    """Create adapters for every platform whose credentials are present."""
    return {pid: pub for pid in ADAPTERS if (pub := build_publisher(pid, secrets))}


__all__ = [
    "PLATFORMS", "SIGN_IN", "build_publisher", "build_publishers", "AccountStatus", "PlatformInfo",
    "PublishError", "PublishResult", "Publisher", "PostExtras", "ImageFile", "LinkCard", "Article", "METRICS",
]
