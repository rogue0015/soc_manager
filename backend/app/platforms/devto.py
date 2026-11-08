"""dev.to adapter: publishes whole blog posts as articles.

dev.to is the one platform that takes an article rather than a short post, so it
ignores the post text and publishes PostExtras.article: the blog post as Markdown
(services/article.py), with canonical_url pointing at the blog so search engines
credit the original. See docs/decisions/0007-devto-articles.md.

- Auth: a personal API key, sent as the `api-key` header. No sign-in, no expiry.
- No duplicates: before creating an article it looks for one of yours with the
  same canonical URL (a second send, an RSS import, a publish that was cut off)
  and updates that one instead. An update never takes a live article down.
- Stats: views, reactions and comments come from /articles/me/all, fetched once
  per refresh and shared by all your articles. Views are stored as impressions.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import requests

from .base import AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher

API = "https://dev.to/api"
CONNECTOR = Connector(
    auth="Personal API key",
    portal_url="https://dev.to/settings/extensions",
    credentials=(Credential("DEVTO_API_KEY", "API key"),),
    built=True,
    note="Create the key on dev.to under Settings → Extensions → DEV Community API Keys. "
         "dev.to gets whole blog posts, so it works with “From my blog” only.",
)
HEADERS = {
    "Accept": "application/vnd.forem.api-v1+json",
    "User-Agent": "SocialManager/1.0 (personal cross-posting tool)",
}
TIMEOUT = 20
MAX_TAGS = 4
LIST_TTL = 60   # seconds: a stats refresh reads your article list once, not once per article

NOT_AN_ARTICLE = "dev.to publishes whole blog posts. Schedule a blog post (“From my blog”) for it instead."


def devto_tags(tags: tuple[str, ...] | list[str]) -> str:
    """dev.to tags are lowercase letters and digits only, at most four, sent comma-separated."""
    out: list[str] = []
    for tag in tags:
        clean = re.sub(r"[^a-z0-9]", "", tag.lower())
        if clean and clean not in out:
            out.append(clean)
    return ", ".join(out[:MAX_TAGS])


def escape_liquid(markdown: str) -> str:
    """dev.to runs Liquid templates over articles, so a literal `{{` or `{%` would break the post."""
    if "{{" in markdown or "{%" in markdown:
        return "{% raw %}\n" + markdown + "\n{% endraw %}"
    return markdown


def _same_url(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().rstrip("/") == b.strip().rstrip("/")


def _explain(resp: requests.Response) -> str:
    """Turn a dev.to error response into one readable sentence."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    detail = (body.get("error") if isinstance(body, dict) else None) or resp.text[:200]
    if resp.status_code == 401:
        return ("dev.to didn't accept the API key. Create a new one under Settings → Extensions "
                "and put it in the env file as DEVTO_API_KEY.")
    if resp.status_code == 429:
        return "dev.to is limiting how fast articles can be sent. Wait a minute, then press Retry failed."
    if resp.status_code == 422:
        return f"dev.to didn't accept the article: {detail}"
    return f"dev.to answered HTTP {resp.status_code}: {detail}"


class DevtoPublisher(Publisher):
    """`http` is the requests module; tests pass a fake with get/post/put."""

    def __init__(self, secrets: dict[str, str], http: Any = requests,
                 clock: Callable[[], float] = time.monotonic):
        key = secrets.get("DEVTO_API_KEY")
        if not key:
            raise ValueError("Missing DEVTO_API_KEY")
        self._headers = {**HEADERS, "api-key": key}
        self._http = http
        self._clock = clock
        self._mine: tuple[float, list[dict[str, Any]]] | None = None

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            return getattr(self._http, method)(f"{API}{path}", headers=self._headers, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise PublishError(f"Couldn't reach dev.to ({type(exc).__name__}). "
                               "Check the internet connection and try again.") from exc

    def _my_articles(self, fresh: bool = False) -> list[dict[str, Any]]:
        """Your articles, drafts included."""
        now = self._clock()
        if not fresh and self._mine and now - self._mine[0] < LIST_TTL:
            return self._mine[1]
        resp = self._call("get", "/articles/me/all", params={"per_page": 1000})
        if not resp.ok:
            raise PublishError(_explain(resp))
        self._mine = (now, resp.json())
        return self._mine[1]

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        article = extras.article if extras else None
        if article is None:
            raise PublishError(NOT_AN_ARTICLE)
        draft = bool(extras and extras.draft)
        payload: dict[str, Any] = {
            "title": article.title,
            "body_markdown": escape_liquid(article.markdown),
            "description": article.description,
            "canonical_url": article.canonical_url,
            "tags": devto_tags(article.tags),
        }
        existing = next((a for a in self._my_articles(fresh=True)
                         if _same_url(a.get("canonical_url"), article.canonical_url)), None)
        notes = []
        if existing:
            live = bool(existing.get("published"))
            if not (draft and live):   # never take a live article down
                payload["published"] = not draft
            resp = self._call("put", f"/articles/{existing['id']}", json={"article": payload})
            notes.append("Updated the article you already had on dev.to for this blog post.")
            if draft and live:
                notes.append("It was already live, so it stays published.")
            elif draft:
                notes.append("It's still a draft there.")
        else:
            payload["published"] = not draft
            resp = self._call("post", "/articles", json={"article": payload})
            if draft:
                notes.append("Saved as a draft on dev.to. Publish it there when you're ready.")
        if resp.status_code not in (200, 201):
            raise PublishError(_explain(resp))
        self._mine = None   # the list changed
        body = resp.json()
        return PublishResult(external_id=str(body["id"]), external_url=body.get("url"),
                             warning=" ".join(notes) or None)

    def check(self) -> AccountStatus:
        try:
            resp = self._call("get", "/users/me")
        except PublishError as exc:
            return AccountStatus(False, False, None, str(exc))
        if not resp.ok:
            return AccountStatus(False, False, None, _explain(resp))
        user = resp.json().get("username")
        return AccountStatus(True, True, f"@{user}" if user else None,
                             "Connected. Blog posts can be published as articles.")

    def stats_enabled(self) -> bool:
        return True

    def metrics(self, external_id: str) -> dict[str, int]:
        for a in self._my_articles():
            if str(a.get("id")) == str(external_id):
                found = {"impressions": a.get("page_views_count"), "reactions": a.get("public_reactions_count"),
                         "comments": a.get("comments_count")}
                return {k: int(v) for k, v in found.items() if v is not None}
        raise PublishError("That article isn't on your dev.to account any more.")
