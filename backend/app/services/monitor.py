"""System monitor: one snapshot of the app's health for the Status tab.

Reads only local state (database, scheduler, settings); it never calls a
platform, so it is cheap enough to poll. Live platform checks stay on
GET /api/platforms/{id}/status. `attention` turns the facts into plain
sentences, so the UI doesn't have to know what counts as a problem.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..db import Database
from ..platforms import PLATFORMS, Publisher
from ..timeutil import now_utc, to_utc_iso
from .activity import plural
from .scheduler import SchedulerState

STATUSES = ("scheduled", "publishing", "published", "failed")


class MonitorService:
    def __init__(self, db: Database, settings: Settings, scheduler: SchedulerState,
                 publishers: dict[str, Publisher], started_at: datetime):
        self.db = db
        self.settings = settings
        self.scheduler = scheduler
        self.publishers = publishers
        self.started_at = started_at

    def snapshot(self) -> dict[str, Any]:
        now = now_utc()
        with self.db.connect() as conn:
            counts = dict(conn.execute("SELECT status, COUNT(*) FROM post_targets GROUP BY status").fetchall())
            next_due = conn.execute(
                "SELECT MIN(p.scheduled_at) FROM posts p JOIN post_targets t ON t.post_id = p.id "
                "WHERE t.status = 'scheduled'"
            ).fetchone()[0]
            blog_count, blog_synced = conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM blog_posts").fetchone()
        queue = {s: counts.get(s, 0) for s in STATUSES}
        queue["next_due_at"] = next_due
        db_path = self.settings.db_path
        snapshot = {
            "started_at": to_utc_iso(self.started_at),
            "uptime_seconds": int((now - self.started_at).total_seconds()),
            "scheduler": {
                "enabled": self.scheduler.enabled,
                "interval_seconds": self.scheduler.interval_seconds,
                "last_run_at": self.scheduler.last_run_at,
                "last_attempted": self.scheduler.last_attempted,
                "last_error": self.scheduler.last_error,
            },
            "queue": queue,
            "blog": {"posts": blog_count, "last_synced_at": blog_synced},
            "platforms": {"known": len(PLATFORMS), "connected": len(self.publishers)},
            "database": {"size_bytes": db_path.stat().st_size if db_path.exists() else 0},
        }
        snapshot["attention"] = self._attention(snapshot, now)
        return snapshot

    def _attention(self, s: dict[str, Any], now: datetime) -> list[str]:
        """Problems worth the user's attention, as plain sentences. Empty means all is well."""
        notes = []
        sched, queue = s["scheduler"], s["queue"]
        if not sched["enabled"]:
            notes.append("The scheduler is off (SM_SCHEDULER=off), so scheduled posts won't be sent.")
        if sched["last_error"]:
            notes.append(f"The last scheduler check failed: {sched['last_error']}")
        if sched["enabled"] and sched["last_run_at"]:
            late = now - datetime.fromisoformat(sched["last_run_at"])
            if late > timedelta(seconds=sched["interval_seconds"] * 3 + 30):
                notes.append("The scheduler hasn't checked for due posts in a while. Restart the backend.")
        if queue["failed"]:
            notes.append(f"{plural(queue['failed'], 'post')} failed to publish. "
                         "Open them on the Scheduler tab to retry.")
        due = queue["next_due_at"]
        if due and datetime.fromisoformat(due) < now - timedelta(minutes=5):
            notes.append("Some scheduled posts are overdue and haven't been sent yet.")
        if not s["platforms"]["connected"]:
            notes.append("No platform is connected, so nothing can be published. Add keys to the env file.")
        return notes
