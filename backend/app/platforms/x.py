"""X (Twitter) adapter.

Posts as the account that owns X_ACCESS_TOKEN using OAuth 1.0a user
context, via POST /2/tweets. Reading is not used: the current access
level blocks most read endpoints (see README "X API access").
"""

from __future__ import annotations

import requests
from requests_oauthlib import OAuth1

from .base import AccountStatus, Connector, Credential, PostExtras, PublishError, PublishResult, Publisher

API = "https://api.x.com"
CONNECTOR = Connector(
    auth="API keys (OAuth 1.0a), with “Read and write” permission",
    portal_url="https://developer.x.com/en/portal/dashboard",
    credentials=(
        Credential("X_API_KEY", "API key"),
        Credential("X_API_SECRET", "API key secret"),
        Credential("X_ACCESS_TOKEN", "Access token"),
        Credential("X_ACCESS_TOKEN_SECRET", "Access token secret"),
    ),
    built=True,
    note="Keys are under your app → Keys and tokens. The app must be attached to a Project to post.",
)
REQUIRED_KEYS = tuple(c.key for c in CONNECTOR.credentials)
TIMEOUT = 20


def _explain(resp: requests.Response) -> str:
    """Turn an X error response into one readable sentence."""
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    detail = body.get("detail") or body.get("title")
    if not detail and body.get("errors"):
        detail = "; ".join(e.get("message", str(e)) for e in body["errors"])
    return f"HTTP {resp.status_code}: {detail or body}"


class XPublisher(Publisher):
    def __init__(self, secrets: dict[str, str]):
        missing = [k for k in REQUIRED_KEYS if not secrets.get(k)]
        if missing:
            raise ValueError(f"Missing X credentials: {', '.join(missing)}")
        self._auth = OAuth1(
            secrets["X_API_KEY"],
            secrets["X_API_SECRET"],
            secrets["X_ACCESS_TOKEN"],
            secrets["X_ACCESS_TOKEN_SECRET"],
        )

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        # Text only for now: images on X need its media upload API (planned).
        try:
            resp = requests.post(f"{API}/2/tweets", json={"text": text}, auth=self._auth, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PublishError(f"Network error talking to X: {exc}") from exc
        if resp.status_code != 201:
            raise PublishError(_explain(resp))
        tweet_id = resp.json()["data"]["id"]
        return PublishResult(external_id=tweet_id, external_url=f"https://x.com/i/web/status/{tweet_id}")

    def check(self) -> AccountStatus:
        """Confirm the credentials, then whether the v2 API (used for posting) is open."""
        try:
            who = requests.get(f"{API}/1.1/account/verify_credentials.json", auth=self._auth, timeout=TIMEOUT)
            if not who.ok:
                return AccountStatus(False, False, None, f"Credentials rejected. {_explain(who)}")
            account = "@" + who.json().get("screen_name", "?")
            v2 = requests.get(f"{API}/2/users/me", auth=self._auth, timeout=TIMEOUT)
        except requests.RequestException as exc:
            return AccountStatus(False, False, None, f"Network error talking to X: {exc}")

        if v2.ok:
            return AccountStatus(True, True, account, "Connected. Posting is available.")
        if "attached to a Project" in v2.text:
            return AccountStatus(
                True, False, account,
                "Signed in, but X blocks posting: the developer app is not attached to a Project. "
                "Fix it at developer.x.com → Projects & Apps.",
            )
        return AccountStatus(True, False, account, f"Signed in, but the posting API is unavailable. {_explain(v2)}")
