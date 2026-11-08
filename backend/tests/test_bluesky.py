"""Bluesky adapter: sign-in, posts with links and hashtags, cards, delete, stats. Bluesky is mocked."""

from __future__ import annotations

import pytest
import requests

from backend.app.platforms import PLATFORMS, ImageFile, LinkCard, PostExtras, PublishError, build_publisher
from backend.app.platforms.bluesky import MAX_BLOB, PUBLIC, SERVICE, BlueskyPublisher, facets, full_handle
from backend.app.platforms.connectors import connector_status

from .test_linkedin import NOW, FakeResponse

KEYS = {"BLUESKY_HANDLE": "tester", "BLUESKY_APP_PASSWORD": "aaaa-bbbb-cccc-dddd"}
DID = "did:plc:test123"
SESSION = {"accessJwt": "jwt", "did": DID, "handle": "tester.bsky.social"}
URI = f"at://{DID}/app.bsky.feed.post/3kabc"


class FakeBluesky:
    """Stands in for the requests module: answers like Bluesky and records every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.override: dict[str, object] = {}   # xrpc name -> FakeResponse or exception

    def _answer(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        name = url.rsplit("/xrpc/", 1)[1]
        if name in self.override:
            answer = self.override[name]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return FakeResponse(200, {
            "com.atproto.server.createSession": SESSION,
            "com.atproto.repo.createRecord": {"uri": URI, "cid": "bafy"},
            "com.atproto.repo.uploadBlob": {"blob": {"$type": "blob", "ref": {"$link": "bafythumb"}}},
            "com.atproto.repo.deleteRecord": {},
            "app.bsky.feed.getPosts": {"posts": [{"uri": URI, "likeCount": 7, "replyCount": 2,
                                                  "repostCount": 3, "quoteCount": 1}]},
        }[name])

    def get(self, url, **kwargs):
        return self._answer("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._answer("post", url, **kwargs)

    def sent(self, name: str) -> list[dict]:
        return [kw for _m, url, kw in self.calls if url.endswith(name)]


def test_short_handles_get_the_bluesky_domain():
    assert full_handle("tester") == "tester.bsky.social"
    assert full_handle("@Tester.bsky.social") == "tester.bsky.social"
    assert full_handle("brandon.dev") == "brandon.dev"


def test_links_and_hashtags_become_clickable_by_byte_position():
    text = "Hi 👋 read https://rogue0015.github.io/blogs/. #community #ai, ok"
    found = facets(text)
    link, tag1, tag2 = found
    encoded = text.encode()
    assert encoded[link["index"]["byteStart"]:link["index"]["byteEnd"]] == b"https://rogue0015.github.io/blogs/"
    assert link["features"] == [{"$type": "app.bsky.richtext.facet#link", "uri": "https://rogue0015.github.io/blogs/"}]
    assert encoded[tag1["index"]["byteStart"]:tag1["index"]["byteEnd"]] == b"#community"
    assert (tag1["features"][0]["tag"], tag2["features"][0]["tag"]) == ("community", "ai")


def test_publish_signs_in_and_posts_as_the_account():
    http = FakeBluesky()
    result = BlueskyPublisher(KEYS, http=http).publish("Hello #ai")
    assert http.sent("createSession")[0]["json"] == {"identifier": "tester.bsky.social",
                                                     "password": "aaaa-bbbb-cccc-dddd"}
    body = http.sent("createRecord")[0]
    assert body["headers"]["Authorization"] == "Bearer jwt"
    record = body["json"]["record"]
    assert body["json"]["repo"] == DID and record["text"] == "Hello #ai" and record["facets"][0]["features"][0]["tag"] == "ai"
    assert record["createdAt"].endswith("Z") and "embed" not in record
    assert (result.external_id, result.external_url) == (URI, "https://bsky.app/profile/tester.bsky.social/post/3kabc")
    assert http.calls[0][1] == f"{SERVICE}/xrpc/com.atproto.server.createSession"


def test_a_link_card_uploads_its_picture():
    http = FakeBluesky()
    card = LinkCard("https://blog.test/a.html", "A post", "Summary", ImageFile(b"\x89PNG" + b"0" * 50, "image/png"))
    result = BlueskyPublisher(KEYS, http=http).publish("New post https://blog.test/a.html", PostExtras(link_card=card))
    assert http.sent("uploadBlob")[0]["headers"]["Content-Type"] == "image/png"
    embed = http.sent("createRecord")[0]["json"]["record"]["embed"]
    assert embed["$type"] == "app.bsky.embed.external"
    assert embed["external"]["title"] == "A post" and embed["external"]["thumb"]["ref"]["$link"] == "bafythumb"
    assert result.warning is None


def test_a_picture_over_1_mb_is_left_off_the_card():
    http = FakeBluesky()
    big = ImageFile(b"0" * (MAX_BLOB + 1), "image/jpeg")
    result = BlueskyPublisher(KEYS, http=http).publish("x", PostExtras(link_card=LinkCard("https://a.test", "A", "", big)))
    assert http.sent("uploadBlob") == [] and "thumb" not in http.sent("createRecord")[0]["json"]["record"]["embed"]["external"]
    assert result.warning.startswith("Posted the preview card without its picture")


@pytest.mark.parametrize("name, answer, message", [
    ("com.atproto.server.createSession", FakeResponse(401, {"error": "AuthenticationRequired"}),
     "Bluesky didn't accept the handle and app password"),
    ("com.atproto.repo.createRecord", FakeResponse(429, {"error": "RateLimitExceeded"}),
     "Bluesky is limiting how fast the app can post"),
    ("com.atproto.repo.createRecord", FakeResponse(400, {"error": "InvalidRequest", "message": "Record/text too long"}),
     "Bluesky answered HTTP 400: Record/text too long"),
    ("com.atproto.server.createSession", requests.ConnectionError("down"), "Couldn't reach Bluesky"),
])
def test_errors_read_as_plain_sentences(name, answer, message):
    http = FakeBluesky()
    http.override[name] = answer
    with pytest.raises(PublishError, match=message):
        BlueskyPublisher(KEYS, http=http).publish("Hello")


def test_delete_removes_the_record():
    http = FakeBluesky()
    BlueskyPublisher(KEYS, http=http).delete(URI)
    assert http.sent("deleteRecord")[0]["json"] == {"repo": DID, "collection": "app.bsky.feed.post", "rkey": "3kabc"}


def test_check_names_the_account_or_says_what_is_wrong():
    status = BlueskyPublisher(KEYS, http=FakeBluesky()).check()
    assert (status.connected, status.can_post, status.account) == (True, True, "@tester.bsky.social")
    http = FakeBluesky()
    http.override["com.atproto.server.createSession"] = FakeResponse(401, {"error": "AuthenticationRequired"})
    assert not BlueskyPublisher(KEYS, http=http).check().connected


def test_stats_come_from_the_public_api_without_signing_in():
    http = FakeBluesky()
    pub = BlueskyPublisher(KEYS, http=http)
    assert pub.stats_enabled() and pub.metrics(URI) == {"reactions": 7, "comments": 2, "reshares": 4}
    assert http.calls[0][1] == f"{PUBLIC}/xrpc/app.bsky.feed.getPosts" and http.calls[0][2]["params"] == {"uris": URI}
    http.override["app.bsky.feed.getPosts"] = FakeResponse(200, {"posts": []})
    with pytest.raises(PublishError, match="isn't on Bluesky any more"):
        pub.metrics(URI)


def test_the_adapter_needs_handle_and_app_password():
    assert build_publisher("bluesky", {"BLUESKY_HANDLE": "tester"}) is None
    assert isinstance(build_publisher("bluesky", KEYS), BlueskyPublisher)
    card = connector_status(PLATFORMS["bluesky"], {}, False, NOW)
    assert card["state"] == "needs_keys" and [i["key"] for i in card["checklist"]] == ["BLUESKY_HANDLE",
                                                                                         "BLUESKY_APP_PASSWORD"]
    assert connector_status(PLATFORMS["bluesky"], KEYS, True, NOW)["state"] == "connected"
