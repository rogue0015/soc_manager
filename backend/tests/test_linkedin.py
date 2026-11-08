"""LinkedIn adapter, sign-in helpers and env-file writing. LinkedIn is mocked; nothing is sent."""

from __future__ import annotations

import importlib.util
import socket
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from backend.app.config import PROJECT_ROOT, Settings, load_env_file, set_env_values
from backend.app.platforms import PublishError, build_publishers
from backend.app.platforms import linkedin
from backend.app.platforms.linkedin import (
    LinkedInPublisher, SignInError, authorization_url, exchange_code, to_little_text,
)

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
TOKEN = {"LINKEDIN_ACCESS_TOKEN": "tok", "LINKEDIN_TOKEN_EXPIRES_AT": "2027-01-06T09:00:00+00:00"}
ME = {"sub": "abc123", "name": "Test Person"}


class FakeResponse:
    def __init__(self, status: int, body: object = None, headers: dict | None = None):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = str(body)
        self.content = b"" if body is None else self.text.encode()
        self.headers = CaseInsensitiveDict(headers or {})

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class FakeLinkedIn:
    """Stands in for requests.get/post and records every call."""

    def __init__(self, userinfo: FakeResponse | None = None, post: FakeResponse | None = None):
        self.userinfo = userinfo or FakeResponse(200, ME)
        self.post_response = post or FakeResponse(201, None, {"x-restli-id": "urn:li:share:777"})
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.userinfo

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.post_response


@pytest.fixture
def fake(monkeypatch) -> FakeLinkedIn:
    fake = FakeLinkedIn()
    monkeypatch.setattr(linkedin.requests, "get", fake.get)
    monkeypatch.setattr(linkedin.requests, "post", fake.post)
    return fake


def publisher(secrets: dict | None = None, now: datetime = NOW) -> LinkedInPublisher:
    return LinkedInPublisher(TOKEN if secrets is None else secrets, now=lambda: now)


# ---- text escaping ----------------------------------------------------------

def test_reserved_characters_are_escaped_but_hashtags_kept():
    assert to_little_text("Tips (part 1): use *stars* & snake_case #python") == (
        r"Tips \(part 1\): use \*stars\* & snake\_case #python"
    )
    assert to_little_text("a # b @ c") == r"a \# b \@ c"


def test_links_are_fully_escaped():
    assert to_little_text("Read https://x.test/a_b#top now") == r"Read https://x.test/a\_b\#top now"


# ---- posting ----------------------------------------------------------------

def test_publish_posts_as_the_signed_in_member(fake):
    result = publisher().publish("Hello (world)")

    method, url, kwargs = fake.calls[-1]
    assert (method, url) == ("POST", "https://api.linkedin.com/rest/posts")
    assert kwargs["json"]["author"] == "urn:li:person:abc123"
    assert kwargs["json"]["commentary"] == r"Hello \(world\)"
    assert kwargs["json"]["visibility"] == "PUBLIC"
    assert kwargs["headers"]["LinkedIn-Version"] == linkedin.API_VERSION
    assert result.external_id == "urn:li:share:777"
    assert result.external_url == "https://www.linkedin.com/feed/update/urn:li:share:777/"


def test_member_is_looked_up_once(fake):
    pub = publisher()
    pub.publish("one")
    pub.publish("two")
    assert [c[0] for c in fake.calls] == ["GET", "POST", "POST"]


def test_rejected_post_says_why(fake):
    fake.post_response = FakeResponse(422, {"message": "Content is a duplicate of urn:li:share:1"})
    with pytest.raises(PublishError, match="HTTP 422: Content is a duplicate"):
        publisher().publish("again")


def test_revoked_sign_in_tells_you_to_sign_in_again(fake):
    fake.post_response = FakeResponse(401, {"message": "Invalid access token"})
    with pytest.raises(PublishError, match="sign in again"):
        publisher().publish("hi")


def test_missing_openid_product_is_explained(fake):
    fake.userinfo = FakeResponse(403, {"message": "Not enough permissions"})
    with pytest.raises(PublishError, match="OpenID Connect"):
        publisher().publish("hi")
    assert all(c[0] == "GET" for c in fake.calls)  # nothing was posted


