from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.platforms import PLATFORMS
from backend.app.services.blog import compose_text, parse_post, post_urls_from_sitemap

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://blog.test/</loc></url>
  <url><loc>https://blog.test/blogs/</loc></url>
  <url><loc>https://blog.test/blogs/page2.html</loc></url>
  <url><loc>https://blog.test/blogs/black-tax.html</loc></url>
  <url><loc>https://blog.test/blogs/hello-world.html</loc></url>
  <url><loc>https://blog.test/tools/qr/</loc></url>
</urlset>"""

POST_HTML = """<html><head>
<title>Black Tax | Brandon T. Bande</title>
<meta property="og:title" content="Black Tax">
<meta property="og:description" content="Taxes  - black tax &amp; careers.">
<meta property="og:url" content="https://blog.test/blogs/black-tax.html">
<meta property="og:image" content="https://blog.test/a.jpg">
<meta property="article:published_time" content="2026-08-23">
<meta property="article:tag" content="personal">
<meta property="article:tag" content="tech-community">
</head><body></body></html>"""


def test_sitemap_keeps_only_post_pages():
    assert post_urls_from_sitemap(SITEMAP, "https://blog.test") == [
        "https://blog.test/blogs/black-tax.html",
        "https://blog.test/blogs/hello-world.html",
    ]


def test_parse_post_reads_meta_tags():
    post = parse_post(POST_HTML, "https://blog.test/blogs/black-tax.html")
    assert post == {
        "url": "https://blog.test/blogs/black-tax.html",
        "title": "Black Tax",
        "description": "Taxes - black tax & careers.",
        "published_on": "2026-08-23",
        "tags": ["personal", "tech-community"],
        "image": "https://blog.test/a.jpg",
    }


def test_compose_text_fits_x():
    post = {"title": "A title", "description": "word " * 200, "url": "https://blog.test/blogs/x.html"}
    text = compose_text(post, ["x", "linkedin"])
    assert PLATFORMS["x"].text_length(text) <= 280
    assert text.startswith("A title") and text.endswith(post["url"])


def test_compose_text_keeps_short_summary_whole():
    post = {"title": "T", "description": "Short.", "url": "https://blog.test/b.html"}
    assert compose_text(post, ["x"]) == "T\n\nShort.\n\nhttps://blog.test/b.html"


def _seed(app):
    with app.state.posts.db.connect() as conn:
        for slug, day in (("one", "2026-01-01"), ("two", "2026-02-01")):
            conn.execute(
                "INSERT INTO blog_posts (url, title, description, published_on, fetched_at) VALUES (?,?,?,?,?)",
                (f"https://blog.test/blogs/{slug}.html", slug.title(), "Summary", day, "now"),
            )


def test_plan_spaces_posts_and_marks_them_scheduled(app, client):
    _seed(app)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    r = client.post("/api/blog/plan", json={
        "urls": ["https://blog.test/blogs/two.html", "https://blog.test/blogs/one.html"],
        "start": start.isoformat(), "every_days": 3, "platforms": ["x"],
    })
    assert r.status_code == 201
    created = r.json()
    times = [datetime.fromisoformat(p["scheduled_at"]) for p in created]
    assert times[1] - times[0] == timedelta(days=3)
    assert all(p["source"] == "blog" for p in created)
    library = {b["url"]: b["scheduled_count"] for b in client.get("/api/blog/posts").json()}
    assert set(library.values()) == {1}


def test_plan_rejects_unknown_url(client):
    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = client.post("/api/blog/plan", json={
        "urls": ["https://blog.test/blogs/nope.html"], "start": start, "every_days": 1, "platforms": ["x"],
    })
    assert r.status_code == 422
