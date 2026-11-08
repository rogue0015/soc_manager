"""YouTube adapter: uploads videos (Shorts and regular) with the YouTube Data API.

- Sign-in: Google OAuth from the Status tab (services/signin.py), asking for
  offline access so Google returns a refresh token (YOUTUBE_REFRESH_TOKEN).
  Access tokens last an hour; this adapter gets fresh ones as needed.
- Upload: the resumable protocol: one request with the details (title,
  description, tags, privacy), then the file itself to the address it returns.
- A vertical video of up to 3 minutes becomes a Short on its own; there is no
  separate Shorts API.
- Google locks uploads from projects it hasn't audited to *private*, whatever
  is asked for. The app says so; the owner makes them public in YouTube Studio.
- While the Google project's consent screen is in "Testing", Google expires the
  refresh token after 7 days; publishing the consent screen avoids that.

See docs/decisions/0011-media-video-cropper.md.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote, urlencode

import requests

from ..timeutil import now_utc, to_utc_iso
from .base import (
    AccountProfile, AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher,
    SignInError,
)

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
TIMEOUT = 30
UPLOAD_TIMEOUT = 60 * 30
TITLE_LIMIT = 100
SHORT_MAX_MS = 3 * 60 * 1000
PRIVATE_NOTE = ("YouTube keeps uploads from apps it hasn't audited private: "
                "make it public in YouTube Studio if you want.")
CONNECTOR = Connector(
    auth="Sign in with Google (OAuth); needs a Google Cloud project with the YouTube Data API",
    portal_url="https://console.cloud.google.com/apis/credentials",
    credentials=(
        Credential("YOUTUBE_CLIENT_ID", "OAuth client ID"),
        Credential("YOUTUBE_CLIENT_SECRET", "OAuth client secret"),
    ),
    sign_in="Press “Sign in with YouTube” and allow access.",
    token_key="YOUTUBE_REFRESH_TOKEN",
    built=True,
    note="Until Google audits your project, uploads stay private. Add the redirect URL below to the "
         "OAuth client (type: Web application).",
)


def _explain(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        detail = err.get("message") or err.get("status")
        reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)}
        if "quotaExceeded" in reasons:
            detail = "YouTube's daily upload quota for this project is used up. Try again tomorrow."
        elif "uploadLimitExceeded" in reasons:
            detail = "Your channel has reached YouTube's upload limit for today."
        return f"HTTP {resp.status_code}: {detail}"
    return f"HTTP {resp.status_code}: {(isinstance(body, dict) and (body.get('error_description') or err)) or body}"


def _unreachable(exc: requests.RequestException) -> str:
    return f"Couldn't reach YouTube ({type(exc).__name__}). Check the internet connection and try again."


# ---- sign-in (services/signin.py) ---------------------------------------------------

def sign_in_url(secrets: dict[str, str], redirect_uri: str, state: str) -> str:
    if not (secrets.get("YOUTUBE_CLIENT_ID") and secrets.get("YOUTUBE_CLIENT_SECRET")):
        raise ValueError("Add the YouTube OAuth client ID and secret to the env file, then restart the app.")
    query = urlencode({
        "client_id": secrets["YOUTUBE_CLIENT_ID"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "state": state,
        # offline + consent: Google only returns a refresh token when both are asked for
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
    }, quote_via=quote)
    return f"{AUTH}?{query}"


def finish_sign_in(secrets: dict[str, str], code: str, redirect_uri: str) -> dict[str, str]:
    try:
        resp = requests.post(TOKEN, data={
            "code": code, "client_id": secrets["YOUTUBE_CLIENT_ID"], "client_secret": secrets["YOUTUBE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        }, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SignInError(_unreachable(exc)) from exc
    if not resp.ok:
        raise SignInError(f"Google did not issue a token. {_explain(resp)}")
    body = resp.json()
    if not body.get("refresh_token"):
        raise SignInError("Google didn't send a refresh token. Remove the app's access at "
                          "myaccount.google.com/permissions, then sign in again.")
    return {"YOUTUBE_REFRESH_TOKEN": body["refresh_token"], "YOUTUBE_SIGNED_IN_AT": to_utc_iso(now_utc())}


# ---- publishing ---------------------------------------------------------------------

def video_title(text: str, title: str) -> str:
    """The given title, or the text's first line, cut to YouTube's limit."""
    chosen = (title or text.strip().split("\n", 1)[0]).strip() or "Video"
    return chosen if len(chosen) <= TITLE_LIMIT else chosen[: TITLE_LIMIT - 1].rstrip() + "…"


class YouTubePublisher(Publisher):
    stats_off_reason = "Sign in with YouTube again to allow reading view counts."

    def __init__(self, secrets: dict[str, str], http: Any = requests):
        missing = [k for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN") if not secrets.get(k)]
        if missing:
            raise ValueError(f"Missing YouTube credentials: {', '.join(missing)}")
        self._secrets = secrets
        self._http = http
        self._access: tuple[str, float] | None = None   # (access token, monotonic time it expires)

    def _call(self, method: str, url: str, timeout: int = TIMEOUT, **kwargs: Any) -> requests.Response:
        try:
            return getattr(self._http, method)(url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc

    def _token(self) -> str:
        if self._access and time.monotonic() < self._access[1]:
            return self._access[0]
        resp = self._call("post", TOKEN, data={
            "client_id": self._secrets["YOUTUBE_CLIENT_ID"], "client_secret": self._secrets["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": self._secrets["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token",
        })
        if not resp.ok:
            if resp.status_code in (400, 401):
                raise PublishError("Google no longer accepts the YouTube sign-in (it expires after 7 days while "
                                   "the consent screen is in Testing). Sign in with YouTube again on the Status tab.")
            raise PublishError(_explain(resp))
        body = resp.json()
        self._access = (body["access_token"], time.monotonic() + int(body.get("expires_in", 3600)) - 60)
        return self._access[0]

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        video = extras.video if extras else None
        if video is None:
            raise PublishError("YouTube only posts videos. Attach a video to this post.")
        privacy = (extras.privacy if extras.privacy in ("public", "unlisted", "private") else "public")
        meta = {
            "snippet": {"title": video_title(text, extras.title), "description": text,
                        "tags": list(extras.tags), "categoryId": "22"},   # 22 = People & Blogs
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        start = self._call("post", f"{UPLOAD}?uploadType=resumable&part=snippet,status", json=meta, headers={
            **self._auth(), "X-Upload-Content-Type": video.mime, "X-Upload-Content-Length": str(video.size)})
        if not start.ok or not start.headers.get("Location"):
            raise PublishError(f"YouTube didn't accept the upload. {_explain(start)}")
        with open(video.path, "rb") as f:
            done = self._call("put", start.headers["Location"], timeout=UPLOAD_TIMEOUT, data=f,
                              headers={**self._auth(), "Content-Type": video.mime, "Content-Length": str(video.size)})
        if done.status_code not in (200, 201):
            raise PublishError(f"Uploading the video to YouTube failed. {_explain(done)}")
        body = done.json()
        vid = body["id"]
        short = video.vertical and (video.duration_ms or SHORT_MAX_MS + 1) <= SHORT_MAX_MS
        url = f"https://www.youtube.com/shorts/{vid}" if short else f"https://youtu.be/{vid}"
        got = body.get("status", {}).get("privacyStatus")
        warning = PRIVATE_NOTE if privacy != "private" and got == "private" else None
        return PublishResult(external_id=vid, external_url=url, warning=warning)

    def _channel(self) -> dict[str, Any]:
        resp = self._call("get", f"{API}/channels?part=snippet,statistics&mine=true", headers=self._auth())
        if not resp.ok:
            raise PublishError(_explain(resp))
        items = resp.json().get("items") or []
        if not items:
            raise PublishError("This Google account has no YouTube channel yet. Create one on youtube.com.")
        return items[0]

    def check(self) -> AccountStatus:
        try:
            channel = self._channel()
        except PublishError as exc:
            return AccountStatus(False, False, None, str(exc))
        return AccountStatus(True, True, channel["snippet"].get("title"),
                             "Connected. Videos can be uploaded. " + PRIVATE_NOTE)

    def profile(self) -> AccountProfile:
        ch = self._channel()
        snip, stats = ch["snippet"], ch.get("statistics", {})
        handle = snip.get("customUrl")
        return AccountProfile(
            name=snip.get("title", "YouTube"), handle=handle,
            url=f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{ch['id']}",
            avatar=(snip.get("thumbnails", {}).get("default") or {}).get("url"),
            followers=None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
            posts=_int(stats.get("videoCount")), bio=snip.get("description", "")[:300],
        )

    def stats_enabled(self) -> bool:
        return True

    def metrics(self, external_id: str) -> dict[str, int]:
        resp = self._call("get", f"{API}/videos?part=statistics&id={quote(external_id)}", headers=self._auth())
        if not resp.ok:
            raise PublishError(_explain(resp))
        items = resp.json().get("items") or []
        if not items:
            raise PublishError("YouTube can't find that video; it may have been deleted.")
        s = items[0].get("statistics", {})
        return {k: v for k, v in (("views", _int(s.get("viewCount"))), ("reactions", _int(s.get("likeCount"))),
                                  ("comments", _int(s.get("commentCount")))) if v is not None}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

