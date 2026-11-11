"""Import post statistics from the analytics file LinkedIn lets you export.

linkedin.com/analytics → Export gives an .xlsx file (some tools save it as .csv).
Its exact columns aren't documented, so this reader is deliberately forgiving:
it scans every sheet for a header row with a post link column and at least one
number column (impressions, engagements, ...). A sheet can hold several tables
side by side (LinkedIn's "Top posts" sheet ranks by engagements and by
impressions), so each link column starts its own group and the groups are
merged per post. See ADR 0006.

Rows are matched to posts this app published by the post's ID in the link,
or, failing that, by the publish date when exactly one post went out then.
Rows that match nothing are past posts (published before this app, or from
LinkedIn itself): they are added as imported posts with their numbers, so past
stats show up too (ADR 0008). Importing the same file again updates them.
A matched hand-off (LinkedIn's own scheduler) gets its link from the file.
An import never overwrites numbers fetched from the API.

The .xlsx reader is services/xlsx.read_xlsx (standard library only).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Any

from .activity import plural
from .errors import InvalidInput
from .posts import PostService
from .xlsx import read_xlsx

MAX_FILE = 5 * 1024 * 1024

# Header text (lower case) -> our field. Several spellings per field.
HEADERS = {
    "url": ("post url", "url", "post link", "link", "permalink"),
    "date": ("post publish date", "publish date", "published", "date", "created date", "posted on", "post date"),
    "impressions": ("impressions",),
    "members_reached": ("members reached", "unique impressions", "unique views"),
    "reactions": ("reactions", "likes"),
    "comments": ("comments",),
    "reshares": ("reposts", "reshares", "shares"),
    "clicks": ("clicks",),
    "link_clicks": ("link clicks",),
    "engagements": ("engagements",),
    "saves": ("saves",),
}
_FIELD_BY_HEADER = {h: field for field, names in HEADERS.items() for h in names}
_POST_ID = re.compile(r"(?:urn(?::|%3A)li(?::|%3A)(?:activity|share|ugcPost)(?::|%3A)|activity-)(\d{15,22})")
_TRAILING_ID = re.compile(r"(\d{15,22})\D*$")
# A past post's text isn't in the analytics file; this stands in for it.
IMPORTED_TEXT = "LinkedIn post (imported, text not in the file)"


def post_id(link: str | None) -> str | None:
    """LinkedIn's numeric post ID in a link or URN, if there is one."""
    if not link:
        return None
    m = _POST_ID.search(link) or _TRAILING_ID.search(link)
    return m.group(1) if m else None


# ---- reading the file ----------------------------------------------------------------

def _csv_sheets(data: bytes) -> list[list[list[str]]]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]]


def read_sheets(data: bytes) -> list[list[list[str]]]:
    if not data:
        raise InvalidInput("That file is empty.")
    if len(data) > MAX_FILE:
        raise InvalidInput("That file is larger than 5 MB; is it LinkedIn's analytics export?")
    return read_xlsx(data) if data[:2] == b"PK" else _csv_sheets(data)


# ---- finding post rows -------------------------------------------------------------

