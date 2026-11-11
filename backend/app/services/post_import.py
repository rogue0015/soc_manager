"""Validate scheduled-post spreadsheets before adding their rows to the existing planner (ADR 0014).

Excel workbooks (.xlsx, the template's format) and CSV files are both read. The files come from
spreadsheets, so the reader is forgiving about what spreadsheets produce: Excel date cells (serial
numbers), Windows-1252 CSV text, semicolon-separated columns, header case, platform display names
("LinkedIn") and times without a timezone (read in the browser's timezone, sent as `tz`).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from ..platforms import PLATFORMS
from ..schemas import PostIn
from .errors import InvalidInput
from .posts import PostService
from .xlsx import excel_serial_to_datetime, read_xlsx

REQUIRED_COLUMNS = {"body", "scheduled_at", "platforms"}
OPTIONAL_COLUMNS = {"blog_url"}
MAX_ROWS = 500
_SERIAL = re.compile(r"\d{4,6}(\.\d+)?")
TIME_EXAMPLE = "2026-12-01 09:00 or 2026-12-01T09:00:00+02:00"


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel's plain "CSV" format on Windows saves Windows-1252, not UTF-8.
        return content.decode("cp1252", errors="replace")


def _delimiter(text: str) -> str:
    """Excel uses ';' in locales where ',' is the decimal mark; the header line tells which."""
    header = text.split("\n", 1)[0]
    return max((",", ";", "\t"), key=header.count)


def _csv_rows(content: bytes) -> tuple[list[list[str]], str | None]:
    text = _decode(content)
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text), strict=True)
    try:
        rows.extend(reader)
    except csv.Error as exc:
        return rows, f"Could not read this row of the CSV file: {exc}"
    return rows, None


def _table(content: bytes) -> tuple[list[list[str]], str | None]:
    """The posts table as rows of cell text, and a read error to report after the good rows.

    An Excel workbook (zip, starts with PK) uses the first sheet with the template's columns,
    so a help sheet or reordered tabs don't matter."""
    if content[:2] != b"PK" and content[:4] != b"\xd0\xcf\x11\xe0":
        return _csv_rows(content)
    sheets = read_xlsx(content)
    for rows in sheets:
        header = {cell.strip().lower().replace(" ", "_") for cell in (rows[0] if rows else [])}
        if REQUIRED_COLUMNS <= header:
            return rows, None
    return (sheets[0] if sheets else []), None


def _platform_ids(raw: str) -> list[str]:
    lookup = {pid.lower(): pid for pid in PLATFORMS}
    lookup |= {info.name.lower(): pid for pid, info in PLATFORMS.items()}
    names = [part.strip() for part in raw.replace(",", ";").split(";") if part.strip()]
    unknown = [name for name in names if name.lower() not in lookup]
    if unknown:
        known = ", ".join(sorted(PLATFORMS))
        raise InvalidInput(f"Unknown platform: {', '.join(unknown)}. Use one of: {known}.")
    return list(dict.fromkeys(lookup[name.lower()] for name in names))


def _parse_time(raw: str, zone: ZoneInfo | None) -> datetime:
    if not raw:
        raise InvalidInput(f"The scheduled_at time is empty. Use a date and time like {TIME_EXAMPLE}.")
    try:
        # A date cell in an Excel workbook arrives as its serial number (46357.375 = 2026-12-01 09:00).
        value = excel_serial_to_datetime(float(raw)) if _SERIAL.fullmatch(raw) else \
            datetime.fromisoformat(raw.replace("/", "-"))
    except (ValueError, OverflowError):
        raise InvalidInput(f"Couldn't read the time “{raw}”. Use a date and time like {TIME_EXAMPLE}.") from None
    if value.tzinfo is None:
        if zone is None:
            raise InvalidInput(f"The time “{raw}” has no timezone. Add one, like 2026-12-01T09:00:00+02:00.")
        value = value.replace(tzinfo=zone)
    return value


class ScheduledPostImport:
    def __init__(self, posts: PostService):
        self.posts = posts

    def _validated_rows(self, content: bytes, tz: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        errors: list[dict[str, Any]] = []
        try:
            zone = ZoneInfo(tz) if tz else None
        except (ZoneInfoNotFoundError, ValueError):
            zone = None  # rows without an offset then get a readable error

        rows, read_error = _table(content)
        raw_headers = rows[0] if rows else []
        headers = [name.strip().lower().replace(" ", "_") for name in raw_headers or []]
        if not any(headers):
            return [], [{"row": 1, "message": "The file is empty. Start from the template."}]
        if len(headers) != len(set(headers)):
            return [], [{"row": 1, "message": "Column names must be unique."}]
        missing = REQUIRED_COLUMNS - set(headers)
        unknown = set(headers) - REQUIRED_COLUMNS - OPTIONAL_COLUMNS
        if missing or unknown:
            messages = []
            if missing:
                messages.append(f"Missing columns: {', '.join(sorted(missing))}.")
            if unknown:
                messages.append(f"Unknown columns: {', '.join(sorted(unknown))}.")
            messages.append("The first row must be: body, scheduled_at, platforms, blog_url.")
            return [], [{"row": 1, "message": " ".join(messages)}]

        posts: list[dict[str, Any]] = []
        # Spreadsheet row numbers, not file lines: a quoted post text can span several lines.
        for row_number, cells in enumerate(rows[1:], start=2):
            if not any(cell.strip() for cell in cells):
                continue
            if len(posts) + len(errors) >= MAX_ROWS:
                errors.append({"row": row_number, "message": f"Files can contain at most {MAX_ROWS} posts."})
                break
            if len(cells) > len(headers) and any(cell.strip() for cell in cells[len(headers):]):
                errors.append({"row": row_number, "message": "This row has more values than the header. "
                               "In a CSV file, put text with commas in quotes."})
                continue
            values = {key: cell.strip() for key, cell in zip(headers, cells)}
            try:
                post = PostIn.model_validate({
                    "body": values.get("body", ""),
                    "scheduled_at": _parse_time(values.get("scheduled_at", ""), zone),
                    "platforms": _platform_ids(values.get("platforms", "")),
                    "blog_url": values.get("blog_url") or None,
                })
                self.posts.validate(post.body, post.scheduled_at, post.platforms, post.blog_url)
            except InvalidInput as exc:
                errors.append({"row": row_number, "message": str(exc)})
                continue
            except ValidationError as exc:
                errors.append({"row": row_number, "message": self._plain(exc)})
                continue
            posts.append(post.model_dump())
        if read_error:
            errors.append({"row": len(rows) + 1, "message": read_error})

        if not posts and not errors:
            errors.append({"row": 2, "message": "Add at least one post below the header row."})
        return posts, errors

    @staticmethod
    def _plain(exc: ValidationError) -> str:
        field = exc.errors(include_url=False)[0]["loc"][0]
        return {
            "body": "The post text is empty or too long.",
            "platforms": "Choose at least one platform.",
        }.get(str(field), f"Check the {field} column.")

    def preview(self, content: bytes, tz: str | None = None) -> dict[str, Any]:
        """Return row validation errors without creating any posts."""
        posts, errors = self._validated_rows(content, tz)
        return {"valid": not errors, "posts": len(posts), "errors": errors}

    def import_csv(self, content: bytes, tz: str | None = None) -> dict[str, Any]:
        """Create every row atomically after revalidating the uploaded file."""
        posts, errors = self._validated_rows(content, tz)
        if errors:
            return {"valid": False, "posts": len(posts), "created": 0, "errors": errors}
        created = self.posts.create_many(posts)
        return {"valid": True, "posts": len(posts), "created": len(created), "errors": []}
