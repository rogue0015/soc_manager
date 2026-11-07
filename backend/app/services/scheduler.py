"""Background publisher.

Every `interval` seconds it sends whatever is due. It runs inside the API
process, so posts only go out while the server is running; anything missed
while it was off is sent on the next start. See docs/decisions/0001-architecture.md.
"""

from __future__ import annotations

import asyncio
import logging

from ..platforms import PLATFORMS, PublishError, Publisher
from .posts import PostService

log = logging.getLogger(__name__)


def publish_due(posts: PostService, publishers: dict[str, Publisher]) -> int:
    """Send every due target once. Returns how many were attempted."""
    due = posts.claim_due()
    for target in due:
        platform = target["platform"]
        publisher = publishers.get(platform)
        if publisher is None:
            name = PLATFORMS[platform].name if platform in PLATFORMS else platform
            posts.mark_failed(target["id"], f"{name} is not connected yet.")
            continue
        try:
            result = publisher.publish(target["body"])
        except PublishError as exc:
            posts.mark_failed(target["id"], str(exc))
            log.warning("Publishing post %s to %s failed: %s", target["post_id"], platform, exc)
        except Exception as exc:  # never let one bad post stop the loop
            posts.mark_failed(target["id"], f"Unexpected error: {exc}")
            log.exception("Unexpected error publishing post %s to %s", target["post_id"], platform)
        else:
            posts.mark_published(target["id"], result.external_id, result.external_url)
            log.info("Published post %s to %s", target["post_id"], platform)
    return len(due)


async def run_scheduler(posts: PostService, publishers: dict[str, Publisher],
                        interval: int, stop: asyncio.Event) -> None:
    log.info("Scheduler started (checking every %ss)", interval)
    while not stop.is_set():
        try:
            await asyncio.to_thread(publish_due, posts, publishers)
        except Exception:
            log.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
