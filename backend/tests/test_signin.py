"""Signing in to LinkedIn from the app. LinkedIn is mocked; nothing leaves the machine."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend.app.config import load_env_file
from backend.app.main import create_app
from backend.app.platforms import linkedin
from backend.app.services.errors import InvalidInput, NotFound
from backend.app.services.signin import STATE_TTL

from .test_linkedin import FakeLinkedIn, FakeResponse

KEYS = {"LINKEDIN_CLIENT_ID": "cid", "LINKEDIN_CLIENT_SECRET": "client-secret-value"}
TOKEN = {"access_token": "new-token-value", "expires_in": 5184000}


@pytest.fixture
def signin_app(tmp_path, app):
    env = tmp_path / "env"
    env.write_text("X_API_KEY=keep\nLINKEDIN_CLIENT_ID=cid\nLINKEDIN_CLIENT_SECRET=client-secret-value\n")
    settings = replace(app.state.settings, db_path=tmp_path / "s.db", env_file=env,
                       public_url="http://localhost:8080", secrets=dict(KEYS))
    return create_app(settings, publishers={})


@pytest.fixture
def linkedin_api(monkeypatch) -> FakeLinkedIn:
    fake = FakeLinkedIn(post=FakeResponse(200, TOKEN))
    monkeypatch.setattr(linkedin.requests, "get", fake.get)
    monkeypatch.setattr(linkedin.requests, "post", fake.post)
    return fake


def state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_sign_in_sends_you_to_linkedin_with_the_registered_redirect(signin_app):
    url = signin_app.state.signin.start("linkedin")
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert query["redirect_uri"] == ["http://localhost:8080/api/connect/linkedin/callback"]
    assert query["client_id"] == ["cid"]


def test_finishing_saves_the_token_and_connects_without_a_restart(signin_app, linkedin_api):
    svc = signin_app.state.signin
    message = svc.finish("linkedin", "code1", state_from(svc.start("linkedin")))

    assert message == "LinkedIn connected as Test Person."
    saved = load_env_file(svc.settings.env_file)
    assert saved["LINKEDIN_ACCESS_TOKEN"] == "new-token-value" and saved["X_API_KEY"] == "keep"
    assert "LINKEDIN_TOKEN_EXPIRES_AT" in saved
    assert "linkedin" in signin_app.state.publishers          # posting works straight away
    assert signin_app.state.activity.recent(1)[0]["message"] == "Signed in to LinkedIn as Test Person."
    exchange = linkedin_api.calls[0][2]["data"]
    assert exchange["redirect_uri"] == "http://localhost:8080/api/connect/linkedin/callback"


def test_a_state_works_once_and_only_for_a_while(signin_app, linkedin_api):
    svc = signin_app.state.signin
    state = state_from(svc.start("linkedin"))
    svc.finish("linkedin", "code1", state)
    with pytest.raises(InvalidInput, match="out of date or wasn't started here"):
        svc.finish("linkedin", "code1", state)

    late = state_from(svc.start("linkedin"))
    issued = svc._pending[late][1]
    svc.now = lambda: issued + STATE_TTL + timedelta(seconds=1)
    with pytest.raises(InvalidInput, match="out of date"):
        svc.finish("linkedin", "code2", late)


def test_forged_or_cancelled_sign_ins_save_nothing(signin_app, linkedin_api):
    svc = signin_app.state.signin
    with pytest.raises(InvalidInput):
        svc.finish("linkedin", "code", "made-up-state")
    with pytest.raises(InvalidInput, match="LinkedIn didn't sign you in: The user cancelled"):
        svc.finish("linkedin", "", state_from(svc.start("linkedin")), "user_cancelled_authorize", "The user cancelled")
    assert "LINKEDIN_ACCESS_TOKEN" not in load_env_file(svc.settings.env_file)
    assert linkedin_api.calls == []


def test_rejected_code_is_explained(signin_app, linkedin_api):
    linkedin_api.post_response = FakeResponse(400, {"error": "invalid_request", "error_description": "code expired"})
    svc = signin_app.state.signin
    with pytest.raises(InvalidInput, match="LinkedIn did not issue a token. HTTP 400: code expired"):
        svc.finish("linkedin", "old", state_from(svc.start("linkedin")))


def test_sign_in_needs_the_app_keys_and_a_browser_platform(signin_app):
    svc = signin_app.state.signin
    svc.settings.secrets.pop("LINKEDIN_CLIENT_SECRET")
    with pytest.raises(InvalidInput, match="Add the LinkedIn Client ID and Client secret"):
        svc.start("linkedin")
    with pytest.raises(NotFound, match="X \\(Twitter\\) doesn't use a browser sign-in"):
        svc.start("x")


def test_routes_redirect_and_never_leak_secrets(signin_app, linkedin_api):
    with TestClient(signin_app, follow_redirects=False) as c:
        go = c.get("/api/connect/linkedin")
        assert go.status_code == 303 and go.headers["location"].startswith("https://www.linkedin.com/")

        back = c.get("/api/connect/linkedin/callback", params={"code": "c", "state": state_from(go.headers["location"])})
        assert back.status_code == 303
        location = back.headers["location"]
        assert location.startswith("/?signin_ok=") and location.endswith("#status")

        bad = c.get("/api/connect/linkedin/callback", params={"code": "c", "state": "nope"})
        assert bad.headers["location"].startswith("/?signin_error=")

        for r in (go, back, bad):
            assert "client-secret-value" not in r.headers["location"]
            assert "new-token-value" not in r.headers["location"]
        linkedin_row = next(p for p in c.get("/api/platforms").json() if p["id"] == "linkedin")
        assert linkedin_row["has_adapter"]  # connected without a restart
