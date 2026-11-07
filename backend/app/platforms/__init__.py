"""Platform registry: the single list of platforms the app knows about.

`publisher` is None until a platform has a working adapter. Posts can still
be planned for those platforms; they fail with a clear message when due.
"""

from __future__ import annotations

from .base import AccountStatus, PlatformInfo, PublishError, PublishResult, Publisher
from .x import XPublisher

PLATFORMS: dict[str, PlatformInfo] = {
    p.id: p
    for p in [
        PlatformInfo("x", "X (Twitter)", "#1d9bf0", "#ffffff", 280, url_weight=23),
        PlatformInfo("linkedin", "LinkedIn", "#0f766e", "#ffffff", 3000),
        PlatformInfo("facebook", "Facebook", "#4338ca", "#ffffff", 63206),
        PlatformInfo("instagram", "Instagram", "#db2777", "#ffffff", 2200),
        PlatformInfo("threads", "Threads", "#525252", "#ffffff", 500),
        PlatformInfo("bluesky", "Bluesky", "#f59e0b", "#1f1300", 300),
        PlatformInfo("mastodon", "Mastodon", "#7c3aed", "#ffffff", 500),
    ]
}


def build_publishers(secrets: dict[str, str]) -> dict[str, Publisher]:
    """Create adapters for every platform whose credentials are present."""
    publishers: dict[str, Publisher] = {}
    try:
        publishers["x"] = XPublisher(secrets)
    except ValueError:
        pass  # X credentials not configured; X shows as "not connected".
    return publishers


__all__ = [
    "PLATFORMS", "build_publishers", "AccountStatus", "PlatformInfo",
    "PublishError", "PublishResult", "Publisher",
]
