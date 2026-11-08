"""Managing LinkedIn posts through the app: own text, options, images, link cards, live edit/delete,
statistics (API and import). Platforms are fakes; nothing leaves the machine."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.db import Database
from backend.app.main import create_app
from backend.app.services.attachments import Attachments
from backend.app.services.scheduler import publish_due

from .conftest import FakePublisher
from .test_blog import _seed
from .test_linkedin import FakeResponse
from .test_posts import force_due, future

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
LONG = "A longer LinkedIn version. " * 20   # ~540 characters: too long for X, fine for LinkedIn


@pytest.fixture
def fake_li() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def li_app(tmp_path, app, fake_x, fake_li):
    settings = replace(app.state.settings, db_path=tmp_path / "li" / "test.db")
    return create_app(settings, publishers={"x": fake_x, "linkedin": fake_li})


@pytest.fixture
def c(li_app):
    with TestClient(li_app) as client:
        yield client


def schedule(c, **overrides):
    payload = {"body": "Hello https://blog.test/a.html", "scheduled_at": future(), "platforms": ["linkedin"]} | overrides
    return c.post("/api/posts", json=payload)


def publish(app, post_id: int) -> None:
    force_due(app, post_id)
    publish_due(app.state.posts, app.state.publishers, app.state.attachments)


def target(post: dict, platform: str = "linkedin") -> dict:
    return next(t for t in post["targets"] if t["platform"] == platform)


# ---- own text per platform ----------------------------------------------------------

def test_linkedin_gets_its_own_longer_text(li_app, c, fake_x, fake_li):
    post = schedule(c, body="Short for X", platforms=["x", "linkedin"], variants={"linkedin": LONG}).json()
    assert target(post)["text"] == LONG and target(post, "x")["text"] == "Short for X"
    publish(li_app, post["id"])
    assert fake_x.sent == ["Short for X"] and fake_li.sent == [LONG]


def test_each_version_is_checked_against_its_own_limit(c):
    r = schedule(c, body="Short", platforms=["x", "linkedin"], variants={"linkedin": "x" * 3001})
    assert r.status_code == 422 and r.json()["detail"] == "LinkedIn allows 3000 characters; the LinkedIn version is 3001."


# ---- options ----------------------------------------------------------------------------

def test_options_reach_linkedin(li_app, c, fake_li):
    opts = {"visibility": "CONNECTIONS", "allow_reshare": False, "first_comment": "More at my blog"}
    post = schedule(c, options={"linkedin": opts}).json()
    assert target(post)["options"]["visibility"] == "CONNECTIONS"
    publish(li_app, post["id"])
    extras = fake_li.extras[0]
    assert (extras.visibility, extras.allow_reshare, extras.first_comment) == ("CONNECTIONS", False, "More at my blog")


def test_platforms_refuse_options_they_cant_do(c):
    r = schedule(c, platforms=["x"], options={"x": {"visibility": "CONNECTIONS"}})
    assert r.status_code == 422 and r.json()["detail"] == "X (Twitter) can't limit who sees a post from the app."


def test_a_link_preview_needs_a_link(c):
    r = schedule(c, body="No link here", options={"linkedin": {"attachment": "link_card"}})
    assert r.json()["detail"] == "A link preview needs a link in the LinkedIn text, or a blog post."


# ---- images -------------------------------------------------------------------------------

def test_upload_attach_and_publish_an_image(li_app, c, fake_li):
    up = c.post("/api/media", content=PNG, headers={"Content-Type": "image/png"})
    assert up.status_code == 201 and up.json()["mime"] == "image/png"
    assert c.get(up.json()["url"]).content == PNG

    opts = {"attachment": "image", "image_id": up.json()["id"], "image_alt": "Screenshot"}
    post = schedule(c, options={"linkedin": opts}).json()
    publish(li_app, post["id"])
    image = fake_li.extras[0].image
    assert (image.data, image.mime, image.alt) == (PNG, "image/png", "Screenshot")


def test_only_real_images_are_accepted(c):
    r = c.post("/api/media", content=b"<script>alert(1)</script>", headers={"Content-Type": "image/png"})
    assert r.status_code == 422 and r.json()["detail"] == "Only JPEG, PNG and GIF pictures and MP4 or MOV videos can be added."
    assert c.get("/api/media/../../env").status_code == 404
    assert c.get("/api/media/" + "a" * 32).status_code == 404


def test_scheduling_with_a_missing_image_is_refused(c):
    r = schedule(c, options={"linkedin": {"attachment": "image", "image_id": "f" * 32}})
    assert r.json()["detail"] == "The attached image isn't available any more. Attach it again."


# ---- link cards ---------------------------------------------------------------------------

def test_blog_posts_get_a_card_from_the_blog_cache(li_app):
    _seed(li_app)
    with li_app.state.posts.db.connect() as conn:
        conn.execute("UPDATE blog_posts SET image='https://blog.test/cover.png'")
    fetched = []
    attachments = Attachments(li_app.state.posts.db, li_app.state.media,
                              fetch=lambda url, **kw: fetched.append(url) or FakeImage())
    extras, warning = attachments.extras_for({"blog_url": "https://blog.test/blogs/one.html", "body": "x",
                                              "options": {"attachment": "link_card"}})
    assert warning is None and extras.link_card.title == "One" and extras.link_card.description == "Summary"
    assert extras.link_card.thumbnail.mime == "image/png" and fetched == ["https://blog.test/cover.png"]


def test_an_unreadable_link_posts_without_a_card(li_app):
    def down(url, **kw):
        import requests
        raise requests.ConnectionError("no")
    attachments = Attachments(li_app.state.posts.db, li_app.state.media, fetch=down)
    extras, warning = attachments.extras_for({"blog_url": None, "body": "See https://other.test/x",
                                              "options": {"attachment": "link_card"}})
    assert extras.link_card is None and warning == "Posted without a preview card: couldn't read https://other.test/x."


class FakeImage:
    content = PNG
    def raise_for_status(self): pass


def test_blog_plan_gives_linkedin_the_full_summary_and_a_card(li_app, c):
    _seed(li_app)
    with li_app.state.posts.db.connect() as conn:
        conn.execute("UPDATE blog_posts SET description=?", ("word " * 80,))
    created = c.post("/api/blog/plan", json={"urls": ["https://blog.test/blogs/one.html"], "start": future(24),
                                             "every_days": 1, "platforms": ["x", "linkedin"]}).json()
    li_target = target(created[0])
    assert len(li_target["text"]) > len(target(created[0], "x")["text"])
    assert li_target["options"]["attachment"] == "link_card"


# ---- live posts: delete and edit ------------------------------------------------------------

def test_delete_on_linkedin(li_app, c, fake_li):
    post = schedule(c).json()
    assert c.delete(f"/api/posts/{post['id']}/published/linkedin").status_code == 409  # not live yet
    publish(li_app, post["id"])

    r = c.delete(f"/api/posts/{post['id']}/published/linkedin")
    assert r.status_code == 200 and target(r.json())["removed_at"]
    assert fake_li.deleted == [target(r.json())["external_id"]]
    assert c.delete(f"/api/posts/{post['id']}/published/linkedin").json()["detail"] == \
        "This post was already deleted from LinkedIn."
    assert c.get("/api/activity", params={"limit": 1}).json()[0]["kind"] == "removed"


def test_x_cant_delete_from_the_app(li_app, c):
    post = schedule(c, platforms=["x"], body="hi").json()
    publish(li_app, post["id"])
    r = c.delete(f"/api/posts/{post['id']}/published/x")
    assert r.json()["detail"] == "X (Twitter) can't delete posts from the app."


def test_edit_on_linkedin(li_app, c, fake_li):
    post = schedule(c).json()
    publish(li_app, post["id"])
    url = f"/api/posts/{post['id']}/published/linkedin"

    r = c.put(url, json={"text": "Better words"})
    assert r.status_code == 200 and target(r.json())["text"] == "Better words" and target(r.json())["edited_at"]
    assert fake_li.edited[-1][1] == "Better words"
    assert c.put(url, json={"text": "Better words"}).json()["detail"].startswith("That's the same text")
    assert c.put(url, json={"text": "x" * 3001}).status_code == 422

    fake_li.fail_with = "HTTP 403: Not allowed"
    assert c.put(url, json={"text": "Again"}).json()["detail"] == "HTTP 403: Not allowed"


def test_a_post_that_went_out_with_a_problem_keeps_the_warning(li_app, c, fake_li):
    fake_li.warning = "The first comment wasn't added: throttled"
    post = schedule(c).json()
    publish(li_app, post["id"])
    t = target(c.get("/api/posts").json()[0])
    assert t["status"] == "published" and t["warning"] == "The first comment wasn't added: throttled"
    assert "But: The first comment" in c.get("/api/activity", params={"limit": 1}).json()[0]["message"]


# ---- statistics ---------------------------------------------------------------------------

def test_stats_status_says_why_automatic_stats_are_off(c):
    assert c.get("/api/stats").json() == [
        {"platform": "linkedin", "connected": True, "automatic": False,
         "reason": "This platform doesn't share post statistics with the app."},
        {"platform": "devto", "connected": False, "automatic": False, "reason": "dev.to isn't connected."},
        {"platform": "bluesky", "connected": False, "automatic": False, "reason": "Bluesky isn't connected."},
        {"platform": "youtube", "connected": False, "automatic": False, "reason": "YouTube isn't connected."},
    ]
    assert c.post("/api/stats/refresh").status_code == 422


def test_refresh_saves_stats_and_analytics_adds_them_up(li_app, c, fake_li):
    post = schedule(c).json()
    publish(li_app, post["id"])
    fake_li.stats = {"impressions": 120, "reactions": 9, "comments": 2}
    assert c.post("/api/stats/refresh").json() == {"updated": 1, "problems": []}

    report = c.get("/api/analytics").json()
    assert report["totals"]["stats"] == {"posts": 1, "impressions": 120, "reactions": 9, "comments": 2}
    metrics = report["posts"][0]["targets"][0]["metrics"]
    assert metrics["impressions"] == 120 and metrics["source"] == "api"
    assert target(c.get("/api/posts").json()[0])["metrics"]["reactions"] == 9


def _published_id(li_app, c) -> str:
    post = schedule(c).json()
    publish(li_app, post["id"])
    return target(c.get("/api/posts").json()[0])["external_id"].rsplit(":", 1)[1]


def test_import_matches_posts_by_their_id(li_app, c):
    post_id = _published_id(li_app, c)
    csv_file = ("Post URL,Post publish date,Impressions,Engagements\n"
                f"https://www.linkedin.com/feed/update/urn:li:share:{post_id}/,11/07/2026,\"1,234\",56\n"
                "https://www.linkedin.com/feed/update/urn:li:activity:7999999999999999999/,11/01/2026,10,1\n")
    r = c.post("/api/stats/import", content=csv_file.encode())
    assert r.status_code == 200
    assert (r.json()["matched"], r.json()["added"], r.json()["unmatched"]) == (1, 1, 0)
    mine = next(p for p in c.get("/api/posts").json() if p["id"] == 1)
    metrics = target(mine)["metrics"]
    assert (metrics["impressions"], metrics["engagements"], metrics["source"]) == (1234, 56, "import")


def test_import_reads_excel_with_side_by_side_tables(li_app, c):
    post_id = _published_id(li_app, c)
    url = f"https://www.linkedin.com/feed/update/urn:li:share:{post_id}/"
    rows = [["Post URL", "Post publish date", "Engagements", "", "Post URL", "Post publish date", "Impressions"],
            [url, "46333", "56", "", url, "46333", "900"]]
    r = c.post("/api/stats/import", content=xlsx({"TOP POSTS": rows}))
    assert r.json()["matched"] == 1
    metrics = target(c.get("/api/posts").json()[0])["metrics"]
    assert (metrics["engagements"], metrics["impressions"]) == (56, 900)


def test_import_falls_back_to_the_publish_date(li_app, c):
    _published_id(li_app, c)
    today = target(c.get("/api/posts").json()[0])["published_at"][:10]
    y, m, d = today.split("-")
    r = c.post("/api/stats/import", content=f"Post URL,Post publish date,Impressions\n"
                                             f"https://www.linkedin.com/posts/me-activity-7123456789012345678-x,{m}/{d}/{y},77\n".encode())
    assert r.json()["matched"] == 1


def test_import_never_overwrites_api_numbers(li_app, c, fake_li):
    post_id = _published_id(li_app, c)
    fake_li.stats = {"impressions": 500}
    c.post("/api/stats/refresh")
    r = c.post("/api/stats/import", content=(f"Post URL,Impressions\n"
                                             f"https://www.linkedin.com/feed/update/urn:li:share:{post_id}/,1\n").encode())
    assert r.json()["skipped"] == 1
    assert target(c.get("/api/posts").json()[0])["metrics"]["impressions"] == 500


def test_import_rejects_files_without_a_post_table(c):
    r = c.post("/api/stats/import", content=b"Name,Value\nfoo,1\n")
    assert r.status_code == 422 and r.json()["detail"].startswith("No post table found")
    assert c.post("/api/stats/import", content=b"PK\x03\x04broken").json()["detail"] == \
        "That file isn't a readable Excel file."


def xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    """A minimal .xlsx with inline strings, like spreadsheet apps write."""
    def col(i):
        return chr(65 + i)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        sheet_tags, rels = [], []
        for n, (name, rows) in enumerate(sheets.items(), start=1):
            sheet_tags.append(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>')
            rels.append(f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml" '
                        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>')
            xml_rows = "".join(
                f'<row r="{r + 1}">' + "".join(
                    f'<c r="{col(i)}{r + 1}" t="inlineStr"><is><t>{v}</t></is></c>' for i, v in enumerate(row) if v
                ) + "</row>" for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{n}.xml", f"<worksheet {ns}><sheetData>{xml_rows}</sheetData></worksheet>")
        z.writestr("xl/workbook.xml", f"<workbook {ns} {rel_ns}><sheets>{''.join(sheet_tags)}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + "</Relationships>")
    return buf.getvalue()


# ---- upgrading an existing database -----------------------------------------------------------

def test_old_databases_get_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'custom', blog_url TEXT, scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE post_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
                platform TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', external_id TEXT,
                external_url TEXT, error TEXT, published_at TEXT, UNIQUE (post_id, platform));
            INSERT INTO posts (body, scheduled_at, created_at, updated_at) VALUES ('old', 'x', 'x', 'x');
            INSERT INTO post_targets (post_id, platform) VALUES (1, 'x');
        """)
    Database(path).init()
    Database(path).init()  # running it twice is harmless
    with sqlite3.connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(post_targets)")}
        assert {"body_override", "options", "warning", "removed_at", "edited_at"} <= cols
        assert conn.execute("SELECT options FROM post_targets").fetchone()[0] == "{}"
    assert json.loads("{}") == {}


