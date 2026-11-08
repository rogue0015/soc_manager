"""Downloadable reports: every delivery in a period as CSV, or an Excel workbook with a summary.

Built from the same analytics report as the Analytics and Social tabs, so the
numbers agree. Times are written in the viewer's time zone (`utc_offset`, in
minutes, sent by the browser). In the CSV, text that a spreadsheet would run as
a formula (starting with = + - @) gets a leading apostrophe; the Excel file
stores all text as text. See docs/decisions/0008-social-tab-handoff-reports.md.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

from ..platforms import METRICS, PLATFORMS
from .analytics import AnalyticsService, deliveries, top_posts
from .errors import InvalidInput
from .xlsx import MIME as XLSX_MIME
from .xlsx import Sheet, write_xlsx

METRIC_LABELS = {
    "impressions": "Impressions", "members_reached": "Members reached", "reactions": "Reactions",
    "comments": "Comments", "reshares": "Reposts", "saves": "Saves", "link_clicks": "Link clicks",
    "clicks": "Clicks", "engagements": "Engagements",
}
COLUMNS = ["Date", "Platform", "Status", "Sent by", "Text", "Link", "Content", "Blog post",
           *(METRIC_LABELS[k] for k in METRICS), "Stats from", "Problem"]
WIDTHS = [17, 13, 20, 20, 60, 40, 10, 40, *([12] * len(METRICS)), 16, 40]
SUMMARY_METRICS = ("impressions", "reactions", "comments", "reshares", "clicks", "engagements")
STATUS_WORDS = {"published": "Published", "failed": "Failed", "scheduled": "Scheduled", "publishing": "Sending"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
FORMATS = ("csv", "xlsx")


def _name(pid: str) -> str:
    return PLATFORMS[pid].name if pid in PLATFORMS else pid


def _local(iso: str | None, tz: timezone) -> str:
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%Y-%m-%d %H:%M") if iso else ""


def _tz_label(tz: timezone) -> str:
    minutes = int(tz.utcoffset(None).total_seconds() // 60)
    sign, minutes = ("+" if minutes >= 0 else "-"), abs(minutes)
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _status(d: dict[str, Any]) -> str:
    if d["removed_at"]:
        return f"Deleted on {_name(d['platform'])}"
    if d["handoff"] and d["status"] == "scheduled":
        return f"Scheduled in {_name(d['platform'])}"
    return STATUS_WORDS.get(d["status"], d["status"])


def _sent_by(d: dict[str, Any]) -> str:
    if d["imported"]:
        return f"Imported from {_name(d['platform'])}"
    if d["handoff"]:
        return f"{_name(d['platform'])}'s scheduler"
    return "This app"


def _stats_from(d: dict[str, Any]) -> str:
    m = d["metrics"]
    if not m:
        return ""
    return f"{_name(d['platform'])} API" if m["source"] == "api" else "Imported file"


def _csv_safe(value: Any) -> Any:
    return f"'{value}" if isinstance(value, str) and value.startswith(FORMULA_START) else value


class ReportService:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def build(self, fmt: str, start: datetime | None = None, end: datetime | None = None,
              platform: str | None = None, source: str | None = None, status: str | None = None,
              utc_offset: int = 0) -> tuple[str, str, bytes]:
        """(file name, media type, file contents)."""
        if fmt not in FORMATS:
            raise InvalidInput("Reports come as csv or xlsx.")
        if not -14 * 60 <= utc_offset <= 14 * 60:
            raise InvalidInput("The time zone offset must be between -14 and +14 hours.")
        tz = timezone(timedelta(minutes=utc_offset))
        report = self.analytics.report(start, end, platform, source, status)
        rows = [self._row(d, tz) for d in deliveries(report)]
        now = datetime.now(tz)
        name = f"social-manager-report-{platform or 'all'}-{now:%Y-%m-%d}.{fmt}"
        if fmt == "csv":
            return name, "text/csv; charset=utf-8", self._csv(rows)
        summary = self._summary(report, start, end, platform, tz, now)
        posts = Sheet("Posts", [COLUMNS, *rows], bold_rows={0}, widths=WIDTHS, freeze_header=True)
        return name, XLSX_MIME, write_xlsx([summary, posts])

    @staticmethod
    def _row(d: dict[str, Any], tz: timezone) -> list[Any]:
        m = d["metrics"] or {}
        return [
            _local(d["happened_at"], tz), _name(d["platform"]), _status(d), _sent_by(d), d["text"],
            d["external_url"] or "", "Blog post" if d["source"] == "blog" else "Own post", d["blog_url"] or "",
            *(m.get(k, "") for k in METRICS), _stats_from(d), d["error"] or d["warning"] or "",
        ]

    @staticmethod
    def _csv(rows: list[list[Any]]) -> bytes:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        writer.writerows([_csv_safe(v) for v in row] for row in rows)
        return out.getvalue().encode("utf-8-sig")   # the BOM makes Excel read emoji and accents correctly

    @staticmethod
    def _summary(report: dict[str, Any], start: datetime | None, end: datetime | None, platform: str | None,
                 tz: timezone, now: datetime) -> Sheet:
        def day(d: datetime) -> str:
            return d.astimezone(tz).strftime("%d %b %Y")
        period = ("All time" if not (start or end) else
                  f"{day(start) if start else 'The start'} to {day(end) if end else 'now'}")
        head = ["Platform", "Published", "Failed", "Waiting", "Success rate", "Imported", "Posts with stats",
                *(METRIC_LABELS[k] for k in SUMMARY_METRICS)]

        def line(label: str, c: dict[str, Any]) -> list[Any]:
            rate = c["success_rate"]
            return [label, c["published"], c["failed"], c["scheduled"] + c["publishing"],
                    f"{round(rate * 100)}%" if rate is not None else "", c["imported"], c["stats"]["posts"],
                    *(c["stats"].get(k, "") for k in SUMMARY_METRICS)]

        rows: list[list[Any]] = [
            ["Social Manager report"],
            ["Period", period],
            ["Platform", _name(platform) if platform else "All platforms"],
            ["Generated", f"{now:%Y-%m-%d %H:%M} ({_tz_label(tz)})"],
            [],
            head,
            *(line(_name(p["platform"]), p) for p in report["by_platform"]),
        ]
        bold = {0, 5}
        if len(report["by_platform"]) != 1:
            bold.add(len(rows))
            rows.append(line("All platforms", report["totals"]))
        top = top_posts(deliveries(report), 10)
        rows += [[], ["Top posts"], ["Date", "Platform", "Text", "Impressions", "Reactions", "Comments", "Link"]]
        bold |= {len(rows) - 2, len(rows) - 1}
        rows += [[_local(d["happened_at"], tz), _name(d["platform"]), d["text"][:200],
                  d["metrics"].get("impressions", ""), d["metrics"].get("reactions", ""),
                  d["metrics"].get("comments", ""), d["external_url"] or ""] for d in top]
        if not top:
            rows.append(["No post statistics in this period yet."])
        return Sheet("Summary", rows, bold_rows=bold, widths=[22, 14, 50, 12, 12, 12, 16, 12, 12, 12, 12, 12, 12])
