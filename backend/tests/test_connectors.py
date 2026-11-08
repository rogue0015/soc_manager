"""Connector cards on the Status tab: setup state per platform, without exposing key values."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.platforms import PLATFORMS
from backend.app.platforms.connectors import CONNECTORS, connector_report
from backend.app.timeutil import now_utc

NOW = now_utc()
X_KEYS = {"X_API_KEY": "k1", "X_API_SECRET": "k2", "X_ACCESS_TOKEN": "k3", "X_ACCESS_TOKEN_SECRET": "k4"}


def card(platform: str, secrets: dict, connected: tuple[str, ...] = ()) -> dict:
    report = connector_report(PLATFORMS, secrets, {p: object() for p in connected}, NOW)
    return next(c for c in report if c["platform"] == platform)


def test_every_platform_has_a_connector_in_registry_order():
    assert list(CONNECTORS) == list(PLATFORMS)
    assert [c["platform"] for c in connector_report(PLATFORMS, {}, {}, NOW)] == list(PLATFORMS)


def test_x_with_keys_is_connected():
    c = card("x", X_KEYS, connected=("x",))
    assert c["state"] == "connected" and all(i["done"] for i in c["checklist"])


def test_x_without_keys_lists_what_is_missing():
    c = card("x", {"X_API_KEY": "k1"})
    assert c["state"] == "needs_keys"
    assert c["next_step"] == ("Add the API key secret, Access token and Access token secret to the env file, "
                              "then restart the app.")
    assert c["sign_in_url"] is None  # X signs in with keys, not in the browser
    assert [i["done"] for i in c["checklist"]] == [True, False, False, False]


def test_linkedin_walks_through_keys_then_sign_in():
    only_id = card("linkedin", {"LINKEDIN_CLIENT_ID": "id"})
    assert only_id["state"] == "needs_keys"
    assert only_id["next_step"] == ("Add the Client secret to the env file, then restart the app. "
                                    "After that: Press “Sign in with LinkedIn” and allow access.")

    keys = card("linkedin", {"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"})
    assert keys["state"] == "needs_sign_in" and "Sign in with LinkedIn" in keys["next_step"]
    assert keys["checklist"][-1] == {"label": "Signed in", "key": "LINKEDIN_ACCESS_TOKEN", "done": False}


def test_linkedin_signed_in_shows_expiry_and_expired_asks_again():
    later = (NOW + timedelta(days=50)).isoformat()
    signed_in = card("linkedin", {"LINKEDIN_ACCESS_TOKEN": "t", "LINKEDIN_TOKEN_EXPIRES_AT": later},
                     connected=("linkedin",))
    assert signed_in["state"] == "connected" and signed_in["expires_at"] == later

    past = (NOW - timedelta(days=1)).isoformat()
    keys = {"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"}
    expired = card("linkedin", {**keys, "LINKEDIN_ACCESS_TOKEN": "t", "LINKEDIN_TOKEN_EXPIRES_AT": past},
                   connected=("linkedin",))
    assert expired["state"] == "needs_sign_in" and expired["next_step"].startswith("The sign-in expired on")
    assert expired["checklist"][-1]["done"] is False

    # A token made in LinkedIn's portal, without client keys: say it expired, then what to add.
    no_keys = card("linkedin", {"LINKEDIN_ACCESS_TOKEN": "t", "LINKEDIN_TOKEN_EXPIRES_AT": past},
                   connected=("linkedin",))
    assert no_keys["state"] == "needs_keys" and no_keys["next_step"].startswith("The sign-in expired on")


def test_sign_in_button_appears_once_the_app_keys_are_in():
    uris = {"linkedin": "http://localhost:8080/api/connect/linkedin/callback"}
    def li(secrets):
        report = connector_report(PLATFORMS, secrets, {}, NOW, redirect_uris=uris)
        return next(c for c in report if c["platform"] == "linkedin")
    assert li({"LINKEDIN_CLIENT_ID": "id"})["sign_in_url"] is None
    ready = li({"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"})
    assert ready["sign_in_url"] == "/api/connect/linkedin"
    assert ready["redirect_uri"] == uris["linkedin"]


def test_platforms_without_an_adapter_are_planned():
    for pid in ("facebook", "threads", "mastodon"):
        c = card(pid, {})
        assert c["state"] == "planned" and not c["built"] and c["next_step"].startswith("Not built yet")


def test_api_reports_setup_but_never_key_values(tmp_path, app):
    secrets = {**X_KEYS, "LINKEDIN_CLIENT_ID": "li-id-value", "LINKEDIN_CLIENT_SECRET": "li-secret-value"}
    settings = replace(app.state.settings, db_path=tmp_path / "c.db", secrets=secrets)
    with TestClient(create_app(settings, publishers={})) as c:
        r = c.get("/api/connectors")
    assert r.status_code == 200
    assert {x["platform"]: x["state"] for x in r.json()}["linkedin"] == "needs_sign_in"
    for value in secrets.values():
        assert value not in r.text
