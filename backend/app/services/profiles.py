"""Who you're signed in as on each connected platform, for the Social tab's sidebar and profile card.

Asks the connected platforms in parallel and keeps each answer for 10 minutes (a
problem for 1 minute), so opening the tab again doesn't call the platforms again.
Only public profile details are returned (name, handle, picture, counts, profile
link); nothing is stored. See docs/decisions/0010-post-now-and-profiles.md.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from ..platforms import PLATFORMS, PublishError, Publisher

log = logging.getLogger(__name__)
KEEP_PROFILE = 600
KEEP_PROBLEM = 60


class ProfileService:
    def __init__(self, publishers: dict[str, Publisher], clock: Callable[[], float] = time.monotonic):
        self.publishers = publishers   # the shared dict: a sign-in can replace an adapter
        self.clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def all(self, refresh: bool = False) -> list[dict[str, Any]]:
        """One entry per connected platform, in registry order: {platform, profile, error}."""
        ids = [pid for pid in PLATFORMS if pid in self.publishers]
        with ThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
            return list(pool.map(lambda pid: self.one(pid, refresh), ids))

    def one(self, platform: str, refresh: bool = False) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            cached = self._cache.get(platform)
        if cached and not refresh:
            at, entry = cached
            if now - at < (KEEP_PROFILE if entry["profile"] else KEEP_PROBLEM):
                return entry
        entry = self._ask(platform)
        with self._lock:
            self._cache[platform] = (now, entry)
        return entry

    def _ask(self, platform: str) -> dict[str, Any]:
        name = PLATFORMS[platform].name if platform in PLATFORMS else platform
        publisher = self.publishers.get(platform)
        if publisher is None:
            return {"platform": platform, "profile": None, "error": f"{name} isn't connected."}
        try:
            return {"platform": platform, "profile": asdict(publisher.profile()), "error": None}
        except PublishError as exc:
            return {"platform": platform, "profile": None, "error": str(exc)}
        except Exception:  # one platform's surprise shouldn't break the others
            log.exception("Reading the %s profile failed", platform)
            return {"platform": platform, "profile": None, "error": f"Couldn't load your {name} profile."}
