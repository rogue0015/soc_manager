"""Instagram adapter: posts Reels with the Instagram API with Instagram Login.

Built but inactive until a sign-in works (ADR 0011). That needs:
- a Meta app with the "Instagram API with Instagram Login" use case, its
  Instagram app ID and secret (INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET);
- an Instagram professional (business or creator) account;
- Meta's review for instagram_business_content_publish to post for real;
- an https redirect URL: Meta refuses http ones, so signing in from
  http://localhost needs a tunnel or hosting.

Posting a Reel: create a container with upload_type=resumable, send the file
to Meta's upload host (no public URL needed, unlike photos), wait until the
container is FINISHED, then publish it. Photos are not offered: Instagram only
fetches them from a public web address, which this app doesn't have.
The long-lived token lasts 60 days, like LinkedIn's.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

import requests

from ..timeutil import now_utc, to_utc_iso
from .base import AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher, SignInError

AUTH = "https://www.instagram.com/oauth/authorize"
SHORT_TOKEN = "https://api.instagram.com/oauth/access_token"
GRAPH_VERSION = "v23.0"   # Meta versions its Graph API; move forward when this one is retired
GRAPH = f"https://graph.instagram.com/{GRAPH_VERSION}"
UPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"
SCOPES = ("instagram_business_basic", "instagram_business_content_publish")
TIMEOUT = 30
WAIT_SECONDS = 180
CONNECTOR = Connector(
    auth="Instagram Login (OAuth); needs a Meta app and an Instagram business or creator account",
    portal_url="https://developers.facebook.com/apps",
    credentials=(
        Credential("INSTAGRAM_APP_ID", "Instagram app ID"),
        Credential("INSTAGRAM_APP_SECRET", "Instagram app secret"),
    ),
    sign_in="Press “Sign in with Instagram” and allow access.",
    token_key="INSTAGRAM_ACCESS_TOKEN",
    expires_key="INSTAGRAM_TOKEN_EXPIRES_AT",
    built=True,
    note="Reels only. Meta must review the app before it posts for real, and it needs an https redirect URL.",
)


def _explain(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"HTTP {resp.status_code}: {err.get('error_user_msg') or err.get('message')}"
    return f"HTTP {resp.status_code}: {(isinstance(body, dict) and (body.get('error_message') or err)) or body}"


def _unreachable(exc: requests.RequestException) -> str:
    return f"Couldn't reach Instagram ({type(exc).__name__}). Check the internet connection and try again."


# ---- sign-in (services/signin.py) ---------------------------------------------------

def sign_in_url(secrets: dict[str, str], redirect_uri: str, state: str) -> str:
    if not (secrets.get("INSTAGRAM_APP_ID") and secrets.get("INSTAGRAM_APP_SECRET")):
        raise ValueError("Add the Instagram app ID and secret to the env file, then restart the app.")
    query = urlencode({"client_id": secrets["INSTAGRAM_APP_ID"], "redirect_uri": redirect_uri,
                       "response_type": "code", "scope": ",".join(SCOPES), "state": state}, quote_via=quote)
    return f"{AUTH}?{query}"


def finish_sign_in(secrets: dict[str, str], code: str, redirect_uri: str) -> dict[str, str]:
    try:
        short = requests.post(SHORT_TOKEN, data={
            "client_id": secrets["INSTAGRAM_APP_ID"], "client_secret": secrets["INSTAGRAM_APP_SECRET"],
            "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code,
        }, timeout=TIMEOUT)
        if not short.ok:
            raise SignInError(f"Instagram did not issue a token. {_explain(short)}")
        first = short.json()
        # Swap the 1-hour token for a 60-day one.
        long = requests.get(f"{GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": secrets["INSTAGRAM_APP_SECRET"],
            "access_token": first["access_token"]}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SignInError(_unreachable(exc)) from exc
    if not long.ok:
        raise SignInError(f"Instagram did not extend the sign-in. {_explain(long)}")
    body = long.json()
    return {"INSTAGRAM_ACCESS_TOKEN": body["access_token"], "INSTAGRAM_USER_ID": str(first.get("user_id", "")),
            "INSTAGRAM_TOKEN_EXPIRES_AT": to_utc_iso(now_utc() + timedelta(seconds=int(body.get("expires_in", 0))))}


# ---- publishing ---------------------------------------------------------------------

class InstagramPublisher(Publisher):
    def __init__(self, secrets: dict[str, str], http: Any = requests, sleep: Any = time.sleep):
        missing = [k for k in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID") if not secrets.get(k)]
        if missing:
            raise ValueError(f"Missing Instagram sign-in: {', '.join(missing)}")
        self._token = secrets["INSTAGRAM_ACCESS_TOKEN"]
        self._user = secrets["INSTAGRAM_USER_ID"]
        self._http = http
        self._sleep = sleep

    def _call(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            return getattr(self._http, method)(url, **kwargs)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc

    def _json(self, resp: requests.Response) -> dict[str, Any]:
        if not resp.ok:
            raise PublishError(_explain(resp))
        return resp.json()

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        video = extras.video if extras else None
        if video is None:
            raise PublishError("Instagram posts from this app are Reels: attach a video.")
        container = self._json(self._call("post", f"{GRAPH}/{self._user}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": text, "access_token": self._token}))
        with open(video.path, "rb") as f:
            sent = self._call("post", f"{UPLOAD}/{container['id']}", data=f, timeout=60 * 30, headers={
                "Authorization": f"OAuth {self._token}", "offset": "0", "file_size": str(video.size)})
        if not sent.ok:
            raise PublishError(f"Uploading the video to Instagram failed. {_explain(sent)}")
        self._wait(container["id"])
        published = self._json(self._call("post", f"{GRAPH}/{self._user}/media_publish", data={
            "creation_id": container["id"], "access_token": self._token}))
        media_id = published["id"]
        link = self._call("get", f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": self._token})
        url = link.json().get("permalink") if link.ok else None
        return PublishResult(external_id=media_id, external_url=url)

    def _wait(self, container_id: str) -> None:
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            state = self._json(self._call("get", f"{GRAPH}/{container_id}", params={
                "fields": "status_code,status", "access_token": self._token}))
            code = state.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram couldn't process the video ({state.get('status') or code}).")
            if time.monotonic() > deadline:
                raise PublishError("Instagram is taking too long to process the video. Try again later.")
            self._sleep(5)

    def check(self) -> AccountStatus:
        try:
            me = self._json(self._call("get", f"{GRAPH}/me", params={"fields": "username", "access_token": self._token}))
        except PublishError as exc:
            return AccountStatus(False, False, None, str(exc))
        return AccountStatus(True, True, f"@{me.get('username', '?')}", "Connected. Reels can be posted.")
