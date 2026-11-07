"""Time helpers.

All times are stored in the database as UTC ISO-8601 strings
("2026-11-07T09:00:00+00:00"). One fixed format means plain string
comparison in SQL sorts correctly. The browser converts to local time.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def now_iso() -> str:
    return to_utc_iso(now_utc())
