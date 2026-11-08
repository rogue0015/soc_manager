"""Account profiles for the Social tab: each adapter's profile(), and the cache in front of them. All mocked."""

from __future__ import annotations

from backend.app.platforms import PublishError
from backend.app.platforms import linkedin, x
from backend.app.platforms.bluesky import BlueskyPublisher
from backend.app.platforms.devto import DevtoPublisher
from backend.app.platforms.linkedin import LinkedInPublisher
from backend.app.platforms.x import XPublisher
from backend.app.services.profiles import KEEP_PROBLEM, KEEP_PROFILE, ProfileService

from .conftest import FakePublisher
from .test_bluesky import KEYS as BSKY_KEYS
from .test_bluesky import FakeBluesky
from .test_devto import KEY as DEVTO_KEY
from .test_devto import FakeDevto
from .test_linkedin import TOKEN, FakeResponse

X_KEYS = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_TOKEN_SECRET": "ts"}


# ---- each platform ------------------------------------------------------------------------------

def test_x_profile_has_counts_and_a_bigger_picture(monkeypatch):
    user = {"name": "Tester", "screen_name": "tester", "followers_count": 10, "friends_count": 2,
            "statuses_count": 99, "description": "Hi", "profile_image_url_https": "https://pbs.test/a_normal.jpg"}
    monkeypatch.setattr(x.requests, "get", lambda url, **kw: FakeResponse(200, user))
    p = XPublisher(X_KEYS).profile()
    assert (p.name, p.handle, p.url, p.avatar) == ("Tester", "@tester", "https://x.com/tester",
                                                   "https://pbs.test/a_200x200.jpg")
    assert (p.followers, p.following, p.posts, p.bio) == (10, 2, 99, "Hi")
    monkeypatch.setattr(x.requests, "get", lambda url, **kw: FakeResponse(401, {"title": "Unauthorized"}))
    try:
        XPublisher(X_KEYS).profile()
    except PublishError as exc:
        assert str(exc).startswith("Credentials rejected")


def test_linkedin_profile_is_name_and_picture(monkeypatch):
    me = {"sub": "abc", "name": "Test Person", "picture": "https://media.test/p.jpg"}
    monkeypatch.setattr(linkedin.requests, "get", lambda url, **kw: FakeResponse(200, me))
    p = LinkedInPublisher(TOKEN).profile()
    assert (p.name, p.avatar, p.url, p.followers) == ("Test Person", "https://media.test/p.jpg",
                                                      "https://www.linkedin.com/in/me/", None)


def test_devto_profile_counts_published_articles():
    http = FakeDevto(mine=[{"id": 1, "published": True}, {"id": 2, "published": False}])
    http.override[("get", "/users/me")] = FakeResponse(200, {"username": "tester", "name": "Tester",
                                                             "profile_image": "https://dev.test/t.png",
                                                             "summary": "Writes"})
    p = DevtoPublisher(DEVTO_KEY, http=http).profile()
    assert (p.name, p.handle, p.url, p.avatar, p.posts, p.bio) == (
        "Tester", "@tester", "https://dev.to/tester", "https://dev.test/t.png", 1, "Writes")


def test_bluesky_profile_reads_the_public_profile_without_signing_in():
    http = FakeBluesky()
    http.override["app.bsky.actor.getProfile"] = FakeResponse(200, {
        "handle": "tester.bsky.social", "displayName": "", "avatar": "https://cdn.test/a.jpg",
        "followersCount": 5, "followsCount": 6, "postsCount": 7, "description": "Hello"})
    p = BlueskyPublisher(BSKY_KEYS, http=http).profile()
    assert (p.name, p.handle, p.url) == ("tester.bsky.social", "@tester.bsky.social",
                                         "https://bsky.app/profile/tester.bsky.social")
    assert (p.followers, p.following, p.posts, p.bio) == (5, 6, 7, "Hello")
    assert http.sent("createSession") == [] and http.calls[0][2]["params"] == {"actor": "tester.bsky.social"}


# ---- the service and the API ----------------------------------------------------------------------

def test_api_lists_connected_accounts_and_remembers_them(client, fake_x):
    first = client.get("/api/profiles").json()
    assert first == [{"platform": "x", "error": None, "profile": {
        "name": "Tester", "handle": "@tester", "url": "https://x.test/tester", "avatar": "https://x.test/tester.png",
        "followers": 12, "following": 3, "posts": 40, "bio": ""}}]
    client.get("/api/profiles")
    assert fake_x.profile_calls == 1
    client.get("/api/profiles", params={"refresh": "true"})
    assert fake_x.profile_calls == 2


def test_a_problem_is_shown_and_asked_again_sooner():
    clock = [1000.0]
    fake = FakePublisher()
    fake.fail_with = "X said no."
    service = ProfileService({"x": fake}, clock=lambda: clock[0])
    assert service.all() == [{"platform": "x", "profile": None, "error": "X said no."}]
    clock[0] += KEEP_PROBLEM + 1
    fake.fail_with = None
    assert service.all()[0]["profile"]["name"] == "Tester"
    clock[0] += KEEP_PROFILE - 1
    service.all()
    assert fake.profile_calls == 2   # asked twice (failed, then fetched); the third came from memory
