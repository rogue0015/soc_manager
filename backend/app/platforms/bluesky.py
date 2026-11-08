"""Bluesky adapter (AT Protocol): post, delete, link preview cards, likes / reposts / replies.

- Auth: your handle and an *app password* (never the account password). Each action opens a
  short session with com.atproto.server.createSession; there is no token to store or renew.
- Links and hashtags are only clickable on Bluesky when the post says where they are
  ("facets", in UTF-8 byte offsets), so the adapter adds them.
- A link preview card is an `app.bsky.embed.external` embed; its picture is uploaded as a blob
  (Bluesky takes up to 1 MB) and left out, with a warning, if it's too big.
- Stats come from the public AppView (app.bsky.feed.getPosts), which needs no sign-in.
See docs/decisions/0009-bluesky.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests

from .base import AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher

SERVICE = "https://bsky.social"            # sign-in and writes, for accounts hosted by Bluesky
PUBLIC = "https://public.api.bsky.app"     # public reads: profiles and post counts
POST = "app.bsky.feed.post"
MAX_BLOB = 1_000_000
TIMEOUT = 20
CONNECTOR = Connector(
    auth="Handle + app password",
    portal_url="https://bsky.app/settings/app-passwords",
    credentials=(Credential("BLUESKY_HANDLE", "Handle"), Credential("BLUESKY_APP_PASSWORD", "App password")),
    built=True,
    note="Create an app password under Settings → Privacy and security → App passwords. "
         "Never use your account password here.",
)
REQUIRED_KEYS = tuple(c.key for c in CONNECTOR.credentials)

_URL = re.compile(r"https?://[^\s<>\"]+")
_TAG = re.compile(r"(?:^|\s)(#[^\d\s#][^\s#]*)")
_TRAILING = ".,;:!?)]}'\""


def full_handle(handle: str) -> str:
    """'yourname' and '@yourname' mean yourname.bsky.social; custom domains stay as they are."""
    handle = handle.strip().lstrip("@").lower()
    return handle if "." in handle else f"{handle}.bsky.social"


def _byte_range(text: str, start: int, end: int) -> dict[str, int]:
    return {"byteStart": len(text[:start].encode()), "byteEnd": len(text[:end].encode())}


def facets(text: str) -> list[dict[str, Any]]:
    """Clickable links and hashtags, as Bluesky expects them."""
    out = []
    for m in _URL.finditer(text):
        url = m.group().rstrip(_TRAILING)
        out.append({"index": _byte_range(text, m.start(), m.start() + len(url)),
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]})
    for m in _TAG.finditer(text):
        tag = m.group(1).rstrip(_TRAILING)
        if 1 < len(tag) <= 65:
            out.append({"index": _byte_range(text, m.start(1), m.start(1) + len(tag)),
                        "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag[1:]}]})
    return out


def _explain(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    detail = body.get("message") or body.get("error") or resp.text[:200]
    if resp.status_code == 401:
        return ("Bluesky didn't accept the handle and app password. Check BLUESKY_HANDLE, and create a new "
                "app password if needed.")
    if resp.status_code == 429:
        return "Bluesky is limiting how fast the app can post. Wait a few minutes, then press Retry failed."
    return f"Bluesky answered HTTP {resp.status_code}: {detail}"


class BlueskyPublisher(Publisher):
    """`http` is the requests module; tests pass a fake with get/post."""

    def __init__(self, secrets: dict[str, str], http: Any = requests):
        missing = [k for k in REQUIRED_KEYS if not secrets.get(k)]
        if missing:
            raise ValueError(f"Missing Bluesky credentials: {', '.join(missing)}")
        self._handle = full_handle(secrets["BLUESKY_HANDLE"])
        self._password = secrets["BLUESKY_APP_PASSWORD"]
        self._service = (secrets.get("BLUESKY_SERVICE") or SERVICE).rstrip("/")
        self._http = http

    def _call(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            return getattr(self._http, method)(url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise PublishError(f"Couldn't reach Bluesky ({type(exc).__name__}). "
                               "Check the internet connection and try again.") from exc

    def _session(self) -> dict[str, Any]:
        resp = self._call("post", f"{self._service}/xrpc/com.atproto.server.createSession",
                          json={"identifier": self._handle, "password": self._password})
        if not resp.ok:
            raise PublishError(_explain(resp))
        return resp.json()

    def _xrpc(self, session: dict[str, Any], name: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {session['accessJwt']}", **kwargs.pop("headers", {})}
        resp = self._call("post", f"{self._service}/xrpc/{name}", headers=headers, **kwargs)
        if not resp.ok:
            raise PublishError(_explain(resp))
        return resp.json()

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        session = self._session()
        record: dict[str, Any] = {"$type": POST, "text": text,
                                  "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        if found := facets(text):
            record["facets"] = found
        warning = None
        card = extras.link_card if extras else None
        if card:
            external: dict[str, Any] = {"uri": card.url, "title": card.title, "description": card.description}
            thumb = card.thumbnail
            if thumb and len(thumb.data) <= MAX_BLOB:
                blob = self._xrpc(session, "com.atproto.repo.uploadBlob", data=thumb.data,
                                  headers={"Content-Type": thumb.mime})
                external["thumb"] = blob["blob"]
            elif thumb:
                warning = "Posted the preview card without its picture: Bluesky takes pictures up to 1 MB."
            record["embed"] = {"$type": "app.bsky.embed.external", "external": external}
        created = self._xrpc(session, "com.atproto.repo.createRecord",
                             json={"repo": session["did"], "collection": POST, "record": record})
        rkey = created["uri"].rsplit("/", 1)[1]
        return PublishResult(external_id=created["uri"],
                             external_url=f"https://bsky.app/profile/{session.get('handle', self._handle)}/post/{rkey}",
                             warning=warning)

    def delete(self, external_id: str) -> None:
        session = self._session()
        repo, collection, rkey = external_id.removeprefix("at://").split("/")
        self._xrpc(session, "com.atproto.repo.deleteRecord", json={"repo": repo, "collection": collection, "rkey": rkey})

    def check(self) -> AccountStatus:
        try:
            session = self._session()
        except PublishError as exc:
            return AccountStatus(False, False, None, str(exc))
        return AccountStatus(True, True, f"@{session.get('handle', self._handle)}", "Connected. Posting is available.")

    def stats_enabled(self) -> bool:
        return True

    def metrics(self, external_id: str) -> dict[str, int]:
        resp = self._call("get", f"{PUBLIC}/xrpc/app.bsky.feed.getPosts", params={"uris": external_id})
        if not resp.ok:
            raise PublishError(_explain(resp))
        posts = resp.json().get("posts") or []
        if not posts:
            raise PublishError("That post isn't on Bluesky any more.")
        p = posts[0]
        return {"reactions": p.get("likeCount", 0), "comments": p.get("replyCount", 0),
                "reshares": p.get("repostCount", 0) + p.get("quoteCount", 0)}
