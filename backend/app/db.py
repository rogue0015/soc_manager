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
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

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
