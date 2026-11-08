"""The Social tab: one platform at a time: what's coming up, what went out, and how it did.

History and totals for the chosen period come from the analytics report, so the
numbers match the Analytics tab and the downloadable reports. The upcoming queue
has no end date, so it is read on its own. See docs/decisions/0008-social-tab-handoff-reports.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..db import Database
from ..platforms import PLATFORMS
from .analytics import AnalyticsService, deliveries, top_posts
from .errors import NotFound

MAX_UPCOMING = 100


class SocialService:
    def __init__(self, db: Database, analytics: AnalyticsService):
        self.db = db
        self.analytics = analytics

    def overview(self, platform: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise NotFound(f"Unknown platform: {platform}")
        report = self.analytics.report(start, end, platform=platform)
        history = [d for d in deliveries(report) if d["status"] in ("published", "failed")]
        return {
            "platform": platform,
            "totals": report["totals"],
            "upcoming": self._upcoming(platform),
            "history": history,
            "top": top_posts(history),
        }

    def _upcoming(self, platform: str) -> list[dict[str, Any]]:
        """Everything still to go out on the platform, soonest first, whatever its date."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.post_id, t.status, t.options, COALESCE(t.body_override, p.body) AS text, "
                "p.scheduled_at, p.source, p.blog_url "
                "FROM post_targets t JOIN posts p ON p.id = t.post_id "
                "WHERE t.platform = ? AND t.status IN ('scheduled', 'publishing') "
                "ORDER BY p.scheduled_at, t.id LIMIT ?",
                (platform, MAX_UPCOMING),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["handoff"] = bool(json.loads(item.pop("options") or "{}").get("handoff"))
            out.append(item)
        return out