def _number(value: str) -> int | None:
    cleaned = value.replace(",", "").replace(" ", "").rstrip("%")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _date(value: str) -> date | None:
    value = value.strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", value):  # Excel serial day number
        return date(1899, 12, 30) + timedelta(days=int(float(value)))
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def post_rows(sheets: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Every row that names a post, merged per post link."""
    merged: dict[str, dict[str, Any]] = {}
    for rows in sheets:
        header_at = None
        for i, row in enumerate(rows):
            fields = [_FIELD_BY_HEADER.get(cell.lower()) for cell in row]
            if "url" in fields and any(f not in (None, "url", "date") for f in fields):
                header_at, columns = i, fields
                break
        if header_at is None:
            continue
        # Each link column starts a group: [url, date?, metric...]
        groups, current = [], None
        for col, field in enumerate(columns):
            if field == "url":
                current = {"url": col}
                groups.append(current)
            elif field and current is not None and field not in current:
                current[field] = col
        for row in rows[header_at + 1:]:
            for g in groups:
                url = row[g["url"]] if g["url"] < len(row) else ""
                if not url.startswith("http"):
                    continue
                entry = merged.setdefault(url, {"url": url})
                for field, col in g.items():
                    if field == "url" or col >= len(row):
                        continue
                    value = _date(row[col]) if field == "date" else _number(row[col])
                    if value is not None:
                        entry[field] = value
    return list(merged.values())


# ---- matching and saving -------------------------------------------------------------

class StatsImport:
    def __init__(self, posts: PostService):
        self.posts = posts

    def run(self, data: bytes, platform: str = "linkedin") -> dict[str, Any]:
        rows = post_rows(read_sheets(data))
        if not rows:
            raise InvalidInput("No post table found in that file. Export it from linkedin.com/analytics "
                               "(Posts / Content → Export) and try again.")
        known = self.posts.published_targets(platform)
        by_id = {pid: t for t in known
                 if (pid := post_id(t["external_id"]) or post_id(t["external_url"]))}
        used: set[int] = set()
        matched, added, unmatched, skipped = 0, 0, [], 0
        for row in rows:
            values = {k: v for k, v in row.items() if k not in ("url", "date")}
            target = self._match(row, by_id, known, used)
            if target is None:
                if not row.get("date"):
                    unmatched.append({"url": row["url"], "date": None})
                    continue
                target_id = self._add_past_post(platform, row)
                added += 1
                self.posts.save_metrics(target_id, "import", values)
                continue
            used.add(target["id"])
            if not target["external_url"]:   # a hand-off: LinkedIn posted it, so the link comes from here
                pid = post_id(row["url"])
                self.posts.set_link(target["id"], f"urn:li:activity:{pid}" if pid else None, row["url"])
            if self.posts.save_metrics(target["id"], "import", values):
                matched += 1
            else:
                skipped += 1
        message = self._message(len(rows), matched, added, skipped, len(unmatched))
        self.posts.activity.record("stats_imported", f"Imported LinkedIn stats. {message}", platform=platform)
        return {
            "rows": len(rows), "matched": matched, "added": added, "skipped": skipped,
            "unmatched": len(unmatched), "unmatched_examples": unmatched[:5], "message": message,
        }

    def _add_past_post(self, platform: str, row: dict[str, Any]) -> int:
        # The file only has the day. Noon UTC keeps it on that day in every time zone from UTC-11 to UTC+11.
        when = f"{row['date'].isoformat()}T12:00:00+00:00"
        pid = post_id(row["url"])
        return self.posts.record_imported(platform, IMPORTED_TEXT, f"urn:li:activity:{pid}" if pid else None,
                                          row["url"], when)

    @staticmethod
    def _match(row: dict[str, Any], by_id: dict[str, dict], known: list[dict], used: set[int]) -> dict | None:
        row_id = post_id(row["url"])
        if row_id and (t := by_id.get(row_id)) and t["id"] not in used:
            return t
        day = row.get("date")
        if not day:
            return None
        # The export's dates are in your time zone and ours are UTC, so allow a day either side,
        # and only accept the match when it's the one candidate. An imported post with another
        # LinkedIn ID is a different post; app posts carry a share ID that can't be compared.
        near = [t for t in known if t["id"] not in used and t["published_at"]
                and not (t["imported_at"] and row_id and post_id(t["external_id"]) != row_id)
                and abs((datetime.fromisoformat(t["published_at"]).date() - day).days) <= 1]
        return near[0] if len(near) == 1 else None

    @staticmethod
    def _message(rows: int, matched: int, added: int, skipped: int, unmatched: int) -> str:
        parts = [f"Read {plural(rows, 'post')} from the file: added stats to {matched} "
                 f"you already had here and {plural(added, 'past post')}."]
        if skipped:
            parts.append(f"{skipped} already had stats straight from LinkedIn, so they were kept.")
        if unmatched:
            parts.append(f"{unmatched} had no date, so they couldn't be added.")
        return " ".join(parts)
