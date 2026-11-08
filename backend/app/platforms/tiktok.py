"""TikTok adapter: posts videos with TikTok's Content Posting API (Direct Post).

- Sign-in: TikTok OAuth from the Status tab. Access tokens last 2 hours; the
  refresh token lasts 180 days and is *replaced* on every refresh, so each new
  one is saved straight back to the env file (base.save_tokens).
- Posting: ask which privacy levels the account allows (creator_info), start a
  post with the file size, upload the file in chunks, then wait for TikTok to
  finish. Apps TikTok hasn't audited can only post privately (SELF_ONLY); the
  app picks that automatically when "public" isn't offered.
- TikTok requires its redirect URL to use https, so signing in from
  http://localhost may be refused: see README "TikTok setup".

See docs/decisions/0011-media-video-cropper.md.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

import requests

from ..timeutil import now_utc, to_utc_iso
from .base import (
    AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher, SignInError,
    save_tokens,
)

AUTH = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ("user.info.basic", "video.publish")
TIMEOUT = 30
CHUNK = 10 * 1024 * 1024        # TikTok wants 5-64 MB chunks; the last one takes the remainder
MIN_CHUNK = 5 * 1024 * 1024
WAIT_SECONDS = 120
# "unlisted" (a YouTube idea) maps to the closest TikTok audience: your friends.
PRIVACY = {"public": "PUBLIC_TO_EVERYONE", "unlisted": "MUTUAL_FOLLOW_FRIENDS", "friends": "MUTUAL_FOLLOW_FRIENDS",
           "private": "SELF_ONLY"}
CONNECTOR = Connector(
    auth="Sign in with TikTok (OAuth); needs a TikTok developer app with Content Posting",
    portal_url="https://developers.tiktok.com/apps",
    credentials=(
        Credential("TIKTOK_CLIENT_KEY", "Client key"),
        Credential("TIKTOK_CLIENT_SECRET", "Client secret"),
    ),
    sign_in="Press “Sign in with TikTok” and allow access.",
    token_key="TIKTOK_REFRESH_TOKEN",
    expires_key="TIKTOK_REFRESH_EXPIRES_AT",
    built=True,
    note="Until TikTok audits your app, posts are visible only to you. TikTok asks for an https redirect URL.",
)


def _explain(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"HTTP {resp.status_code}: {err.get('message') or err.get('code')}"
    return f"HTTP {resp.status_code}: {(isinstance(body, dict) and (body.get('error_description') or err)) or body}"


def _unreachable(exc: requests.RequestException) -> str:
    return f"Couldn't reach TikTok ({type(exc).__name__}). Check the internet connection and try again."


def _token_values(body: dict[str, Any]) -> dict[str, str]:
    return {
        "TIKTOK_REFRESH_TOKEN": body["refresh_token"],
        "TIKTOK_REFRESH_EXPIRES_AT": to_utc_iso(now_utc() + timedelta(seconds=int(body.get("refresh_expires_in", 0)))),
        "TIKTOK_OPEN_ID": body.get("open_id", ""),
    }


# ---- sign-in (services/signin.py) ---------------------------------------------------

def sign_in_url(secrets: dict[str, str], redirect_uri: str, state: str) -> str:
    if not (secrets.get("TIKTOK_CLIENT_KEY") and secrets.get("TIKTOK_CLIENT_SECRET")):
        raise ValueError("Add the TikTok client key and secret to the env file, then restart the app.")
    query = urlencode({"client_key": secrets["TIKTOK_CLIENT_KEY"], "scope": ",".join(SCOPES),
                       "response_type": "code", "redirect_uri": redirect_uri, "state": state}, quote_via=quote)
    return f"{AUTH}?{query}"


def finish_sign_in(secrets: dict[str, str], code: str, redirect_uri: str) -> dict[str, str]:
    try:
        resp = requests.post(f"{API}/oauth/token/", data={
            "client_key": secrets["TIKTOK_CLIENT_KEY"], "client_secret": secrets["TIKTOK_CLIENT_SECRET"],
            "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SignInError(_unreachable(exc)) from exc
    body = resp.json() if resp.content else {}
    if not resp.ok or "refresh_token" not in body:
        raise SignInError(f"TikTok did not issue a token. {_explain(resp)}")
    return _token_values(body)


# ---- publishing ---------------------------------------------------------------------

def chunks(size: int) -> list[tuple[int, int]]:
    """(start, end exclusive) byte ranges for TikTok's chunked upload."""
    if size <= CHUNK + MIN_CHUNK:
        return [(0, size)]                       # small files go in one piece
    count = size // CHUNK                        # the remainder joins the last chunk
    return [(i * CHUNK, size if i == count - 1 else (i + 1) * CHUNK) for i in range(count)]


