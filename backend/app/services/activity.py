"""Activity log: a short history of what the app did, in plain sentences.

Feeds "Recent activity" on the Status tab. The services write an entry as
things happen, because some events (a deletion, a blog sync) leave nothing
else behind to read later. Only the newest KEEP entries are kept.
See docs/decisions/0004-tabs-status-analytics.md.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..platforms import PLATFORMS
from ..timeutil import now_iso

KEEP = 1000
SNIPPET_WIDTH = 60


def snippet(body: str) -> str:
    """The first line of a post, shortened and quoted, to name it in a sentence."""
    line = body.strip().split("\n", 1)[0].strip()
    if len(line) > SNIPPET_WIDTH:
        line = line[: SNIPPET_WIDTH - 1].rstrip() + "…"
    return f"“{line}”"


def plural(n: int, word: str) -> str:
    """'1 post', '3 posts'."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def platform_names(ids: list[str]) -> str:
    names = [PLATFORMS[i].name if i in PLATFORMS else i for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class ActivityLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, kind: str, message: str, *, platform: str | None = None,
               post_id: int | None = None, url: str | None = None) -> None:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO activity (at, kind, platform, post_id, message, url) VALUES (?, ?, ?, ?, ?, ?)",
                (now_iso(), kind, platform, post_id, message, url),
            )
            conn.execute("DELETE FROM activity WHERE id <= ?", (cur.lastrowid - KEEP,))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
