"""Analytics: how your posts did, filtered by time, platform, source and status.

"Performance" here is what this app can see: what was published, what
failed and why, per platform and over time, plus each post's statistics
(impressions, reactions, ...) where they were imported from LinkedIn's
analytics file or fetched from its API (post_metrics; see ADR 0006).

Each platform delivery (a post_targets row) is counted on its own, dated by
when it was published, or by its scheduled time if it hasn't been. The
response also lists the matching posts, so the browser can group them by
day or week in local time (the same way the calendar groups by local day).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from ..db import Database
from ..platforms import METRICS, PLATFORMS
from ..timeutil import to_utc_iso
from .errors import InvalidInput

STATUSES = ("scheduled", "publishing", "published", "failed")
SOURCES = ("custom", "blog")
TOP_REASONS = 5


def _rate(published: int, failed: int) -> float | None:
    """Share of finished deliveries that worked. None when nothing has finished."""
    done = published + failed
    return round(published / done, 4) if done else None


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(r["status"] for r in rows)
    out: dict[str, Any] = {s: by_status.get(s, 0) for s in STATUSES}
    out["success_rate"] = _rate(out["published"], out["failed"])
    out["stats"] = _stat_totals(rows)
    return out


def _stat_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Sum of each statistic over the deliveries that have one; `posts` = how many have stats."""
    with_stats = [r for r in rows if r["stats_source"]]
    totals = {k: sum(r[k] for r in with_stats if r[k] is not None)
              for k in METRICS if any(r[k] is not None for r in with_stats)}
    return {"posts": len(with_stats), **totals}


class AnalyticsService:
    def __init__(self, db: Database):
        self.db = db

    def report(self, start: datetime | None = None, end: datetime | None = None,
               platform: str | None = None, source: str | None = None,
               status: str | None = None) -> dict[str, Any]:
        if platform is not None and platform not in PLATFORMS:
            raise InvalidInput(f"Unknown platform: {platform}")
        if source is not None and source not in SOURCES:
            raise InvalidInput("Source must be 'custom' or 'blog'.")
        if status is not None and status not in STATUSES:
            raise InvalidInput(f"Status must be one of: {', '.join(STATUSES)}.")
        if start and end and start >= end:
            raise InvalidInput("The start of the range must be before its end.")

        # Both times are stored in one UTC ISO format, so text comparison orders them correctly.
        sql = f"""
            SELECT t.id, t.post_id, t.platform, t.status, t.external_url, t.error, t.published_at,
                   t.warning, t.removed_at, t.edited_at, COALESCE(t.body_override, p.body) AS text,
                   p.body, p.source, p.blog_url, p.scheduled_at,
                   COALESCE(t.published_at, p.scheduled_at) AS happened_at,
                   m.source AS stats_source, m.updated_at AS stats_updated_at, {", ".join(f"m.{k}" for k in METRICS)}
            FROM post_targets t JOIN posts p ON p.id = t.post_id
            LEFT JOIN post_metrics m ON m.target_id = t.id
            WHERE 1 = 1"""
        args: list[Any] = []
        for clause, value in (
            ("COALESCE(t.published_at, p.scheduled_at) >= ?", start and to_utc_iso(start)),
            ("COALESCE(t.published_at, p.scheduled_at) < ?", end and to_utc_iso(end)),
            ("t.platform = ?", platform),
            ("p.source = ?", source),
            ("t.status = ?", status),
        ):
            if value:
                sql += f" AND {clause}"
                args.append(value)
        sql += " ORDER BY happened_at DESC, t.id"
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, args)]

        posts: dict[int, dict[str, Any]] = {}
        for r in rows:
            post = posts.setdefault(r["post_id"], {
                "id": r["post_id"], "body": r["body"], "source": r["source"],
                "blog_url": r["blog_url"], "scheduled_at": r["scheduled_at"], "targets": [],
            })
            post["targets"].append({
                "platform": r["platform"], "status": r["status"], "external_url": r["external_url"],
                "error": r["error"], "published_at": r["published_at"], "happened_at": r["happened_at"],
                "warning": r["warning"], "removed_at": r["removed_at"], "edited_at": r["edited_at"],
                "text": r["text"],
                "metrics": ({"source": r["stats_source"], "updated_at": r["stats_updated_at"],
                             **{k: r[k] for k in METRICS if r[k] is not None}} if r["stats_source"] else None),
            })

        reasons = Counter(r["error"] for r in rows if r["status"] == "failed" and r["error"])
        return {
            "totals": {**_counts(rows), "posts": len(posts)},
            "by_platform": [
                {"platform": pid, **_counts(group)}
                for pid in PLATFORMS
                if (group := [r for r in rows if r["platform"] == pid])
            ],
            "failure_reasons": [{"error": e, "count": n} for e, n in reasons.most_common(TOP_REASONS)],
            "posts": list(posts.values()),
        }
