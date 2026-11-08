"""Background publisher.

Every `interval` seconds it sends whatever is due. It runs inside the API
process, so posts only go out while the server is running; anything missed
while it was off is sent on the next start. See docs/decisions/0001-architecture.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..platforms import PLATFORMS, PublishError, Publisher
from ..timeutil import now_iso
from .attachments import AttachmentError, Attachments
from .errors import InvalidInput
from .posts import PostService

log = logging.getLogger(__name__)


@dataclass
class SchedulerState:
    """What the background publisher last did. Read by the Status tab (services/monitor.py)."""
    enabled: bool
    interval_seconds: int
    last_run_at: str | None = None   # UTC ISO time of the last finished check
    last_attempted: int = 0          # posts it tried to send in that check
    last_error: str | None = None    # why the last check crashed, if it did
    stats_refreshed_at: str | None = None   # last time post statistics were fetched from the platforms
    stats_problem: str | None = None        # what went wrong then, if anything


def publish_due(posts: PostService, publishers: dict[str, Publisher],
                attachments: Attachments | None = None) -> int:
    """Send every due target once, with its image / link card / options. Returns how many were attempted."""
    posts.settle_handoffs()   # posted by the platform's own scheduler; nothing to send
    due = posts.claim_due()
    send_targets(posts, publishers, attachments, due)
    return len(due)


def send_targets(posts: PostService, publishers: dict[str, Publisher], attachments: Attachments | None,
                 claimed: list[dict]) -> None:
    """Publish claimed targets (from claim_due, or claim_post for "Post now") and record each outcome."""
    attachments = attachments or Attachments(posts.db, media=None)
    for target in claimed:
        platform = target["platform"]
        publisher = publishers.get(platform)
        if publisher is None:
            name = PLATFORMS[platform].name if platform in PLATFORMS else platform
            posts.mark_failed(target["id"], f"{name} is not connected yet.")
            continue
        try:
            extras, warning = attachments.extras_for(target)
            result = publisher.publish(target["body"], extras)
        except (PublishError, AttachmentError) as exc:
            posts.mark_failed(target["id"], str(exc))
            log.warning("Publishing post %s to %s failed: %s", target["post_id"], platform, exc)
        except Exception as exc:  # never let one bad post stop the loop
            posts.mark_failed(target["id"], f"Unexpected error: {exc}")
            log.exception("Unexpected error publishing post %s to %s", target["post_id"], platform)
        else:
            note = " ".join(w for w in (warning, result.warning) if w) or None
            posts.mark_published(target["id"], result.external_id, result.external_url, note)
            log.info("Published post %s to %s", target["post_id"], platform)


async def refresh_stats_now(refresh_stats: Callable[[], dict], state: SchedulerState) -> None:
    """Fetch post statistics from every platform that shares them. Quiet when none does."""
    try:
        result = await asyncio.to_thread(refresh_stats)
    except InvalidInput:   # no connected platform shares statistics: nothing to do
        return
    except Exception as exc:   # a failed refresh must never stop the publisher
        state.stats_problem = str(exc) or type(exc).__name__
        log.exception("Refreshing post statistics failed")
        return
    state.stats_refreshed_at = now_iso()
    state.stats_problem = " ".join(result.get("problems") or [])[:500] or None


async def run_scheduler(posts: PostService, publishers: dict[str, Publisher],
                        interval: int, stop: asyncio.Event, state: SchedulerState | None = None,
                        attachments: Attachments | None = None,
                        refresh_stats: Callable[[], dict] | None = None, stats_every: int = 1800) -> None:
    """Every `interval` seconds send what's due; every `stats_every` seconds also fetch post statistics,
    so the Analytics tab shows current numbers without anyone pressing Refresh."""
    state = state or SchedulerState(enabled=True, interval_seconds=interval)
    log.info("Scheduler started (checking every %ss)", interval)
    stats_due = 0.0   # monotonic time of the next statistics refresh; 0 = at the first check
    while not stop.is_set():
        if refresh_stats and time.monotonic() >= stats_due:
            stats_due = time.monotonic() + stats_every
            await refresh_stats_now(refresh_stats, state)
        try:
            state.last_attempted = await asyncio.to_thread(publish_due, posts, publishers, attachments)
            state.last_error = None
        except Exception as exc:
            state.last_error = str(exc) or type(exc).__name__
            log.exception("Scheduler tick failed")
        state.last_run_at = now_iso()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
