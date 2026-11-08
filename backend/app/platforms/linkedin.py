"""LinkedIn adapter: posts to your own feed with the Posts API (POST /rest/posts).

LinkedIn grants posting rights only through a browser sign-in (OAuth 2.0),
and gives personal apps no refresh tokens. So:

1. "Sign in with LinkedIn" on the Status tab (services/signin.py), or the
   fallback `scripts/linkedin_login.py`, writes a 60-day LINKEDIN_ACCESS_TOKEN
   and its expiry into the env file;
2. this adapter posts with that token until it expires, then asks you to
   sign in again.

The client ID and secret are only needed to sign in.
See docs/decisions/0003-linkedin-sign-in.md and 0005-sign-in-from-the-app.md.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import requests

from ..timeutil import now_utc, to_utc_iso
from .base import (
    SignInError, VideoFile,
    URL_RE, AccountProfile, AccountStatus, Connector, Credential, ImageFile, PostExtras, PublishError, PublishResult,
    Publisher,
)

API = "https://api.linkedin.com"
OAUTH = "https://www.linkedin.com/oauth/v2"
# The Posts API is versioned by month and LinkedIn retires each version about
# a year after release. If posts fail with HTTP 426, move this to a newer month.
API_VERSION = "202610"
# openid + profile tell us who signed in (the post's author); w_member_social
# allows posting, commenting, editing and deleting your own posts. They come from
# the app's "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn"
# products. LINKEDIN_SCOPES in env can change the list (e.g. to add STATS_SCOPE).
SCOPES = ("openid", "profile", "w_member_social")
# Per-post statistics. Only granted to apps approved for LinkedIn's Community
# Management API (registered companies). Stats switch on when a sign-in grants it.
STATS_SCOPE = "r_member_postAnalytics"
STATS_OFF = ("Automatic LinkedIn stats are off: they need LinkedIn's approval for its Community Management "
             "API, which is only for registered companies. Import your LinkedIn analytics file instead.")
# Our metric name -> LinkedIn's memberCreatorPostAnalytics queryType.
STAT_QUERIES = {
    "impressions": "IMPRESSION", "members_reached": "MEMBERS_REACHED", "reactions": "REACTION",
    "comments": "COMMENT", "reshares": "RESHARE", "saves": "POST_SAVE", "link_clicks": "LINK_CLICKS",
}
VISIBILITIES = ("PUBLIC", "CONNECTIONS")
VIDEO_MAX_BYTES = 500 * 1024 * 1024
VIDEO_WAIT_SECONDS = 300
DEFAULT_REDIRECT_URI = "http://localhost:8765/linkedin/callback"
TIMEOUT = 20
SIGN_IN_AGAIN = "Open the Status tab and sign in again on the LinkedIn card."
CONNECTOR = Connector(
    auth="Browser sign-in (OAuth 2.0); lasts 60 days",
    portal_url="https://www.linkedin.com/developers/apps",
    credentials=(
        Credential("LINKEDIN_CLIENT_ID", "Client ID"),
        Credential("LINKEDIN_CLIENT_SECRET", "Client secret"),
    ),
    sign_in="Press “Sign in with LinkedIn” and allow access.",
    token_key="LINKEDIN_ACCESS_TOKEN",
    expires_key="LINKEDIN_TOKEN_EXPIRES_AT",
    built=True,
    note="The app needs the “Share on LinkedIn” and “Sign In with LinkedIn using OpenID Connect” products.",
)
EXPIRY_WARNING = timedelta(days=7)

# Characters reserved by the "little" text format of the `commentary` field.
# Unescaped, LinkedIn can drop the rest of the post after one of them.
# "#word" is left alone so hashtags still work; inside links everything is escaped.
_RESERVED = re.compile(r"[\\|{}@\[\]()<>*_~]|#(?!\w)")
_RESERVED_IN_URL = re.compile(r"[\\|{}@\[\]()<>*_~#]")


def to_little_text(text: str) -> str:
    """Escape plain text so LinkedIn shows it exactly as typed."""
    parts: list[str] = []
    pos = 0
    for url in URL_RE.finditer(text):
        parts.append(_RESERVED.sub(r"\\\g<0>", text[pos:url.start()]))
        parts.append(_RESERVED_IN_URL.sub(r"\\\g<0>", url.group()))
        pos = url.end()
    parts.append(_RESERVED.sub(r"\\\g<0>", text[pos:]))
    return "".join(parts)


def _explain(resp: requests.Response) -> str:
    """Turn a LinkedIn error response into one readable sentence."""
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    if isinstance(body, dict):
        body = body.get("message") or body.get("error_description") or body.get("error") or body
    return f"HTTP {resp.status_code}: {body}"


def _unreachable(exc: requests.RequestException) -> str:
    # The full exception text is long and technical; its type tells a timeout from no connection.
    return f"Couldn't reach LinkedIn ({type(exc).__name__}). Check the internet connection and try again."


def _days(delta: timedelta) -> str:
    days = delta.days
    return "less than a day" if days < 1 else "1 day" if days == 1 else f"{days} days"


def _parse_time(value: str | None) -> datetime | None:
    """Parse the stored expiry. Unknown or malformed means "don't know", not "expired"."""
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return parsed if parsed and parsed.tzinfo else None


# ---- sign-in (used by services/signin.py and scripts/linkedin_login.py) ------



@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: datetime
    scopes: tuple[str, ...] = ()   # what LinkedIn actually granted


def _split_scopes(value: str | None) -> tuple[str, ...]:
    """LinkedIn lists scopes separated by commas or spaces."""
    return tuple(s for s in re.split(r"[,\s]+", value or "") if s)


def requested_scopes(secrets: dict[str, str]) -> tuple[str, ...]:
    return _split_scopes(secrets.get("LINKEDIN_SCOPES")) or SCOPES


def authorization_url(client_id: str, redirect_uri: str, state: str, scopes: tuple[str, ...] = SCOPES) -> str:
    """The LinkedIn page where you allow this app to post for you."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(scopes),
        },
        quote_via=quote,  # LinkedIn expects %20 between scopes, not "+"
    )
    return f"{OAUTH}/authorization?{query}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  now: Callable[[], datetime] = now_utc) -> Token:
    """Swap the one-time code from the redirect for an access token."""
    try:
        resp = requests.post(
            f"{OAUTH}/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SignInError(_unreachable(exc)) from exc
    if not resp.ok:
        raise SignInError(f"LinkedIn did not issue a token. {_explain(resp)}")
    body = resp.json()
    return Token(body["access_token"], now() + timedelta(seconds=int(body["expires_in"])),
                 _split_scopes(body.get("scope")))


def sign_in_url(secrets: dict[str, str], redirect_uri: str, state: str) -> str:
    """Where to send the browser to start signing in. ValueError if the app keys are missing."""
    if not (secrets.get("LINKEDIN_CLIENT_ID") and secrets.get("LINKEDIN_CLIENT_SECRET")):
        raise ValueError("Add the LinkedIn Client ID and Client secret to the env file, then restart the app.")
    return authorization_url(secrets["LINKEDIN_CLIENT_ID"], redirect_uri, state, requested_scopes(secrets))


def finish_sign_in(secrets: dict[str, str], code: str, redirect_uri: str,
                   now: Callable[[], datetime] = now_utc) -> dict[str, str]:
    """Swap the code from LinkedIn's redirect for a token. Returns the env values to save."""
    token = exchange_code(secrets["LINKEDIN_CLIENT_ID"], secrets["LINKEDIN_CLIENT_SECRET"], code, redirect_uri, now)
    return {"LINKEDIN_ACCESS_TOKEN": token.access_token,
            "LINKEDIN_TOKEN_EXPIRES_AT": to_utc_iso(token.expires_at),
            "LINKEDIN_GRANTED_SCOPES": " ".join(token.scopes)}


# ---- posting and managing posts ------------------------------------------------

def _path_urn(urn: str) -> str:
    """A post URN as one URL path segment (urn:li:share:1 -> urn%3Ali%3Ashare%3A1)."""
    return quote(urn, safe="")


class LinkedInPublisher(Publisher):
    stats_off_reason = STATS_OFF

    def __init__(self, secrets: dict[str, str], now: Callable[[], datetime] = now_utc):
        if not secrets.get("LINKEDIN_ACCESS_TOKEN"):
            raise ValueError("No LinkedIn access token: sign in from the Status tab")
        self._token = secrets["LINKEDIN_ACCESS_TOKEN"]
        self._expires_at = _parse_time(secrets.get("LINKEDIN_TOKEN_EXPIRES_AT"))
        self._granted = set(_split_scopes(secrets.get("LINKEDIN_GRANTED_SCOPES")))
        self._now = now
        self._sleep = time.sleep
        self._member: dict | None = None  # who the token belongs to; fixed for its lifetime

    def _expired(self) -> str | None:
        if self._expires_at and self._now() >= self._expires_at:
            return f"The LinkedIn sign-in expired on {self._expires_at:%d %b %Y}. {SIGN_IN_AGAIN}"
        return None

    def _ready(self) -> None:
        if expired := self._expired():
            raise PublishError(expired)

    def _headers(self, method: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if method:
            headers["X-RestLi-Method"] = method
        return headers

    def _userinfo(self) -> requests.Response:
        return requests.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {self._token}"},
                            timeout=TIMEOUT)

    @staticmethod
    def _userinfo_problem(resp: requests.Response) -> str:
        if resp.status_code == 401:
            return f"LinkedIn no longer accepts this sign-in. {SIGN_IN_AGAIN}"
        if resp.status_code == 403:
            return ("LinkedIn won't say who is signed in. Add the \"Sign In with LinkedIn using "
                    "OpenID Connect\" product to the app, then sign in again.")
        return f"LinkedIn could not confirm who is signed in. {_explain(resp)}"

    def _author(self) -> str:
        if self._member is None:
            resp = self._userinfo()
            if not resp.ok:
                raise PublishError(self._userinfo_problem(resp))
            self._member = resp.json()
        return f"urn:li:person:{self._member['sub']}"

    # ---- publish ----

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        extras = extras or PostExtras()
        self._ready()
        warnings: list[str] = []
        try:
            author = self._author()
            body = {
                "author": author,
                "commentary": to_little_text(text),
                "visibility": extras.visibility if extras.visibility in VISIBILITIES else "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": not extras.allow_reshare,
            }
            if extras.video:
                media = {"id": self._upload_video(extras.video, author)}
                if extras.title:
                    media["title"] = extras.title
                body["content"] = {"media": media}
            elif extras.image:
                media = {"id": self._upload_image(extras.image, author)}
                if extras.image.alt:
                    media["altText"] = extras.image.alt
                body["content"] = {"media": media}
            elif extras.link_card:
                card = extras.link_card
                article = {"source": card.url, "title": card.title}
                if card.description:
                    article["description"] = card.description
                if card.thumbnail:
                    try:
                        article["thumbnail"] = self._upload_image(card.thumbnail, author)
                    except PublishError as exc:  # a card without a picture beats no post
                        warnings.append(f"The preview picture couldn't be added ({exc}).")
                body["content"] = {"article": article}
            resp = requests.post(f"{API}/rest/posts", headers=self._headers(), json=body, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc

        if resp.status_code != 201:
            raise PublishError(self._problem(resp))
        # The post is live at this point, so nothing below may count as a failure
        # (a retry would post it twice): a missing ID or a failed comment is a warning.
        urn = resp.headers.get("x-restli-id", "")
        url = f"https://www.linkedin.com/feed/update/{urn}/" if urn else None
        if extras.first_comment and urn:
            try:
                self.comment(urn, extras.first_comment)
            except PublishError as exc:
                warnings.append(f"The first comment wasn't added: {exc}")
        return PublishResult(external_id=urn, external_url=url, warning=" ".join(warnings) or None)

    def _upload_video(self, video: VideoFile, owner: str) -> str:
        """Upload a video in the parts LinkedIn asks for, then wait until it's processed.
        Returns its urn:li:video for the post."""
        if video.size > VIDEO_MAX_BYTES:
            raise PublishError(f"LinkedIn takes videos up to 500 MB; this one is {video.size / 1048576:.0f} MB.")
        if video.duration_ms and not 3000 <= video.duration_ms <= 30 * 60 * 1000:
            raise PublishError("LinkedIn takes videos from 3 seconds to 30 minutes long.")
        init = requests.post(f"{API}/rest/videos?action=initializeUpload", headers=self._headers(), timeout=TIMEOUT,
                             json={"initializeUploadRequest": {"owner": owner, "fileSizeBytes": video.size,
                                                               "uploadCaptions": False, "uploadThumbnail": False}})
        if not init.ok:
            raise PublishError(f"LinkedIn didn't accept the video. {self._problem(init)}")
        value = init.json()["value"]
        etags = []
        for part in value["uploadInstructions"]:
            put = requests.put(part["uploadUrl"], data=video.read(part["firstByte"], part["lastByte"] + 1),
                               headers={"Content-Type": "application/octet-stream"}, timeout=300)
            if not put.ok or not put.headers.get("etag"):
                raise PublishError(f"Uploading the video to LinkedIn failed. {_explain(put)}")
            etags.append(put.headers["etag"])
        done = requests.post(f"{API}/rest/videos?action=finalizeUpload", headers=self._headers(), timeout=TIMEOUT,
                             json={"finalizeUploadRequest": {"video": value["video"],
                                                             "uploadToken": value.get("uploadToken", ""),
                                                             "uploadedPartIds": etags}})
        if not done.ok:
            raise PublishError(f"LinkedIn couldn't finish the video upload. {self._problem(done)}")
        self._wait_for_video(value["video"])
        return value["video"]

    def _wait_for_video(self, urn: str) -> None:
        """LinkedIn refuses a post whose video is still processing, so wait (up to a few minutes)."""
        deadline = time.monotonic() + VIDEO_WAIT_SECONDS
        while True:
            resp = requests.get(f"{API}/rest/videos/{_path_urn(urn)}", headers=self._headers(), timeout=TIMEOUT)
            status = resp.json().get("status") if resp.ok else None
            if status == "AVAILABLE":
                return
            if status == "PROCESSING_FAILED":
                reason = resp.json().get("processingFailureReason", "no reason given")
                raise PublishError(f"LinkedIn couldn't process the video ({reason}). Check it's an MP4 file.")
            if time.monotonic() > deadline:
                raise PublishError("LinkedIn is taking too long to process the video. Retry in a few minutes.")
            self._sleep(5)

    def _upload_image(self, image: ImageFile, owner: str) -> str:
        """Upload an image owned by you; returns its urn:li:image for use in a post."""
        try:
            init = requests.post(f"{API}/rest/images?action=initializeUpload", headers=self._headers(),
                                 json={"initializeUploadRequest": {"owner": owner}}, timeout=TIMEOUT)
            if not init.ok:
                raise PublishError(f"LinkedIn didn't accept the image. {self._problem(init)}")
            value = init.json()["value"]
            put = requests.put(value["uploadUrl"], data=image.data, timeout=60,
                               headers={"Authorization": f"Bearer {self._token}", "Content-Type": image.mime})
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        if not put.ok:
            raise PublishError(f"Uploading the image to LinkedIn failed. {_explain(put)}")
        return value["image"]

    # ---- actions on live posts ----

    def comment(self, urn: str, text: str) -> None:
        """Comment on a post as yourself (used for the first comment)."""
        self._ready()
        try:
            resp = requests.post(f"{API}/rest/socialActions/{_path_urn(urn)}/comments", headers=self._headers(),
                                 json={"actor": self._author(), "object": urn, "message": {"text": text}},
                                 timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        if resp.status_code not in (200, 201):
            raise PublishError(self._problem(resp))

    def delete(self, external_id: str) -> None:
        """Delete the post on LinkedIn. LinkedIn treats deleting twice as success."""
        self._ready()
        try:
            resp = requests.delete(f"{API}/rest/posts/{_path_urn(external_id)}",
                                   headers=self._headers("DELETE"), timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        if resp.status_code not in (200, 204):
            raise PublishError(self._problem(resp))

    def edit(self, external_id: str, text: str) -> None:
        """Replace the text of a live post (LinkedIn marks it as edited)."""
        self._ready()
        try:
            resp = requests.post(f"{API}/rest/posts/{_path_urn(external_id)}",
                                 headers=self._headers("PARTIAL_UPDATE"),
                                 json={"patch": {"$set": {"commentary": to_little_text(text)}}}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        if resp.status_code not in (200, 204):
            raise PublishError(self._problem(resp))

    def stats_enabled(self) -> bool:
        return STATS_SCOPE in self._granted

    def metrics(self, external_id: str) -> dict[str, int]:
        """Lifetime statistics of one post (one request per metric, as the API requires)."""
        if not self.stats_enabled():
            raise PublishError(STATS_OFF)
        self._ready()
        kind = {"urn:li:share:": "share", "urn:li:ugcPost:": "ugc"}.get(external_id[: external_id.rfind(":") + 1])
        if not kind:
            raise PublishError(f"LinkedIn can't report on this post ID: {external_id}")
        entity = f"({kind}:{_path_urn(external_id)})"
        out: dict[str, int] = {}
        try:
            for name, query in STAT_QUERIES.items():
                resp = requests.get(f"{API}/rest/memberCreatorPostAnalytics?q=entity&entity={entity}"
                                    f"&queryType={query}&aggregation=TOTAL", headers=self._headers(), timeout=TIMEOUT)
                if not resp.ok:
                    raise PublishError(self._problem(resp))
                out[name] = sum(int(e.get("count", 0)) for e in resp.json().get("elements", []))
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        return out

    @staticmethod
    def _problem(resp: requests.Response) -> str:
        detail = _explain(resp).rstrip(".")
        if resp.status_code == 401:
            return f"{detail}. LinkedIn no longer accepts this sign-in. {SIGN_IN_AGAIN}"
        if resp.status_code == 403:
            return (f"{detail}. LinkedIn refused this for your app. Check that it has the \"Share on LinkedIn\" "
                    "product, then sign in again.")
        if resp.status_code == 404:
            return f"{detail}. LinkedIn can't find that post; it may have been deleted on LinkedIn."
        if resp.status_code == 426:
            return (f"{detail}. LinkedIn retired API version {API_VERSION}: "
                    "set API_VERSION in backend/app/platforms/linkedin.py to a recent month.")
        return detail

    def profile(self) -> AccountProfile:
        """Name and picture from OpenID userinfo. LinkedIn shares no handle or follower numbers with
        this sign-in; linkedin.com/in/me/ opens your own profile when you're signed in there."""
        self._ready()
        try:
            resp = self._userinfo()
        except requests.RequestException as exc:
            raise PublishError(_unreachable(exc)) from exc
        if not resp.ok:
            raise PublishError(self._userinfo_problem(resp))
        self._member = me = resp.json()
        return AccountProfile(name=me.get("name") or "LinkedIn member", url="https://www.linkedin.com/in/me/",
                              avatar=me.get("picture"))

    def check(self) -> AccountStatus:
        """Confirm the token works and say how long it stays valid. Posts nothing."""
        if expired := self._expired():
            return AccountStatus(False, False, None, expired)
        try:
            resp = self._userinfo()
        except requests.RequestException as exc:
            return AccountStatus(False, False, None, _unreachable(exc))
        if not resp.ok:
            return AccountStatus(False, False, None, self._userinfo_problem(resp))

        self._member = resp.json()
        message = "Connected. Posting is available."
        if self._expires_at:
            left = self._expires_at - self._now()
            if left < EXPIRY_WARNING:
                message += f" The sign-in expires in {_days(left)}. {SIGN_IN_AGAIN}"
            else:
                message += f" Sign-in valid for {_days(left)}."
        if self.stats_enabled():
            message += " Post stats: on."
        return AccountStatus(True, True, self._member.get("name"), message)
