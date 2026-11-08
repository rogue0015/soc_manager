"""Turns a post's saved per-platform options into PostExtras when it is published.

- "image": reads the attached image from the media store.
- "link_card": builds a preview card for the post's link. Blog posts use the
  blog cache (title, summary, cover image); other links are read from the
  page's own meta tags. If the page can't be read, the post still goes out
  as plain text, with a warning, because the link is in the text anyway.
- article: platforms that publish whole articles (dev.to) get the blog post itself,
  read fresh from the blog when it goes out (services/article.py).
- visibility, allow_reshare, first_comment and draft are passed through.

A missing image or an unreadable article is an error (posting without it would
be a surprise); a missing preview card is only a warning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests

from ..db import Database
from ..platforms import PLATFORMS, Article, ImageFile, LinkCard, PostExtras
from ..platforms.base import URL_RE
from .article import ArticleError, ArticleReader
from .blog import parse_post
from .errors import NotFound
from .media import MAX_BYTES, MediaService, sniff_image

TIMEOUT = 15
MAX_DESCRIPTION = 400


class AttachmentError(Exception):
    """The post can't go out as planned. The message is shown to the user."""


def post_link(target: dict[str, Any]) -> str | None:
    """The link a preview card is for: the blog post, or the first link in the text."""
    if target.get("blog_url"):
        return target["blog_url"]
    match = URL_RE.search(target.get("body") or "")
    return match.group().rstrip(".,;:!?)") if match else None


class Attachments:
    def __init__(self, db: Database, media: MediaService | None,
                 fetch: Callable[..., requests.Response] = requests.get,
                 articles: ArticleReader | None = None):
        self.db = db
        self.media = media
        self.fetch = fetch
        self.articles = articles or ArticleReader(db, fetch)

    def article_for(self, target: dict[str, Any]) -> Article | None:
        """The blog post as an article, for platforms that publish articles; else None."""
        info = PLATFORMS.get(target.get("platform", ""))
        if info is None or "article" not in info.features:
            return None
        if not target.get("blog_url"):
            raise AttachmentError(f"{info.name} publishes whole blog posts, and this post has no blog post. "
                                  f"Remove {info.name} from it, or schedule a blog post instead.")
        try:
            return self.articles.read(target["blog_url"])
        except ArticleError as exc:
            raise AttachmentError(str(exc)) from exc

    def extras_for(self, target: dict[str, Any]) -> tuple[PostExtras, str | None]:
        """The extras for one delivery, and a warning if something had to be left out."""
        opts = target.get("options") or {}
        image, card, warning = None, None, None
        kind = opts.get("attachment", "none")
        if kind == "image":
            if self.media is None:
                raise AttachmentError("Images can't be attached here.")
            try:
                image = self.media.load(opts.get("image_id"), opts.get("image_alt", ""))
            except NotFound as exc:
                raise AttachmentError(str(exc)) from exc
        elif kind == "link_card":
            url = post_link(target)
            if url:
                card = self.link_card(url)
            if card is None:
                warning = ("Posted without a preview card: the post has no link." if not url
                           else f"Posted without a preview card: couldn't read {url}.")
        extras = PostExtras(
            visibility=opts.get("visibility", "PUBLIC"),
            allow_reshare=opts.get("allow_reshare", True),
            image=image,
            link_card=card,
            first_comment=(opts.get("first_comment") or "").strip(),
            article=self.article_for(target),
            draft=bool(opts.get("draft")),
        )
        return extras, warning

    def link_card(self, url: str) -> LinkCard | None:
        meta = self._from_blog_cache(url) or self._from_page(url)
        if not meta or not meta.get("title"):
            return None
        description = meta.get("description") or ""
        if len(description) > MAX_DESCRIPTION:
            description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
        return LinkCard(url=url, title=meta["title"], description=description,
                        thumbnail=self._image(meta.get("image"), alt=meta["title"]))

    def _from_blog_cache(self, url: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT title, description, image FROM blog_posts WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def _from_page(self, url: str) -> dict[str, Any] | None:
        try:
            resp = self.fetch(url, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            return None
        return parse_post(resp.text, url)

    def _image(self, url: str | None, alt: str) -> ImageFile | None:
        """Download a preview picture. Anything odd (too big, not an image) means no picture."""
        if not url:
            return None
        try:
            resp = self.fetch(url, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            return None
        data = resp.content
        mime = sniff_image(data)
        return ImageFile(data, mime, alt) if mime and len(data) <= MAX_BYTES else None