# ---- test posts are kept, so they can be managed later ----------------------------------------

def test_a_test_post_can_be_edited_and_deleted_later(c, fake_li):
    sent = c.post("/api/platforms/linkedin/test", json={"text": "Hello LinkedIn"}).json()
    post = c.get(f"/api/posts/{sent['post_id']}").json()
    t = target(post)
    assert (t["status"], t["external_id"], t["text"]) == ("published", sent["external_id"], "Hello LinkedIn")
    assert any(p["id"] == post["id"] for p in c.get("/api/posts").json())   # on the calendar
    assert c.get("/api/activity", params={"limit": 1}).json()[0]["post_id"] == post["id"]

    assert c.put(f"/api/posts/{post['id']}/published/linkedin", json={"text": "Hello again"}).status_code == 200
    assert c.delete(f"/api/posts/{post['id']}/published/linkedin").status_code == 200
    assert fake_li.edited[-1] == (sent["external_id"], "Hello again") and fake_li.deleted == [sent["external_id"]]


def test_a_failed_test_post_leaves_nothing_behind(c, fake_li):
    fake_li.fail_with = "HTTP 422: Duplicate"
    r = c.post("/api/platforms/linkedin/test", json={"text": "Hello"})
    assert r.status_code == 422 and r.json()["detail"] == "HTTP 422: Duplicate"
    assert c.get("/api/posts").json() == []


def test_get_one_post(c):
    assert c.get("/api/posts/999").json()["detail"] == "Post 999 not found"