def test_network_error_becomes_publish_error(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(linkedin.requests, "get", boom)
    with pytest.raises(PublishError, match="Couldn't reach LinkedIn \\(ConnectionError\\)"):
        publisher().publish("hi")


def test_expired_token_fails_without_calling_linkedin(fake):
    with pytest.raises(PublishError, match="expired on 06 Jan 2027"):
        publisher(now=NOW + timedelta(days=61)).publish("hi")
    assert fake.calls == []


def test_success_without_post_id_still_counts_as_published(fake):
    fake.post_response = FakeResponse(201, None, {})
    result = publisher().publish("hi")
    assert result.external_url is None


# ---- status check -------------------------------------------------------------

def test_check_reports_account_and_days_left(fake):
    status = publisher().check()
    assert (status.connected, status.can_post, status.account) == (True, True, "Test Person")
    assert "valid for 60 days" in status.message


def test_check_warns_shortly_before_expiry(fake):
    status = publisher(now=NOW + timedelta(days=57)).check()
    assert status.can_post and "expires in 3 days" in status.message


def test_check_rejected_token(fake):
    fake.userinfo = FakeResponse(401, {"message": "Invalid access token"})
    status = publisher().check()
    assert (status.connected, status.can_post) == (False, False)
    assert "sign in again" in status.message


def test_check_works_without_a_known_expiry(fake):
    status = publisher({"LINKEDIN_ACCESS_TOKEN": "tok"}).check()
    assert status.message == "Connected. Posting is available."


# ---- wiring -------------------------------------------------------------------

def test_linkedin_connects_only_once_signed_in():
    assert "linkedin" not in build_publishers({"LINKEDIN_CLIENT_ID": "id", "LINKEDIN_CLIENT_SECRET": "s"})
    assert "linkedin" in build_publishers(TOKEN)


def test_linkedin_keys_are_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_ENV_FILE", str(tmp_path / "missing-env"))  # never read real secrets in tests
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "from-docker")
    monkeypatch.setenv("META_APP_ID", "meta-id")
    monkeypatch.setenv("THREADS_APP_SECRET", "threads-secret")
    monkeypatch.setenv("X_API_KEY", "")  # blank variables never hide a value from the file
    secrets = Settings.from_environment().secrets
    assert secrets["LINKEDIN_ACCESS_TOKEN"] == "from-docker"
    assert (secrets["META_APP_ID"], secrets["THREADS_APP_SECRET"]) == ("meta-id", "threads-secret")
    assert "X_API_KEY" not in secrets


# ---- sign-in ------------------------------------------------------------------

def test_authorization_url_asks_for_posting_rights():
    url = urlparse(authorization_url("cid", "http://localhost:8765/linkedin/callback", "st8"))
    query = parse_qs(url.query)
    assert url.netloc == "www.linkedin.com" and url.path == "/oauth/v2/authorization"
    assert query["client_id"] == ["cid"] and query["state"] == ["st8"]
    assert query["redirect_uri"] == ["http://localhost:8765/linkedin/callback"]
    assert query["scope"] == ["openid profile w_member_social"]
    assert "+" not in url.query


def test_exchange_code_returns_token_and_expiry(fake):
    fake.post_response = FakeResponse(200, {"access_token": "new", "expires_in": 5184000})
    token = exchange_code("cid", "secret", "code1", "http://localhost/cb", now=lambda: NOW)
    assert token.access_token == "new"
    assert token.expires_at == NOW + timedelta(days=60)
    assert fake.calls[0][2]["data"]["grant_type"] == "authorization_code"


def test_exchange_code_failure_is_readable(fake):
    fake.post_response = FakeResponse(400, {"error": "invalid_request", "error_description": "code expired"})
    with pytest.raises(SignInError, match="LinkedIn did not issue a token. HTTP 400: code expired"):
        exchange_code("cid", "secret", "old", "http://localhost/cb")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sign_in_script_catches_the_redirect():
    spec = importlib.util.spec_from_file_location("linkedin_login", PROJECT_ROOT / "scripts" / "linkedin_login.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    port = _free_port()
    result: dict = {}
    waiter = threading.Thread(target=lambda: result.update(
        script.wait_for_redirect(f"http://localhost:{port}/linkedin/callback", wait_seconds=10)))
    waiter.start()
    for _ in range(50):  # the server needs a moment to start listening
        try:
            assert requests.get(f"http://127.0.0.1:{port}/favicon.ico", timeout=2).status_code == 404
            page = requests.get(f"http://127.0.0.1:{port}/linkedin/callback?code=c1&state=s1", timeout=2)
            break
        except requests.ConnectionError:
            threading.Event().wait(0.05)
    waiter.join(timeout=10)
    assert "close this tab" in page.text
    assert result == {"code": "c1", "state": "s1"}


# ---- env file -----------------------------------------------------------------

def test_set_env_values_updates_in_place_and_keeps_other_lines(tmp_path):
    env = tmp_path / "env"
    env.write_text("# keys\nX_API_KEY=keep\nLINKEDIN_ACCESS_TOKEN=old\n\nLINKEDIN_ACCESS_TOKEN=older\n")
    set_env_values(env, {"LINKEDIN_ACCESS_TOKEN": "new", "LINKEDIN_TOKEN_EXPIRES_AT": "2027-01-06T09:00:00+00:00"})
    text = env.read_text()
    assert text.startswith("# keys\nX_API_KEY=keep\nLINKEDIN_ACCESS_TOKEN=new\n")
    assert load_env_file(env) == {
        "X_API_KEY": "keep",
        "LINKEDIN_ACCESS_TOKEN": "new",
        "LINKEDIN_TOKEN_EXPIRES_AT": "2027-01-06T09:00:00+00:00",
    }


def test_set_env_values_creates_a_private_file(tmp_path):
    env = tmp_path / "env"
    set_env_values(env, {"LINKEDIN_ACCESS_TOKEN": "t"})
    assert load_env_file(env) == {"LINKEDIN_ACCESS_TOKEN": "t"}
    assert env.stat().st_mode & 0o077 == 0
