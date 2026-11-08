"""Scheduled posts: storage and state changes.

A post has one main text and one time, and a target row per platform. A
target can carry its own text (body_override, e.g. a longer LinkedIn version)
and options as JSON (visibility, reposts, image, link card, first comment,
draft); which options a platform honours is listed in PlatformInfo.features.
Platforms with the "article" feature (dev.to) publish the blog post itself
instead of the text, so they only accept posts made from a blog post.

Hand-off (option `handoff`, LinkedIn): the platform's own scheduler posts it,
because LinkedIn's API only publishes immediately. The app keeps the post on
its calendar but never sends it; once its time has passed it is recorded as
published (settle_handoffs). See docs/decisions/0008-social-tab-handoff-reports.md.
Imported deliveries (imported_at) are past posts read from a platform's export.

Target lifecycle:  scheduled -> publishing -> published | failed
                   failed --retry--> scheduled
A published target can later be edited (edited_at) or deleted on the
platform (removed_at) through services/live.py, and gets statistics in
post_metrics (services/stats_import.py, services/live.py).
Every change is also written to the activity log (services/activity.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from ..platforms import METRICS, PLATFORMS
from ..platforms.base import URL_RE
from ..timeutil import now_iso, now_utc, to_utc_iso
from .activity import ActivityLog, platform_names, plural, snippet
from .errors import Conflict, InvalidInput, NotFound

# Allow a little clock slack so "post at 09:00" submitted at 09:00:20 still works.
PAST_GRACE = timedelta(minutes=2)

DEFAULT_OPTIONS: dict[str, Any] = {
    "visibility": "PUBLIC",        # PUBLIC | CONNECTIONS
    "allow_reshare": True,
    "attachment": "none",          # none | image | link_card
    "image_id": None,
    "image_alt": "",
    "first_comment": "",
    "draft": False,                # save as a draft on the platform (dev.to) instead of publishing
    "handoff": False,              # the platform's own scheduler posts it (LinkedIn); the app doesn't send it
}
# Which feature each non-default option needs, and the words for the error message.
OPTION_NEEDS = {
    "visibility": ("visibility", "PUBLIC", "limit who sees a post"),
    "allow_reshare": ("visibility", True, "turn off reposts"),
    "first_comment": ("first_comment", "", "add a first comment"),
    "draft": ("draft", False, "save a draft"),
    "handoff": ("handoff", False, "schedule posts on its own side"),
}
# Shared by the SQL below: is this delivery handed to the platform's own scheduler?
HANDOFF_SQL = "COALESCE(json_extract(options, '$.handoff'), 0)"
ATTACHMENTS = {"image": "attach an image", "link_card": "show a link preview"}


def validate_content(body: str, platforms: list[str]) -> None:
    """One text for all platforms (test posts, blog plans)."""
    validate_targets(body, platforms, {})


def text_for(platform: str, body: str, variants: dict[str, str] | None) -> str:
    """The text a platform gets: its own version if one was written, else the main text."""
    own = (variants or {}).get(platform)
    return own if own and own.strip() else body


def validate_targets(body: str, platforms: list[str], variants: dict[str, str] | None) -> None:
    if not body.strip():
        raise InvalidInput("Post text is empty.")
    if not platforms:
        raise InvalidInput("Choose at least one platform.")
    problems = []
    for pid in platforms:
        info = PLATFORMS.get(pid)
        if info is None:
            raise InvalidInput(f"Unknown platform: {pid}")
        text = text_for(pid, body, variants)
        length = info.text_length(text)
        if length > info.char_limit:
            which = "this post" if text is body else f"the {info.name} version"
            problems.append(f"{info.name} allows {info.char_limit} characters; {which} is {length}.")
    if problems:
        raise InvalidInput(" ".join(problems))


def article_platforms(platforms: list[str]) -> list[str]:
    """The chosen platforms that publish whole blog posts rather than the post text."""
    return [p for p in platforms if p in PLATFORMS and "article" in PLATFORMS[p].features]


def validate_source(platforms: list[str], blog_url: str | None) -> None:
    """Article platforms (dev.to) publish the blog post itself, so the post must be made from one."""
    names = [PLATFORMS[p].name for p in article_platforms(platforms)]
    if names and not blog_url:
        name = " and ".join(names)
        raise InvalidInput(f"{name} publishes whole blog posts, so it only works with “From my blog”. "
                           f"Pick a blog post, or untick {name}.")


def validate_time(scheduled_at: datetime) -> None:
    if scheduled_at < now_utc() - PAST_GRACE:
        raise InvalidInput("That time is in the past. Pick a future time.")


class PostService:
    def __init__(self, db: Database, activity: ActivityLog | None = None,
                 image_exists: Callable[[str | None], bool] | None = None):
        self.db = db
        self.activity = activity or ActivityLog(db)
        self.image_exists = image_exists or (lambda _id: False)

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
            f"""SELECT t.*, m.source AS stats_source, m.updated_at AS stats_updated_at,
                       {", ".join(f"m.{k}" for k in METRICS)}
                FROM post_targets t LEFT JOIN post_metrics m ON m.target_id = t.id
                WHERE t.post_id IN ({marks}) ORDER BY t.id""",
            list(by_id),
        )
        for r in rows:
            post = by_id[r["post_id"]]
            post["targets"].append(target_dict(dict(r), post["body"]))

    # ---- writes --------------------------------------------------------

    def _checked_options(self, body: str, platforms: list[str], variants: dict[str, str] | None,
                         options: dict[str, dict[str, Any]] | None, blog_url: str | None) -> dict[str, dict]:
        """Fill in defaults and check each platform supports what was asked of it."""
        out: dict[str, dict] = {}
        for pid, raw in (options or {}).items():
            if pid not in platforms:
                continue
            info = PLATFORMS[pid]
            opts = {**DEFAULT_OPTIONS, **{k: v for k, v in raw.items() if k in DEFAULT_OPTIONS}}
            for key, (feature, off, words) in OPTION_NEEDS.items():
                if opts[key] != off and feature not in info.features:
                    raise InvalidInput(f"{info.name} can't {words} from the app.")
            kind = opts["attachment"]
            if kind != "none":
                if kind not in ATTACHMENTS or kind not in info.features:
                    raise InvalidInput(f"{info.name} can't {ATTACHMENTS.get(kind, 'add that')} from the app.")
                if kind == "image" and not self.image_exists(opts["image_id"]):
                    raise InvalidInput("The attached image isn't available any more. Attach it again.")
                if kind == "link_card" and not (blog_url or URL_RE.search(text_for(pid, body, variants))):
                    raise InvalidInput(f"A link preview needs a link in the {info.name} text, or a blog post.")
            if opts["handoff"] and (kind != "none" or opts["first_comment"]):
                raise InvalidInput(f"{info.name}'s own scheduler only gets the text from this app. "
                                   f"Add images, link previews and a first comment in {info.name} instead.")
            out[pid] = opts
        return out

    @staticmethod
    def _insert_targets(conn, post_id: int, body: str, platforms: list[str],
                        variants: dict[str, str] | None, options: dict[str, dict]) -> None:
        rows = []
        for p in platforms:
            own = text_for(p, body, variants)
            rows.append((post_id, p, own if own is not body and own != body else None, json.dumps(options.get(p, {}))))
        conn.executemany("INSERT INTO post_targets (post_id, platform, body_override, options) VALUES (?, ?, ?, ?)",
                         rows)

    def create(self, body: str, scheduled_at: datetime, platforms: list[str],
               blog_url: str | None = None, log: bool = True,
               variants: dict[str, str] | None = None,
               options: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """`log=False` lets a caller that creates many posts record one summary instead."""
        platforms = list(dict.fromkeys(platforms))  # de-duplicate, keep order
        validate_targets(body, platforms, variants)
        validate_source(platforms, blog_url)
        validate_time(scheduled_at)
        checked = self._checked_options(body, platforms, variants, options, blog_url)
        now = now_iso()
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO posts (body, source, blog_url, scheduled_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (body, "blog" if blog_url else "custom", blog_url, to_utc_iso(scheduled_at), now, now),
            )
            post_id = cur.lastrowid
            self._insert_targets(conn, post_id, body, platforms, variants, checked)
        if log:
            self.activity.record("scheduled", f"Scheduled {snippet(body)} for {platform_names(platforms)}.",
                                 post_id=post_id)
        return self.get(post_id)

    def update(self, post_id: int, body: str, scheduled_at: datetime, platforms: list[str],
               blog_url: str | None = None, variants: dict[str, str] | None = None,
               options: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        post = self.get(post_id)
        if any(t["status"] in ("publishing", "published") for t in post["targets"]):
            raise Conflict("This post has already gone out on at least one platform, so it can't be edited here. "
                           "Use “Edit on LinkedIn” for the live LinkedIn post.")
        platforms = list(dict.fromkeys(platforms))
        validate_targets(body, platforms, variants)
        validate_source(platforms, blog_url)
        validate_time(scheduled_at)
        checked = self._checked_options(body, platforms, variants, options, blog_url)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE posts SET body=?, source=?, blog_url=?, scheduled_at=?, updated_at=? WHERE id=?",
                (body, "blog" if blog_url else "custom", blog_url, to_utc_iso(scheduled_at), now_iso(), post_id),
            )
            # Editing resets every target to "scheduled" (clears old failures).
            conn.execute("DELETE FROM post_targets WHERE post_id=?", (post_id,))
            self._insert_targets(conn, post_id, body, platforms, variants, checked)
        self.activity.record("edited", f"Edited {snippet(body)}.", post_id=post_id)
        return self.get(post_id)

    def delete(self, post_id: int) -> None:
        """Removes the post from the planner. Does NOT delete anything already published."""
        post = self.get(post_id)
        if any(t["status"] == "publishing" for t in post["targets"]):
            raise Conflict("This post is being published right now. Try again in a moment.")
        with self.db.connect() as conn:
            conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
        self.activity.record("deleted", f"Removed {snippet(post['body'])} from the planner.", post_id=post_id)

    def retry(self, post_id: int) -> dict[str, Any]:
        """Re-queue failed targets. If the post time has passed they go out on the next scheduler tick."""
        post = self.get(post_id)
        failed = [t["platform"] for t in post["targets"] if t["status"] == "failed"]
        with self.db.connect() as conn:
            changed = conn.execute(
                "UPDATE post_targets SET status='scheduled', error=NULL, warning=NULL "
                "WHERE post_id=? AND status='failed'",
                (post_id,),
            ).rowcount
        if not changed:
            raise Conflict("Nothing to retry: no platform failed for this post.")
        self.activity.record("retried", f"Queued {snippet(post['body'])} again for {platform_names(failed)}.",
                             post_id=post_id)
        return self.get(post_id)

    # ---- used by the scheduler ------------------------------------------

    def claim_due(self) -> list[dict[str, Any]]:
        """Atomically move due targets to 'publishing' so each is sent at most once.
        Each comes with the text, link and options it should be published with."""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                UPDATE post_targets SET status='publishing'
                WHERE status='scheduled' AND {HANDOFF_SQL} = 0 AND post_id IN (
                    SELECT id FROM posts WHERE scheduled_at <= ?
                )
                RETURNING id, post_id, platform, body_override, options
                """,
                (now_iso(),),
            ).fetchall()
            return self._claimed(conn, rows)

    def claim_post(self, post_id: int) -> list[dict[str, Any]]:
        """Post now: move one post's time to now and claim its waiting deliveries, the same atomic
        way as claim_due, so the scheduler and "Post now" can never both send a delivery."""
        now = now_iso()
        with self.db.connect() as conn:
            conn.execute("UPDATE posts SET scheduled_at=?, updated_at=? WHERE id=? AND scheduled_at > ?",
                         (now, now, post_id, now))
            rows = conn.execute(
                f"""
                UPDATE post_targets SET status='publishing'
                WHERE post_id=? AND status='scheduled' AND {HANDOFF_SQL} = 0
                RETURNING id, post_id, platform, body_override, options
                """,
                (post_id,),
            ).fetchall()
            return self._claimed(conn, rows)

    @staticmethod
    def _claimed(conn, rows) -> list[dict[str, Any]]:
        """Claimed deliveries with the text, link and options each should be published with."""
        claimed = []
        for r in rows:
            post = conn.execute("SELECT body, blog_url FROM posts WHERE id=?", (r["post_id"],)).fetchone()
            claimed.append({
                "id": r["id"], "post_id": r["post_id"], "platform": r["platform"],
                "body": r["body_override"] or post["body"], "blog_url": post["blog_url"],
                "options": json.loads(r["options"] or "{}"),
            })
        return claimed

    def settle_handoffs(self) -> int:
        """Posts handed to a platform's own scheduler go out there, not from here. Once their time has
        passed, record them as published so the calendar and analytics count them. Returns how many."""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                UPDATE post_targets
                SET status='published', published_at=(SELECT scheduled_at FROM posts WHERE posts.id = post_id)
                WHERE status='scheduled' AND {HANDOFF_SQL} = 1 AND post_id IN (
                    SELECT id FROM posts WHERE scheduled_at <= ?
                )
                RETURNING id
                """,
                (now_iso(),),
            ).fetchall()
            settled = [self._target(conn, r["id"]) for r in rows]
        for t in settled:
            name = platform_names([t["platform"]])
            self.activity.record(
                "published", f"{snippet(t['body'])} was due on {name}, posted by {name}'s own scheduler. "
                             f"The app can't check it, so look on {name} if in doubt.",
                platform=t["platform"], post_id=t["post_id"])
        return len(settled)

    def _target(self, conn, target_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT t.id, t.platform, t.post_id, t.external_id, t.status, t.removed_at, "
            "COALESCE(t.body_override, p.body) AS body "
            "FROM post_targets t JOIN posts p ON p.id = t.post_id WHERE t.id = ?", (target_id,),
        ).fetchone()
        if row is None:
            raise NotFound("That post isn't in the planner any more.")
        return dict(row)

    def mark_published(self, target_id: int, external_id: str, external_url: str | None,
                       warning: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE post_targets SET status='published', external_id=?, external_url=?, error=NULL, "
                "warning=?, published_at=? WHERE id=?",
                (external_id, external_url, warning, now_iso(), target_id),
            )
            t = self._target(conn, target_id)
        message = f"Published {snippet(t['body'])} on {platform_names([t['platform']])}."
        self.activity.record("published", message + (f" But: {warning}" if warning else ""),
                             platform=t["platform"], post_id=t["post_id"], url=external_url)

    def mark_failed(self, target_id: int, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE post_targets SET status='failed', error=? WHERE id=?", (error, target_id))
            t = self._target(conn, target_id)
        self.activity.record(
            "failed", f"Couldn't publish {snippet(t['body'])} on {platform_names([t['platform']])}: {error}",
            platform=t["platform"], post_id=t["post_id"],
        )

    def recover_interrupted(self) -> int:
        """On startup, anything stuck in 'publishing' was cut off mid-send. We can't know
        whether the platform received it, so mark it failed rather than risk a duplicate."""
        with self.db.connect() as conn:
            count = conn.execute(
                "UPDATE post_targets SET status='failed', "
                "error='Interrupted while publishing. Check the platform before retrying.' "
                "WHERE status='publishing'"
            ).rowcount
        if count:
            self.activity.record(
                "interrupted",
                f"{plural(count, 'post')} {'was' if count == 1 else 'were'} cut off while publishing "
                "and marked failed. Check the platform before retrying.",
            )
        return count

    # ---- live posts (services/live.py) and statistics ---------------------

    def record_published(self, body: str, platform: str, external_id: str, external_url: str | None,
                         warning: str | None = None, published_at: str | None = None) -> dict[str, Any]:
        """Keep a post that went out straight away (a test post) in the planner, so it shows on the
        calendar and in analytics and can be edited or deleted like any other."""
        when = published_at or now_iso()
        with self.db.connect() as conn:
            post_id = conn.execute(
                "INSERT INTO posts (body, source, scheduled_at, created_at, updated_at) VALUES (?, 'custom', ?, ?, ?)",
                (body, when, when, when),
            ).lastrowid
            conn.execute(
                "INSERT INTO post_targets (post_id, platform, status, external_id, external_url, warning, published_at) "
                "VALUES (?, ?, 'published', ?, ?, ?, ?)",
                (post_id, platform, external_id, external_url, warning, when),
            )
        return self.get(post_id)

    def target(self, target_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            return self._target(conn, target_id)

    def mark_removed(self, target_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE post_targets SET removed_at=? WHERE id=?", (now_iso(), target_id))

    def set_live_text(self, target_id: int, text: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE post_targets SET body_override=?, edited_at=? WHERE id=?",
                         (text, now_iso(), target_id))

    def live_targets(self, platform: str) -> list[dict[str, Any]]:
        """Published deliveries on a platform that are still up, newest first."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.post_id, t.external_id, t.published_at, COALESCE(t.body_override, p.body) AS body "
                "FROM post_targets t JOIN posts p ON p.id = t.post_id "
                "WHERE t.platform=? AND t.status='published' AND t.removed_at IS NULL "
                "AND COALESCE(t.external_id, '') != '' ORDER BY t.published_at DESC",
                (platform,),
            ).fetchall()
        return [dict(r) for r in rows]

    def published_targets(self, platform: str) -> list[dict[str, Any]]:
        """Every delivery that is up on a platform, including hand-offs and imported past posts
        (which may have no post ID yet). Used to match rows of an imported analytics file."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.post_id, t.external_id, t.external_url, t.published_at, t.imported_at "
                "FROM post_targets t WHERE t.platform=? AND t.status='published' AND t.removed_at IS NULL "
                "ORDER BY t.published_at DESC",
                (platform,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_imported(self, platform: str, body: str, external_id: str | None, external_url: str,
                        published_at: str) -> int:
        """Add a past post found in a platform's export, so its statistics have a home. Returns the target id."""
        now = now_iso()
        with self.db.connect() as conn:
            post_id = conn.execute(
                "INSERT INTO posts (body, source, scheduled_at, created_at, updated_at) VALUES (?, 'custom', ?, ?, ?)",
                (body, published_at, now, now),
            ).lastrowid
            return conn.execute(
                "INSERT INTO post_targets (post_id, platform, status, external_id, external_url, published_at, "
                "imported_at) VALUES (?, ?, 'published', ?, ?, ?, ?)",
                (post_id, platform, external_id, external_url, published_at, now),
            ).lastrowid

    def set_link(self, target_id: int, external_id: str | None, external_url: str) -> None:
        """Fill in the live post's ID and link when the app didn't get them (a hand-off). Never overwrites."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE post_targets SET external_id=COALESCE(external_id, ?), "
                "external_url=COALESCE(external_url, ?) WHERE id=?",
                (external_id, external_url, target_id),
            )

    def save_metrics(self, target_id: int, source: str, values: dict[str, int | None]) -> bool:
        """Store a post's statistics. An import never overwrites numbers fetched from the API."""
        with self.db.connect() as conn:
            existing = conn.execute("SELECT source FROM post_metrics WHERE target_id=?", (target_id,)).fetchone()
            if existing and existing["source"] == "api" and source == "import":
                return False
            cols = [k for k in METRICS if k in values]
            conn.execute(
                f"INSERT OR REPLACE INTO post_metrics (target_id, source, updated_at, {', '.join(cols)}) "
                f"VALUES (?, ?, ?, {', '.join('?' * len(cols))})",
                (target_id, source, now_iso(), *(values[k] for k in cols)),
            )
        return True


def target_dict(row: dict[str, Any], post_body: str) -> dict[str, Any]:
    """A target as the API returns it: options parsed, effective text, statistics grouped."""
    stats = {k: row.pop(k) for k in METRICS}
    source, updated = row.pop("stats_source", None), row.pop("stats_updated_at", None)
    row["options"] = {**DEFAULT_OPTIONS, **json.loads(row.get("options") or "{}")} if row.get("options") not in (
        None, "{}") else {}
    row["text"] = row.get("body_override") or post_body
    row["metrics"] = ({"source": source, "updated_at": updated, **{k: v for k, v in stats.items() if v is not None}}
                      if source else None)
    return row
