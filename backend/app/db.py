"""SQLite storage.

Why SQLite: single user, runs locally, zero setup, one file to back up.
See docs/decisions/0001-architecture.md. Each call opens its own
connection, which keeps things safe across the API and the scheduler thread.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    body          TEXT    NOT NULL,
    source        TEXT    NOT NULL DEFAULT 'custom' CHECK (source IN ('custom', 'blog')),
    blog_url      TEXT,
    scheduled_at  TEXT    NOT NULL,   -- UTC ISO-8601
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- One row per platform a post goes to. Each platform succeeds or fails on its own.
CREATE TABLE IF NOT EXISTS post_targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    platform      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'scheduled'
                  CHECK (status IN ('scheduled', 'publishing', 'published', 'failed')),
    external_id   TEXT,
    external_url  TEXT,
    error         TEXT,
    published_at  TEXT,
    UNIQUE (post_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_targets_status ON post_targets(status);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(scheduled_at);

-- Cache of posts read from the personal blog.
CREATE TABLE IF NOT EXISTS blog_posts (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    published_on  TEXT,               -- YYYY-MM-DD
    tags          TEXT NOT NULL DEFAULT '[]',  -- JSON array
    image         TEXT,
    fetched_at    TEXT NOT NULL
);

-- The media library: pictures and videos. The file itself is <media dir>/<id> (services/media.py).
-- kind, name, width, height and duration_ms are added by MIGRATIONS below (ADR 0011).
CREATE TABLE IF NOT EXISTS media (
    id            TEXT PRIMARY KEY,
    mime          TEXT    NOT NULL,   -- image/jpeg | image/png | image/gif
    size          INTEGER NOT NULL,
    created_at    TEXT    NOT NULL
);

-- How a published post did, per platform delivery. From the platform's API or an
-- imported analytics file (services/stats.py). NULL = not reported by that source.
CREATE TABLE IF NOT EXISTS post_metrics (
    target_id        INTEGER PRIMARY KEY REFERENCES post_targets(id) ON DELETE CASCADE,
    source           TEXT    NOT NULL CHECK (source IN ('api', 'import')),
    updated_at       TEXT    NOT NULL,
    impressions      INTEGER,
    members_reached  INTEGER,
    reactions        INTEGER,
    comments         INTEGER,
    reshares         INTEGER,
    saves            INTEGER,
    link_clicks      INTEGER,
    clicks           INTEGER,
    engagements      INTEGER
);

-- Plain-language history for "Recent activity" on the Status tab (services/activity.py).
-- No foreign key on post_id: the event outlives a deleted post. Only the newest 1000 are kept.
CREATE TABLE IF NOT EXISTS activity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT    NOT NULL,   -- UTC ISO-8601
    kind          TEXT    NOT NULL,   -- started | scheduled | edited | deleted | retried | published
                                      -- | failed | interrupted | test_post | signed_in | blog_synced
                                      -- | blog_planned | removed | stats_imported | ai_generated
    platform      TEXT,               -- platform id, when the event is about one platform
    post_id       INTEGER,
    message       TEXT    NOT NULL,   -- one sentence, shown to the user as is
    url           TEXT                -- link to the live post, if any
);

-- How AI-written posts should sound (services/personas.py). Style only: no lengths or
-- platforms, so any persona works with any platform. See ADR 0012.
CREATE TABLE IF NOT EXISTS personas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    tone          TEXT    NOT NULL DEFAULT '',
    voice         TEXT    NOT NULL DEFAULT '',
    language      TEXT    NOT NULL DEFAULT '',
    format        TEXT    NOT NULL DEFAULT '',
    hashtags      TEXT    NOT NULL DEFAULT '',
    emoji         TEXT    NOT NULL DEFAULT '',
    always        TEXT    NOT NULL DEFAULT '',
    never         TEXT    NOT NULL DEFAULT '',
    example       TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

-- Every AI generation, remembered (services/ai_writer.py). No foreign key on persona_id:
-- the history outlives a deleted persona and keeps its own copy. Newest 200 are kept.
CREATE TABLE IF NOT EXISTS generations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    source        TEXT    NOT NULL CHECK (source IN ('idea', 'blog')),
    idea          TEXT    NOT NULL DEFAULT '',
    blog_url      TEXT,
    event         TEXT    NOT NULL DEFAULT '{}',   -- JSON: name, day, venue, takeaways
    platforms     TEXT    NOT NULL,                -- JSON list of platform ids
    persona_id    INTEGER,
    persona       TEXT    NOT NULL,                -- JSON copy of the persona as used
    model         TEXT    NOT NULL,
    drafts        TEXT    NOT NULL                 -- JSON: platform -> {text, title?, length, limit, over, post_id, error}
);

-- Requests sent to Gemini per day (Pacific time, when Google's daily quota resets), so the
-- daily budget survives a restart (services/ai_writer.py Usage). Old days can be dropped.
CREATE TABLE IF NOT EXISTS ai_usage (
    day           TEXT    PRIMARY KEY,             -- YYYY-MM-DD in Pacific time
    requests      INTEGER NOT NULL DEFAULT 0
);
"""


# Columns added after the first release. SQLite can add a column but not a CHECK
# constraint, so new states are separate columns rather than new `status` values.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # Text for this platform when it differs from the post's main text (e.g. a longer LinkedIn version).
    ("post_targets", "body_override", "TEXT"),
    # Per-platform options as JSON: visibility, allow_reshare, attachment, image_id, image_alt, first_comment,
    # draft (dev.to: save as a draft instead of publishing), handoff (LinkedIn's own scheduler posts it).
    ("post_targets", "options", "TEXT NOT NULL DEFAULT '{}'"),
    # The post went out, but an extra step didn't (e.g. the first comment).
    ("post_targets", "warning", "TEXT"),
    # Deleted from the platform through the app (the planner keeps the record).
    ("post_targets", "removed_at", "TEXT"),
    # Text changed on the platform through the app.
    ("post_targets", "edited_at", "TEXT"),
    # The media library holds videos as well as pictures (services/media.py, ADR 0011).
    ("media", "kind", "TEXT NOT NULL DEFAULT 'image'"),   # image | video
    ("media", "name", "TEXT"),                            # the file name it was uploaded with
    ("media", "width", "INTEGER"),                        # as displayed (video rotation applied)
    ("media", "height", "INTEGER"),
    ("media", "duration_ms", "INTEGER"),                  # videos only
    ("post_metrics", "views", "INTEGER"),                 # video views (YouTube)
    # A past post read from the platform's own export (not sent by this app). See ADR 0008.
    ("post_targets", "imported_at", "TEXT"),
    # AI generation: the link goes in the first comment, not in the text (ADR 0012).
    ("generations", "link_in_comment", "INTEGER NOT NULL DEFAULT 0"),
)


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for table, column, ddl in MIGRATIONS:
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
