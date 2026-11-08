"""LinkedIn hand-off, past posts from an analytics file, the Social tab overview, and reports.
Platforms are fakes; nothing is posted."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from backend.app.services.reports import COLUMNS
from backend.app.services.scheduler import publish_due
from backend.app.services.stats_import import IMPORTED_TEXT, read_sheets
from backend.app.services.xlsx import Sheet, write_xlsx

from .test_post_management import c, fake_li, li_app, publish, schedule, target  # noqa: F401 (fixtures)
from .test_posts import force_due

HANDOFF = {"linkedin": {"handoff": True}}
PAST_URL = "https://www.linkedin.com/feed/update/urn:li:activity:7111111111111111111/"


def analytics_file(*rows: str) -> bytes:
    return ("Post URL,Post publish date,Impressions,Reactions\n" + "".join(r + "\n" for r in rows)).encode()


def all_posts(c) -> list[dict]:
    return c.get("/api/posts").json()


# ---- LinkedIn's own scheduler (hand-off) --------------------------------------------------

def test_a_handoff_is_kept_but_never_sent_by_the_app(li_app, c, fake_li):
    post = schedule(c, options=HANDOFF).json()
    assert target(post)["options"]["handoff"] is True
    publish_due(li_app.state.posts, li_app.state.publishers, li_app.state.attachments)
    assert target(c.get(f"/api/posts/{post['id']}").json())["status"] == "scheduled"   # not due yet

    force_due(li_app, post["id"])
    assert publish_due(li_app.state.posts, li_app.state.publishers, li_app.state.attachments) == 0
    t = target(c.get(f"/api/posts/{post['id']}").json())
    assert fake_li.sent == [] and t["status"] == "published"
    assert t["published_at"] == "2000-01-01T00:00:00+00:00"   # the time LinkedIn was told to post it
    assert "LinkedIn's own scheduler" in c.get("/api/activity").json()[0]["message"]


def test_other_platforms_in_a_handoff_post_are_still_sent(li_app, c, fake_x, fake_li):
    post = schedule(c, platforms=["x", "linkedin"], body="Hello", options=HANDOFF).json()
    force_due(li_app, post["id"])
    publish_due(li_app.state.posts, li_app.state.publishers, li_app.state.attachments)
    assert fake_x.sent == ["Hello"] and fake_li.sent == []


def test_only_linkedin_takes_a_handoff(c):
    r = schedule(c, platforms=["x"], body="Hi", options={"x": {"handoff": True}})
    assert r.status_code == 422 and r.json()["detail"] == "X (Twitter) can't schedule posts on its own side from the app."


def test_a_handoff_carries_text_only(c):
    r = schedule(c, options={"linkedin": {"handoff": True, "first_comment": "More here"}})
    assert r.status_code == 422 and r.json()["detail"].startswith("LinkedIn's own scheduler only gets the text")


def test_linkedin_has_a_post_box_for_the_handoff(c):
    li = next(p for p in c.get("/api/platforms").json() if p["id"] == "linkedin")
    assert li["compose_url"].startswith("https://www.linkedin.com/") and "{text}" in li["compose_url"]
    assert "handoff" in li["features"]


# ---- past posts from LinkedIn's analytics file ------------------------------------------------

def test_past_posts_are_added_with_their_stats_once(c):
    file = analytics_file(f"{PAST_URL},10/03/2026,500,12")
    r = c.post("/api/stats/import", content=file).json()
    assert (r["matched"], r["added"], r["unmatched"]) == (0, 1, 0)
    assert "1 past post" in r["message"]
    past = all_posts(c)[0]
    t = target(past)
    assert past["body"] == IMPORTED_TEXT and t["imported_at"] and t["external_url"] == PAST_URL
    assert t["published_at"] == "2026-10-03T12:00:00+00:00"
    assert (t["metrics"]["impressions"], t["metrics"]["reactions"]) == (500, 12)

    again = c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},10/03/2026,800,20")).json()
    assert (again["matched"], again["added"]) == (1, 0)
    assert len(all_posts(c)) == 1 and target(all_posts(c)[0])["metrics"]["impressions"] == 800


def test_rows_without_a_date_cannot_be_added(c):
    r = c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},,5,1")).json()
    assert (r["added"], r["unmatched"]) == (0, 1) and all_posts(c) == []


def test_a_handoff_gets_its_link_from_the_file(li_app, c):
    post = schedule(c, options=HANDOFF).json()
    force_due(li_app, post["id"])   # due on 1 Jan 2000, then settled as posted by LinkedIn
    publish_due(li_app.state.posts, li_app.state.publishers, li_app.state.attachments)
    r = c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},01/01/2000,42,3")).json()
    assert (r["matched"], r["added"]) == (1, 0)
    t = target(c.get(f"/api/posts/{post['id']}").json())
    assert t["external_url"] == PAST_URL and t["metrics"]["impressions"] == 42


def test_imported_posts_count_as_published_but_not_in_the_success_rate(c):
    c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},10/03/2026,500,12"))
    totals = c.get("/api/analytics").json()["totals"]
    assert (totals["published"], totals["imported"], totals["success_rate"]) == (1, 1, None)


# ---- the Social tab -----------------------------------------------------------------------------

def test_social_overview_lists_upcoming_history_and_top_posts(li_app, c):
    sent = schedule(c, body="Sent one").json()
    publish(li_app, sent["id"])
    schedule(c, body="Later one", options=HANDOFF)
    c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},10/03/2026,500,12"))

    o = c.get("/api/social/linkedin").json()
    assert [(u["text"], u["handoff"]) for u in o["upcoming"]] == [("Later one", True)]
    assert {h["text"] for h in o["history"]} == {"Sent one", IMPORTED_TEXT}
    assert [t["text"] for t in o["top"]] == [IMPORTED_TEXT]
    assert o["totals"]["imported"] == 1
    assert c.get("/api/social/nope").status_code == 404


# ---- downloadable reports -------------------------------------------------------------------------

def test_csv_report_in_local_time_and_safe_for_spreadsheets(li_app, c):
    post = schedule(c, body="=HYPERLINK(\"x\") looks like a formula").json()
    with li_app.state.posts.db.connect() as conn:
        conn.execute("UPDATE posts SET scheduled_at='2026-11-09T07:00:00+00:00' WHERE id=?", (post["id"],))
    r = c.get("/api/reports/posts", params={"format": "csv", "utc_offset": 120, "platform": "linkedin"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="social-manager-report-linkedin-' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == COLUMNS
    assert rows[1][:5] == ["2026-11-09 09:00", "LinkedIn", "Scheduled", "This app",
                           "'=HYPERLINK(\"x\") looks like a formula"]


def test_excel_report_has_a_summary_and_every_post(li_app, c):
    sent = schedule(c, body="Sent one").json()
    publish(li_app, sent["id"])
    c.post("/api/stats/import", content=analytics_file(f"{PAST_URL},10/03/2026,500,12"))
    r = c.get("/api/reports/posts", params={"format": "xlsx"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    summary, posts = read_sheets(r.content)
    assert summary[0] == ["Social Manager report"] and summary[1] == ["Period", "All time"]
    assert ["LinkedIn", "2", "0", "0", "100%", "1", "1", "500", "12"] == summary[6][:9]
    assert posts[0] == COLUMNS and len(posts) == 3
    assert {row[3] for row in posts[1:]} == {"This app", "Imported from LinkedIn"}


def test_report_filters_are_checked(c):
    assert c.get("/api/reports/posts", params={"format": "pdf"}).status_code == 422
    assert c.get("/api/reports/posts", params={"platform": "nope"}).status_code == 422
    assert c.get("/api/reports/posts", params={"utc_offset": 2000}).status_code == 422


def test_xlsx_writer_round_trips_text_numbers_and_odd_characters():
    rows = [["Name", "Count"], ["Tab\there & <tags> \x07bell", 3], ["", 2.5]]
    sheets = read_sheets(write_xlsx([Sheet("Data: one/two", rows, bold_rows={0})]))
    assert sheets == [[["Name", "Count"], ["Tab\there & <tags> bell", "3"], ["", "2.5"]]]
