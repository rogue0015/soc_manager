"""Blog importer and content planner.

The blog (a static GitHub Pages site) has no RSS feed, but it has a
sitemap.xml and each post carries Open Graph meta tags. We read the
sitemap for post URLs, then each post's <meta> tags for title, summary,
date, tags and image. Results are cached in the blog_posts table.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import requests

from ..db import Database
from ..platforms import PLATFORMS
from ..timeutil import now_iso
from .errors import InvalidInput
from .posts import PostService, validate_content, validate_time

TIMEOUT = 15
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# Index/pagination pages live under /blogs/ too; they are not posts.
NOT_A_POST = re.compile(r"/blogs/(index|page\d+)\.html$")


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.tags: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            content = a.get("content")
            if key and content is not None:
                if key == "article:tag":
                    self.tags.append(content)
                else:
                    self.meta.setdefault(key, content)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse_post(html: str, url: str) -> dict[str, Any]:
    p = _MetaParser()
    p.feed(html)
    m = p.meta
    title = m.get("og:title") or p.title.split("|")[0].strip() or url
    return {
        "url": m.get("og:url") or url,
        "title": title.strip(),
        "description": re.sub(r"\s+", " ", m.get("og:description") or m.get("description") or "").strip(),
        "published_on": m.get("article:published_time", "")[:10] or None,
        "tags": p.tags,
        "image": m.get("og:image"),
    }


def post_urls_from_sitemap(xml_text: str, base_url: str) -> list[str]:
    root = ET.fromstring(xml_text)
    urls = [loc.text.strip() for loc in root.findall(".//sm:loc", SITEMAP_NS) if loc.text]
    prefix = f"{base_url}/blogs/"
    return [u for u in urls if u.startswith(prefix) and u.endswith(".html") and not NOT_A_POST.search(u)]


def compose_text(post: dict[str, Any], platforms: list[str]) -> str:
    """Build share text (title, summary, link) that fits the strictest selected platform."""
    title, desc, url = post["title"], post["description"], post["url"]
    infos = [PLATFORMS[p] for p in platforms if p in PLATFORMS] or [PLATFORMS["x"]]

    def fits(text: str) -> bool:
        return all(i.text_length(text) <= i.char_limit for i in infos)

    full = f"{title}\n\n{desc}\n\n{url}" if desc else f"{title}\n\n{url}"
    if fits(full):
        return full
    # Trim the summary word by word until it fits; drop it entirely if needed.
    words = desc.split()
    while words:
        words.pop()
        text = f"{title}\n\n{' '.join(words).rstrip('.,;:')}…\n\n{url}"
        if fits(text):
            return text
    return f"{title}\n\n{url}"


class BlogService:
    def __init__(self, db: Database, posts: PostService, base_url: str):
        self.db = db
        self.posts = posts
        self.base_url = base_url

    def sync(self) -> list[dict[str, Any]]:
        """Fetch the blog and refresh the cache. Raises InvalidInput if the blog can't be reached."""
        try:
            resp = requests.get(f"{self.base_url}/sitemap.xml", timeout=TIMEOUT)
            resp.raise_for_status()
            urls = post_urls_from_sitemap(resp.text, self.base_url)
        except (requests.RequestException, ET.ParseError) as exc:
            raise InvalidInput(f"Couldn't read the blog sitemap: {exc}") from exc

        def fetch(url: str) -> dict[str, Any] | None:
            try:
                r = requests.get(url, timeout=TIMEOUT)
                r.raise_for_status()
                return parse_post(r.text, url)
            except requests.RequestException:
                return None  # one broken post shouldn't stop the import

        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [p for p in pool.map(fetch, urls) if p]

        fetched_at = now_iso()
        with self.db.connect() as conn:
            for p in found:
                conn.execute(
                    """INSERT INTO blog_posts (url, title, description, published_on, tags, image, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(url) DO UPDATE SET title=excluded.title, description=excluded.description,
                         published_on=excluded.published_on, tags=excluded.tags, image=excluded.image,
                         fetched_at=excluded.fetched_at""",
                    (p["url"], p["title"], p["description"], p["published_on"],
                     json.dumps(p["tags"]), p["image"], fetched_at),
                )
        return self.list()

    def list(self) -> list[dict[str, Any]]:
        """Cached blog posts, newest first, each with `scheduled_count` (how many planner posts use it)."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT b.*, (SELECT COUNT(*) FROM posts p WHERE p.blog_url = b.url) AS scheduled_count
                   FROM blog_posts b ORDER BY b.published_on DESC, b.title"""
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"])
            out.append(d)
        return out

    def plan(self, urls: list[str], start: datetime, every_days: int, platforms: list[str]) -> list[dict[str, Any]]:
        """Schedule one post per blog URL: the first at `start`, then every `every_days` days."""
        if not urls:
            raise InvalidInput("Pick at least one blog post to plan.")
        if not 1 <= every_days <= 60:
            raise InvalidInput("Spacing must be between 1 and 60 days.")
        cache = {p["url"]: p for p in self.list()}
        unknown = [u for u in urls if u not in cache]
        if unknown:
            raise InvalidInput(f"Not in the blog library (sync first?): {', '.join(unknown)}")
        # Validate everything first so a bad input never leaves a half-made plan.
        validate_time(start)
        drafts = []
        for i, url in enumerate(urls):
            text = compose_text(cache[url], platforms)
            validate_content(text, platforms)
            drafts.append((text, start + timedelta(days=i * every_days), url))
        return [self.posts.create(text, when, platforms, blog_url=url) for text, when, url in drafts]
