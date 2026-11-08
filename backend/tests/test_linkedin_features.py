"""LinkedIn adapter: images, link cards, options, first comment, edit, delete, stats. LinkedIn is mocked."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from backend.app.platforms import ImageFile, LinkCard, PostExtras, PublishError
from backend.app.platforms import linkedin
from backend.app.platforms.linkedin import LinkedInPublisher

from .test_linkedin import FakeResponse

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
SECRETS = {"LINKEDIN_ACCESS_TOKEN": "tok", "LINKEDIN_TOKEN_EXPIRES_AT": "2027-01-06T09:00:00+00:00"}
PNG = ImageFile(b"\x89PNG\r\n\x1a\nrest", "image/png", "A chart")


class Router:
    """Fake requests module: answers by (method, URL fragment), records every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.routes: list[tuple[str, str, FakeResponse]] = [
            ("GET", "/v2/userinfo", FakeResponse(200, {"sub": "abc123", "name": "Test Person"})),
            ("POST", "/rest/images?action=initializeUpload",
             FakeResponse(200, {"value": {"uploadUrl": "https://upload.test/1", "image": "urn:li:image:IMG1"}})),
            ("PUT", "https://upload.test/", FakeResponse(201, None)),
            ("POST", "/rest/posts", FakeResponse(201, None, {"x-restli-id": "urn:li:share:7000000000000000001"})),
            ("POST", "/comments", FakeResponse(201, {})),
            ("DELETE", "/rest/posts/", FakeResponse(204, None)),
        ]

    def answer(self, method: str, fragment: str, response: FakeResponse) -> None:
        self.routes.insert(0, (method, fragment, response))

    def _call(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, fragment, response in self.routes:
            if m == method and fragment in url:
                return response
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw): return self._call("GET", url, **kw)
    def post(self, url, **kw): return self._call("POST", url, **kw)
    def put(self, url, **kw): return self._call("PUT", url, **kw)
    def delete(self, url, **kw): return self._call("DELETE", url, **kw)

    def sent(self, method: str, fragment: str) -> list[dict]:
        return [kw for m, url, kw in self.calls if m == method and fragment in url]


@pytest.fixture
def li(monkeypatch) -> Router:
    router = Router()
    for name in ("get", "post", "put", "delete"):
        monkeypatch.setattr(linkedin.requests, name, getattr(router, name))
    return router


def pub(**extra) -> LinkedInPublisher:
    return LinkedInPublisher({**SECRETS, **extra}, now=lambda: NOW)


def post_body(li: Router) -> dict:
    return [kw for m, url, kw in li.calls if m == "POST" and url.endswith("/rest/posts")][-1]["json"]


