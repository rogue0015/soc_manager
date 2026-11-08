"""Connector cards for the Status tab: how each platform connects and how far setup has got.

Built platforms describe their own connector next to their adapter (x.py,
linkedin.py, devto.py, bluesky.py); the ones without an adapter yet are described here. The report
only looks at which env keys are *present* and returns their names: values
never leave the backend. It makes no network calls; the live check is
GET /api/platforms/{id}/status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import bluesky, devto, linkedin, x
from .base import Connector, PlatformInfo, Publisher

META_APPS = "https://developers.facebook.com/apps"

CONNECTORS: dict[str, Connector] = {
    "x": x.CONNECTOR,
    "linkedin": linkedin.CONNECTOR,
    "devto": devto.CONNECTOR,
    "facebook": Connector(
        auth="Meta app with a Page access token",
        portal_url=META_APPS,
        note="Not built yet. Posts can go to a Facebook Page only, not a personal profile, "
             "and the Meta app needs review.",
    ),
    "instagram": Connector(
        auth="Meta app + Instagram professional account",
        portal_url=META_APPS,
        note="Not built yet. Needs a business or creator account linked to a Facebook Page, "
             "and every Instagram post needs an image or video.",
    ),
    "threads": Connector(
        auth="Meta app (Threads API)",
        portal_url=META_APPS,
        note="Not built yet. Posts through the Threads API with a Meta app.",
    ),
    "bluesky": bluesky.CONNECTOR,
    "mastodon": Connector(
        auth="Access token from your server",
        note="Not built yet. Create the token on your server under Preferences → Development, "
             "with the write:statuses scope.",
    ),
}

STATES = ("connected", "needs_sign_in", "needs_keys", "planned")


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _expiry(connector: Connector, secrets: dict[str, str]) -> datetime | None:
    raw = secrets.get(connector.expires_key or "")
    try:
        parsed = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None
    return parsed if parsed and parsed.tzinfo else None


def connector_status(platform: PlatformInfo, secrets: dict[str, str], connected: bool,
                     now: datetime, redirect_uri: str | None = None) -> dict[str, Any]:
    """One card: state, setup checklist (key names + whether set) and the next step in plain words."""
    c = CONNECTORS[platform.id]
    expires_at = _expiry(c, secrets)
    expired = bool(expires_at and expires_at <= now)
    checklist = [{"label": cred.label, "key": cred.key, "done": bool(secrets.get(cred.key))}
                 for cred in c.credentials]
    if c.token_key:
        checklist.append({"label": "Signed in", "key": c.token_key,
                          "done": bool(secrets.get(c.token_key)) and not expired})
    missing = [i["label"] for i in checklist if not i["done"] and i["key"] != c.token_key]

    expired_note = f"The sign-in expired on {expires_at:%d %b %Y}. " if expired else ""
    if not c.built:
        state, step = "planned", c.note
    elif connected and not expired:
        state, step = "connected", "Press Check to test the connection now."
    elif missing and not (c.token_key and secrets.get(c.token_key) and not expired):
        state = "needs_keys"
        step = f"{expired_note}Add the {_and(missing)} to the env file, then restart the app."
        step += f" After that: {c.sign_in}" if c.sign_in else ""
    else:
        state, step = "needs_sign_in", expired_note + (c.sign_in or "")
    # In-app sign-in is offered once the app keys are in place (see services/signin.py).
    keys_ready = not missing and redirect_uri is not None

    return {
        "platform": platform.id,
        "state": state,
        "built": c.built,
        "auth": c.auth,
        "portal_url": c.portal_url,
        "checklist": checklist,
        "next_step": step,
        "note": c.note,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "sign_in_url": f"/api/connect/{platform.id}" if keys_ready else None,
        "redirect_uri": redirect_uri,   # what to register with the platform for in-app sign-in
    }


def connector_report(platforms: dict[str, PlatformInfo], secrets: dict[str, str],
                     publishers: dict[str, Publisher], now: datetime,
                     redirect_uris: dict[str, str] | None = None) -> list[dict[str, Any]]:
    uris = redirect_uris or {}
    return [connector_status(p, secrets, p.id in publishers, now, uris.get(p.id)) for p in platforms.values()]
