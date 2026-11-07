"""Scheduled posts: storage and state changes.

A post has one body and one time, and a target row per platform.
Target lifecycle:  scheduled -> publishing -> published | failed
                   failed --retry--> scheduled
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from ..platforms import PLATFORMS
from ..timeutil import now_iso, now_utc, to_utc_iso
from .errors import Conflict, InvalidInput, NotFound

# Allow a little clock slack so "post at 09:00" submitted at 09:00:20 still works.
PAST_GRACE = timedelta(minutes=2)


def validate_content(body: str, platforms: list[str]) -> None:
    if not body.strip():
        raise InvalidInput("Post text is empty.")
    if not platforms:
        raise InvalidInput("Choose at least one platform.")
    problems = []
    for pid in platforms:
        info = PLATFORMS.get(pid)
        if info is None:
            raise InvalidInput(f"Unknown platform: {pid}")
        length = info.text_length(body)
        if length > info.char_limit:
            problems.append(f"{info.name} allows {info.char_limit} characters; this post is {length}.")
    if problems:
        raise InvalidInput(" ".join(problems))


def validate_time(scheduled_at: datetime) -> None:
    if scheduled_at < now_utc() - PAST_GRACE:
        raise InvalidInput("That time is in the past. Pick a future time.")


class PostService:
    def __init__(self, db: Database):
        self.db = db

    # ---- reads ---------------------------------------------------------

    def list(self, start: datetime | None = None, end: datetime | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM posts WHERE 1=1", []
        if start:
            sql += " AND scheduled_at >= ?"
            args.append(to_utc_iso(start))
        if end:
            sql += " AND scheduled_at < ?"
            args.append(to_utc_iso(end))
        sql += " ORDER BY scheduled_at, id"
        with self.db.connect() as conn:
            posts = [dict(r) for r in conn.execute(sql, args)]
            self._attach_targets(conn, posts)
        return posts

    def get(self, post_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if row is None:
                raise NotFound(f"Post {post_id} not found")
            post = dict(row)
            self._attach_targets(conn, [post])
        return post

    @staticmethod
    def _attach_targets(conn, posts: list[dict[str, Any]]) -> None:
        if not posts:
            return
        by_id = {p["id"]: p for p in posts}
        for p in posts:
            p["targets"] = []
        marks = ",".join("?" * len(by_id))
        rows = conn.execute(
            f"SELECT * FROM post_targets WHERE post_id IN ({marks}) ORDER BY id", list(by_id)
        )
        for r in rows:
            by_id[r["post_id"]]["targets"].append(dict(r))

    # ---- writes --------------------------------------------------------

    def create(self, body: str, scheduled_at: datetime, platforms: list[str],
               blog_url: str | None = None) -> dict[str, Any]:
        platforms = list(dict.fromkeys(platforms))  # de-duplicate, keep order
        validate_content(body, platforms)
        validate_time(scheduled_at)
        now = now_iso()
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO posts (body, source, blog_url, scheduled_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (body, "blog" if blog_url else "custom", blog_url, to_utc_iso(scheduled_at), now, now),
            )
            post_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO post_targets (post_id, platform) VALUES (?, ?)",
                [(post_id, p) for p in platforms],
            )
        return self.get(post_id)

    def update(self, post_id: int, body: str, scheduled_at: datetime, platforms: list[str],
               blog_url: str | None = None) -> dict[str, Any]:
        post = self.get(post_id)
        if any(t["status"] in ("publishing", "published") for t in post["targets"]):
            raise Conflict("This post has already gone out on at least one platform, so it can't be edited.")
        platforms = list(dict.fromkeys(platforms))
        validate_content(body, platforms)
        validate_time(scheduled_at)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE posts SET body=?, source=?, blog_url=?, scheduled_at=?, updated_at=? WHERE id=?",
                (body, "blog" if blog_url else "custom", blog_url, to_utc_iso(scheduled_at), now_iso(), post_id),
            )
            # Editing resets every target to "scheduled" (clears old failures).
            conn.execute("DELETE FROM post_targets WHERE post_id=?", (post_id,))
            conn.executemany(
                "INSERT INTO post_targets (post_id, platform) VALUES (?, ?)",
                [(post_id, p) for p in platforms],
            )
        return self.get(post_id)

    def delete(self, post_id: int) -> None:
        """Removes the post from the planner. Does NOT delete anything already published."""
        post = self.get(post_id)
        if any(t["status"] == "publishing" for t in post["targets"]):
            raise Conflict("This post is being published right now. Try again in a moment.")
        with self.db.connect() as conn:
            conn.execute("DELETE FROM posts WHERE id=?", (post_id,))

    def retry(self, post_id: int) -> dict[str, Any]:
        """Re-queue failed targets. If the post time has passed they go out on the next scheduler tick."""
        self.get(post_id)
        with self.db.connect() as conn:
            changed = conn.execute(
                "UPDATE post_targets SET status='scheduled', error=NULL WHERE post_id=? AND status='failed'",
                (post_id,),
            ).rowcount
        if not changed:
            raise Conflict("Nothing to retry: no platform failed for this post.")
        return self.get(post_id)

    # ---- used by the scheduler ------------------------------------------

    def claim_due(self) -> list[dict[str, Any]]:
        """Atomically move due targets to 'publishing' so each is sent at most once."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                UPDATE post_targets SET status='publishing'
                WHERE status='scheduled' AND post_id IN (
                    SELECT id FROM posts WHERE scheduled_at <= ?
                )
                RETURNING id, post_id, platform
                """,
                (now_iso(),),
            ).fetchall()
            claimed = [dict(r) for r in rows]
            for t in claimed:
                t["body"] = conn.execute("SELECT body FROM posts WHERE id=?", (t["post_id"],)).fetchone()["body"]
        return claimed

    def mark_published(self, target_id: int, external_id: str, external_url: str | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE post_targets SET status='published', external_id=?, external_url=?, error=NULL, "
                "published_at=? WHERE id=?",
                (external_id, external_url, now_iso(), target_id),
            )

    def mark_failed(self, target_id: int, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE post_targets SET status='failed', error=? WHERE id=?", (error, target_id))

    def recover_interrupted(self) -> int:
        """On startup, anything stuck in 'publishing' was cut off mid-send. We can't know
        whether the platform received it, so mark it failed rather than risk a duplicate."""
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE post_targets SET status='failed', "
                "error='Interrupted while publishing. Check the platform before retrying.' "
                "WHERE status='publishing'"
            ).rowcount
