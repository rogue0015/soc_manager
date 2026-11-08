"""Platform registry: the single list of platforms the app knows about.

`publisher` is None until a platform has a working adapter. Posts can still
be planned for those platforms; they fail with a clear message when due.
"""

from __future__ import annotations

from . import instagram, linkedin, tiktok, youtube
from .base import (
    METRICS, AccountProfile, AccountStatus, Article, ImageFile, LinkCard, PlatformInfo, PostExtras, PublishError,
    PublishResult, Publisher, SignInError, VideoFile,
)
from .bluesky import BlueskyPublisher
from .devto import DevtoPublisher
from .instagram import InstagramPublisher
from .linkedin import LinkedInPublisher
from .tiktok import TikTokPublisher
from .x import XPublisher
from .youtube import YouTubePublisher

PLATFORMS: dict[str, PlatformInfo] = {
    p.id: p
    for p in [
        PlatformInfo("x", "X (Twitter)", "#1d9bf0", "#ffffff", 280, url_weight=23,
                     image_formats=(("Landscape", 1600, 900), ("Square", 1080, 1080))),
        PlatformInfo("linkedin", "LinkedIn", "#0f766e", "#ffffff", 3000,
                     features=("edit", "delete", "image", "link_card", "first_comment", "visibility", "stats",
                               "handoff", "stats_import", "video"),
                     image_formats=(("Landscape", 1200, 627), ("Square", 1080, 1080), ("Portrait", 1080, 1350)),
                     video_min_ms=3_000, video_max_ms=30 * 60_000, video_max_bytes=500 * 1024 * 1024,
                     # LinkedIn's post box. {text} is filled in when it's honoured; the app also copies the text.
                     compose_url="https://www.linkedin.com/feed/?shareActive=true&text={text}"),
        # Takes the whole blog post, not the post text, so the text limit is only the API's own cap.
        PlatformInfo("devto", "dev.to", "#65a30d", "#0b1a00", 70000, features=("article", "draft", "stats")),
        PlatformInfo("facebook", "Facebook", "#4338ca", "#ffffff", 63206),
        # Reels only: Instagram fetches photos from a public web address, which this app doesn't have.
        PlatformInfo("instagram", "Instagram", "#db2777", "#ffffff", 2200, features=("video", "video_only"),
                     image_formats=(("Portrait", 1080, 1350), ("Square", 1080, 1080), ("Landscape", 1080, 566))),
        PlatformInfo("threads", "Threads", "#525252", "#ffffff", 500),
        # 300 is Bluesky's limit in graphemes; counting characters is never more lenient.
        PlatformInfo("bluesky", "Bluesky", "#f59e0b", "#1f1300", 300,
                     features=("link_card", "delete", "stats", "image", "video"),
                     image_formats=(("Landscape", 1600, 900), ("Square", 1080, 1080), ("Portrait", 1080, 1350)),
                     image_max_bytes=1_000_000,    # Bluesky refuses pictures over 1 MB
                     video_max_ms=3 * 60_000, video_max_bytes=100 * 1024 * 1024),
        # The post text becomes the video's description; the title is its own field.
        PlatformInfo("youtube", "YouTube", "#be123c", "#ffffff", 5000, title_limit=100,
                     features=("video", "video_only", "stats"),
                     image_formats=(("Thumbnail", 1280, 720),)),
        # TikTok sets the length limit per account (often 10 minutes); the adapter asks it before posting.
        PlatformInfo("tiktok", "TikTok", "#0e7490", "#ffffff", 2200, features=("video", "video_only"),
                     video_min_ms=3_000, video_max_ms=10 * 60_000),
        PlatformInfo("mastodon", "Mastodon", "#7c3aed", "#ffffff", 500),
    ]
}


ADAPTERS: dict[str, type[Publisher]] = {
    "x": XPublisher, "linkedin": LinkedInPublisher, "devto": DevtoPublisher, "bluesky": BlueskyPublisher,
    "youtube": YouTubePublisher, "tiktok": TikTokPublisher, "instagram": InstagramPublisher,
}

# Platforms you sign in to from the app (browser OAuth). Each module provides
# sign_in_url(secrets, redirect_uri, state) and finish_sign_in(secrets, code, redirect_uri).
SIGN_IN = {"linkedin": linkedin, "youtube": youtube, "tiktok": tiktok, "instagram": instagram}


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
    "PLATFORMS", "SIGN_IN", "build_publisher", "build_publishers", "AccountProfile", "AccountStatus", "PlatformInfo",
    "PublishError", "PublishResult", "Publisher", "SignInError", "VideoFile", "PostExtras", "ImageFile", "LinkCard", "Article", "METRICS",
]