class TikTokPublisher(Publisher):
    def __init__(self, secrets: dict[str, str], http: Any = requests, sleep: Any = time.sleep):
        missing = [k for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN") if not secrets.get(k)]
        if missing:
            raise ValueError(f"Missing TikTok credentials: {', '.join(missing)}")
        self._secrets = secrets
        self._http = http
        self._sleep = sleep
        self._access: tuple[str, float] | None = None

    def _call(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            return getattr(self._http, method)(url, **kwargs)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc

    def _token(self) -> str:
        if self._access and time.monotonic() < self._access[1]:
            return self._access[0]
        resp = self._call("post", f"{API}/oauth/token/", data={
            "client_key": self._secrets["TIKTOK_CLIENT_KEY"], "client_secret": self._secrets["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token", "refresh_token": self._secrets["TIKTOK_REFRESH_TOKEN"],
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = resp.json() if resp.content else {}
        if not resp.ok or "access_token" not in body:
            raise PublishError(f"TikTok no longer accepts the sign-in. Sign in with TikTok again on the Status tab. "
                               f"({_explain(resp)})")
        # The old refresh token stops working now: keep the new one, in memory and in env.
        values = _token_values(body)
        self._secrets.update(values)
        save_tokens(values)
        self._access = (body["access_token"], time.monotonic() + int(body.get("expires_in", 7200)) - 60)
        return self._access[0]

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._call("post", f"{API}{path}", json=payload, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"})
        body = resp.json() if resp.content else {}
        code = (body.get("error") or {}).get("code", "ok") if isinstance(body, dict) else "ok"
        if not resp.ok or code != "ok":
            raise PublishError(_explain(resp))
        return body.get("data") or {}

    def _creator(self) -> dict[str, Any]:
        return self._post("/post/publish/creator_info/query/", {})

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        video = extras.video if extras else None
        if video is None:
            raise PublishError("TikTok only posts videos. Attach a video to this post.")
        creator = self._creator()
        allowed = creator.get("privacy_level_options") or ["SELF_ONLY"]
        username = creator.get("creator_username")
        limit = creator.get("max_video_post_duration_sec")
        if limit and video.duration_ms and video.duration_ms > limit * 1000:
            raise PublishError(f"TikTok lets this account post videos up to {limit // 60} min {limit % 60} s; "
                               f"this one is longer.")
        wanted = PRIVACY.get(extras.privacy or "public", "PUBLIC_TO_EVERYONE")
        privacy = wanted if wanted in allowed else ("SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0])
        parts = chunks(video.size)
        init = self._post("/post/publish/video/init/", {
            "post_info": {"title": text[:2200], "privacy_level": privacy,
                          "disable_comment": False, "disable_duet": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": video.size,
                            "chunk_size": parts[0][1] - parts[0][0], "total_chunk_count": len(parts)},
        })
        for start, end in parts:
            put = self._call("put", init["upload_url"], data=video.read(start, end), timeout=600, headers={
                "Content-Type": video.mime, "Content-Length": str(end - start),
                "Content-Range": f"bytes {start}-{end - 1}/{video.size}"})
            if put.status_code not in (200, 201, 206):
                raise PublishError(f"Uploading the video to TikTok failed. {_explain(put)}")
        status, post_id = self._wait(init["publish_id"])
        notes = []
        if privacy != wanted:
            notes.append("TikTok only allowed this as a private post (apps it hasn't audited post privately).")
        if status != "PUBLISH_COMPLETE":
            notes.append("TikTok is still processing the video; it will appear on your profile shortly.")
        return PublishResult(external_id=post_id or init["publish_id"],
                             external_url=(f"https://www.tiktok.com/@{username}/video/{post_id}"
                                           if post_id and username else None),
                             warning=" ".join(notes) or None)

    def _wait(self, publish_id: str) -> tuple[str, str | None]:
        """Poll until TikTok has the post, failed, or the wait runs out."""
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            data = self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status", "")
            if status == "FAILED":
                raise PublishError(f"TikTok couldn't publish the video ({data.get('fail_reason', 'no reason given')}).")
            if status == "PUBLISH_COMPLETE" or time.monotonic() > deadline:
                ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
                return status, str(ids[0]) if ids else None
            self._sleep(5)

    def check(self) -> AccountStatus:
        try:
            creator = self._creator()
        except PublishError as exc:
            return AccountStatus(False, False, None, str(exc))
        name = creator.get("creator_nickname") or creator.get("creator_username")
        private_only = creator.get("privacy_level_options") == ["SELF_ONLY"]
        return AccountStatus(True, True, name, "Connected. Videos can be posted"
                             + (", but only privately until TikTok audits the app." if private_only else "."))