def test_image_post_uploads_the_image_then_attaches_it(li):
    pub().publish("Look at this", PostExtras(image=PNG))
    init = li.sent("POST", "initializeUpload")[0]["json"]
    assert init == {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    upload = li.sent("PUT", "upload.test")[0]
    assert upload["data"] == PNG.data and upload["headers"]["Content-Type"] == "image/png"
    assert post_body(li)["content"] == {"media": {"id": "urn:li:image:IMG1", "altText": "A chart"}}


def test_link_card_becomes_an_article_with_thumbnail(li):
    card = LinkCard("https://blog.test/a.html", "My post", "Summary", thumbnail=PNG)
    pub().publish("New post", PostExtras(link_card=card))
    assert post_body(li)["content"] == {"article": {
        "source": "https://blog.test/a.html", "title": "My post", "description": "Summary",
        "thumbnail": "urn:li:image:IMG1"}}


def test_a_thumbnail_that_fails_to_upload_still_posts_the_card(li):
    li.answer("POST", "initializeUpload", FakeResponse(403, {"message": "nope"}))
    result = pub().publish("New post", PostExtras(link_card=LinkCard("https://b.test", "T", thumbnail=PNG)))
    assert "thumbnail" not in post_body(li)["content"]["article"]
    assert result.warning.startswith("The preview picture couldn't be added")


def test_an_image_that_fails_to_upload_stops_the_post(li):
    li.answer("PUT", "https://upload.test/", FakeResponse(500, {"message": "broken"}))
    with pytest.raises(PublishError, match="Uploading the image to LinkedIn failed"):
        pub().publish("Look", PostExtras(image=PNG))
    assert not [c for c in li.calls if c[1].endswith("/rest/posts")]


def test_visibility_and_reposts(li):
    pub().publish("Friends only", PostExtras(visibility="CONNECTIONS", allow_reshare=False))
    body = post_body(li)
    assert body["visibility"] == "CONNECTIONS" and body["isReshareDisabledByAuthor"] is True


def test_first_comment_is_posted_as_you_on_the_new_post(li):
    pub().publish("Post", PostExtras(first_comment="Link: https://blog.test"))
    call = [(url, kw) for m, url, kw in li.calls if url.endswith("/comments")][0]
    assert "/rest/socialActions/urn%3Ali%3Ashare%3A7000000000000000001/comments" in call[0]
    assert call[1]["json"] == {"actor": "urn:li:person:abc123", "object": "urn:li:share:7000000000000000001",
                               "message": {"text": "Link: https://blog.test"}}


def test_a_failed_first_comment_is_only_a_warning(li):
    li.answer("POST", "/comments", FakeResponse(429, {"message": "Comment create throttled"}))
    result = pub().publish("Post", PostExtras(first_comment="hi"))
    assert result.external_id == "urn:li:share:7000000000000000001"
    assert result.warning == "The first comment wasn't added: HTTP 429: Comment create throttled"


def test_delete_and_edit_target_the_post_by_its_encoded_id(li):
    li.answer("POST", "/rest/posts/urn", FakeResponse(204, None))
    p = pub()
    p.delete("urn:li:share:123")
    p.edit("urn:li:share:123", "Fixed (typo)")
    (m1, url1, kw1), (m2, url2, kw2) = li.calls[-2:]
    assert (m1, url1) == ("DELETE", "https://api.linkedin.com/rest/posts/urn%3Ali%3Ashare%3A123")
    assert kw1["headers"]["X-RestLi-Method"] == "DELETE"
    assert url2 == "https://api.linkedin.com/rest/posts/urn%3Ali%3Ashare%3A123"
    assert kw2["headers"]["X-RestLi-Method"] == "PARTIAL_UPDATE"
    assert kw2["json"] == {"patch": {"$set": {"commentary": r"Fixed \(typo\)"}}}


def test_deleting_a_post_that_is_gone_explains_why(li):
    li.answer("DELETE", "/rest/posts/", FakeResponse(404, {"message": "Not found"}))
    with pytest.raises(PublishError, match="may have been deleted on LinkedIn"):
        pub().delete("urn:li:share:123")


def test_stats_are_off_without_the_analytics_permission(li):
    p = pub(LINKEDIN_GRANTED_SCOPES="openid profile w_member_social")
    assert not p.stats_enabled()
    with pytest.raises(PublishError, match="Community Management"):
        p.metrics("urn:li:share:123")


def test_stats_ask_for_each_metric_and_add_them_up(li):
    li.answer("GET", "memberCreatorPostAnalytics", FakeResponse(200, {"elements": [{"count": 7}, {"count": 3}]}))
    p = pub(LINKEDIN_GRANTED_SCOPES="openid,profile,w_member_social,r_member_postAnalytics")
    stats = p.metrics("urn:li:share:123")
    assert stats["impressions"] == 10 and set(stats) == set(linkedin.STAT_QUERIES)
    first = [url for m, url, _ in li.calls if "memberCreatorPostAnalytics" in url][0]
    assert "entity=(share:urn%3Ali%3Ashare%3A123)" in first and "queryType=IMPRESSION" in first


def test_network_errors_become_readable(monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(linkedin.requests, "delete", down)
    with pytest.raises(PublishError, match="Couldn't reach LinkedIn"):
        pub().delete("urn:li:share:1")


def test_sign_in_records_the_granted_scopes(li):
    li.answer("POST", "/oauth/v2/accessToken",
              FakeResponse(200, {"access_token": "t", "expires_in": 60, "scope": "openid,profile,w_member_social"}))
    saved = linkedin.finish_sign_in({"LINKEDIN_CLIENT_ID": "c", "LINKEDIN_CLIENT_SECRET": "s"}, "code", "http://cb",
                                    now=lambda: NOW)
    assert saved["LINKEDIN_GRANTED_SCOPES"] == "openid profile w_member_social"


def test_extra_scopes_can_be_requested_from_env():
    assert linkedin.requested_scopes({}) == linkedin.SCOPES
    url = linkedin.sign_in_url({"LINKEDIN_CLIENT_ID": "c", "LINKEDIN_CLIENT_SECRET": "s",
                                "LINKEDIN_SCOPES": "openid profile w_member_social r_member_postAnalytics"},
                               "http://cb", "st")
    assert "scope=openid%20profile%20w_member_social%20r_member_postAnalytics" in url
