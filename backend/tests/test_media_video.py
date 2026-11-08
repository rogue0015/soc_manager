"""Media library, video reading, and video/picture posting on YouTube, TikTok, Instagram, LinkedIn and
Bluesky. Every platform is faked; nothing leaves the machine."""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import mediainfo
from backend.app.main import create_app
from backend.app.platforms import ImageFile, PostExtras, PublishError, VideoFile, base
from backend.app.platforms import instagram, linkedin, tiktok, youtube
from backend.app.platforms.bluesky import BlueskyPublisher
from backend.app.services.scheduler import publish_due

from .conftest import FakePublisher
from .test_linkedin import FakeResponse
from .test_posts import force_due, future

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 1200, 627) + b"0" * 50


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(width: int = 1920, height: int = 1080, seconds: int = 15, turned: bool = False, pad: int = 2000) -> bytes:
    """A tiny but well-formed MP4: ftyp, mdat filler, then moov at the end (as phones write it)."""
    mvhd = bytes(4) + bytes(8) + struct.pack(">II", 1000, seconds * 1000) + bytes(80)
    matrix = struct.pack(">9i", 0, 65536, 0, -65536, 0, 0, 0, 0, 1 << 30) if turned else \
        struct.pack(">9i", 65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30)
    tkhd = bytes(4) + bytes(36) + matrix + struct.pack(">II", width << 16, height << 16)
    audio_tkhd = bytes(4) + bytes(36) + struct.pack(">9i", 65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30) + bytes(8)
    moov = box(b"moov", box(b"mvhd", mvhd) + box(b"trak", box(b"tkhd", audio_tkhd)) + box(b"trak", box(b"tkhd", tkhd)))
    return box(b"ftyp", b"isom" + bytes(4) + b"isommp41") + box(b"mdat", bytes(pad)) + moov


# ---- reading files ------------------------------------------------------------------

def test_picture_sizes_from_headers():
    assert mediainfo.image_size(PNG) == (1200, 627)
    assert mediainfo.image_size(b"GIF89a" + struct.pack("<HH", 300, 200)) == (300, 200)
    jpeg = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"xx" + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 1350, 1080)
    assert mediainfo.image_size(jpeg + bytes(20)) == (1080, 1350)
    assert mediainfo.image_size(b"nonsense") is None


def test_video_size_length_and_phone_rotation(tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(mp4(1920, 1080, seconds=42, turned=True))
    info = mediainfo.video_info(path)
    assert (info.width, info.height, info.duration_ms) == (1080, 1920, 42000)
    (tmp_path / "bad.mp4").write_bytes(b"\x00\x00\x00\x08ftyp" + b"\xff" * 30)
    assert mediainfo.video_info(tmp_path / "bad.mp4").width is None   # garbage in, nothing out


# ---- the library over HTTP -----------------------------------------------------------

@pytest.fixture
def fake_yt() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def app2(tmp_path, app, fake_x, fake_yt):
    settings = replace(app.state.settings, db_path=tmp_path / "m" / "test.db")
    return create_app(settings, publishers={"x": fake_x, "youtube": fake_yt, "linkedin": FakePublisher()})


@pytest.fixture
def c(app2):
    with TestClient(app2) as client:
        yield client


def upload_video(c, **kw) -> dict:
    r = c.post("/api/media", params={"name": "clip.mp4"}, content=mp4(**kw))
    assert r.status_code == 201, r.text
    return r.json()


def test_upload_lists_and_describes_a_video(c):
    v = upload_video(c, width=1080, height=1920, seconds=30)
    assert (v["kind"], v["mime"], v["width"], v["height"], v["duration_ms"], v["name"]) == \
        ("video", "video/mp4", 1080, 1920, 30000, "clip.mp4")
    assert c.post("/api/media", content=PNG).json()["kind"] == "image"
    assert [m["kind"] for m in c.get("/api/media", params={"kind": "video"}).json()] == ["video"]
    assert len(c.get("/api/media").json()) == 2
    assert c.get(v["url"]).content[4:8] == b"ftyp"


def test_video_platforms_need_a_video(c):
    r = c.post("/api/posts", json={"body": "Hi", "scheduled_at": future(), "platforms": ["youtube"]})
    assert r.status_code == 422 and r.json()["detail"] == "YouTube only posts videos. Attach a video, or untick YouTube."
    r = c.post("/api/posts", json={"body": "Hi", "scheduled_at": future(), "platforms": ["x"],
                                   "options": {"x": {"attachment": "video", "video_id": "a" * 32}}})
    assert r.json()["detail"] == "X (Twitter) can't post a video from the app."


def test_schedule_and_publish_a_video(app2, c, fake_yt):
    v = upload_video(c, width=1080, height=1920, seconds=20)
    opts = {"attachment": "video", "video_id": v["id"], "title": "My Short", "privacy": "unlisted", "tags": "ai, dev"}
    post = c.post("/api/posts", json={"body": "Watch this", "scheduled_at": future(), "platforms": ["youtube"],
                                      "options": {"youtube": opts}}).json()
    assert c.post("/api/posts", json={"body": "x", "scheduled_at": future(), "platforms": ["youtube"],
                                      "options": {"youtube": {**opts, "title": "t" * 101}}}).json()["detail"] == \
        "YouTube titles can be up to 100 characters; this one is 101."
    force_due(app2, post["id"])
    publish_due(app2.state.posts, app2.state.publishers, app2.state.attachments)
    extras = fake_yt.extras[0]
    assert extras.video.vertical and extras.video.duration_ms == 20000 and Path(extras.video.path).exists()
    assert (extras.title, extras.privacy, extras.tags) == ("My Short", "unlisted", ("ai", "dev"))


def test_library_refuses_to_delete_what_a_scheduled_post_needs(c):
    v = upload_video(c)
    c.post("/api/posts", json={"body": "x", "scheduled_at": future(), "platforms": ["youtube"],
                               "options": {"youtube": {"attachment": "video", "video_id": v["id"]}}})
    assert c.get("/api/media").json()[0]["used_by"][0]["platform"] == "youtube"
    assert c.delete(f"/api/media/{v['id']}").status_code == 409
    spare = upload_video(c)
    assert c.delete(f"/api/media/{spare['id']}").status_code == 204
    assert c.get(spare["url"]).status_code == 404


def test_only_pictures_and_videos_are_kept(c, app2):
    assert c.post("/api/media", content=b"#!/bin/sh\nrm -rf /").status_code == 422
    leftovers = [p for p in app2.state.settings.media_dir.iterdir() if p.name.startswith(".upload-")]
    assert leftovers == []


# ---- fake HTTP for the adapters --------------------------------------------------------

class Router:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def _call(self, method, url, **kw):
        if hasattr(kw.get("data"), "read"):
            kw["data"] = kw["data"].read()
        self.calls.append((method, url, kw))
        for m, frag, resp in self.routes:
            if m == method and frag in url:
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw): return self._call("GET", url, **kw)
    def post(self, url, **kw): return self._call("POST", url, **kw)
    def put(self, url, **kw): return self._call("PUT", url, **kw)

    def sent(self, method, frag):
        return [kw for m, u, kw in self.calls if m == method and frag in u]


def video_file(tmp_path, size=3000, **kw) -> VideoFile:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"v" * size)
    return VideoFile(str(path), "video/mp4", size, **kw)


# ---- YouTube ------------------------------------------------------------------------------

YT = {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "sec", "YOUTUBE_REFRESH_TOKEN": "ref"}


def yt_router(privacy="private"):
    return Router([
        ("POST", "oauth2.googleapis.com/token", FakeResponse(200, {"access_token": "acc", "expires_in": 3600})),
        ("POST", "uploadType=resumable", FakeResponse(200, {}, {"Location": "https://upload.test/session1"})),
        ("PUT", "https://upload.test/session1", FakeResponse(200, {"id": "VID1", "status": {"privacyStatus": privacy}})),
        ("GET", "/channels", FakeResponse(200, {"items": [{"id": "UC1", "snippet": {"title": "Brandon", "customUrl": "@b"},
                                                          "statistics": {"subscriberCount": "12", "videoCount": "3"}}]})),
        ("GET", "/videos?part=statistics", FakeResponse(200, {"items": [{"statistics": {"viewCount": "99", "likeCount": "5"}}]})),
    ])


def test_youtube_uploads_a_short_and_says_it_is_private(tmp_path):
    http = yt_router()
    pub = youtube.YouTubePublisher(YT, http=http)
    result = pub.publish("Line one\nmore", PostExtras(video=video_file(tmp_path, width=1080, height=1920, duration_ms=30000),
                                                       privacy="public", tags=("ai",)))
    meta = http.sent("POST", "uploadType=resumable")[0]
    assert meta["json"]["snippet"]["title"] == "Line one" and meta["json"]["snippet"]["tags"] == ["ai"]
    assert meta["headers"]["X-Upload-Content-Length"] == "3000"
    assert http.sent("PUT", "session1")[0]["data"] == b"v" * 3000
    assert result.external_url == "https://www.youtube.com/shorts/VID1"
    assert result.warning == youtube.PRIVATE_NOTE
    assert pub.metrics("VID1") == {"views": 99, "reactions": 5}
    assert pub.profile().followers == 12 and pub.check().account == "Brandon"


def test_youtube_landscape_is_a_regular_video_and_needs_a_video(tmp_path):
    pub = youtube.YouTubePublisher(YT, http=yt_router(privacy="public"))
    result = pub.publish("Hi", PostExtras(video=video_file(tmp_path, width=1920, height=1080, duration_ms=30000)))
    assert result.external_url == "https://youtu.be/VID1" and result.warning is None
    with pytest.raises(PublishError, match="only posts videos"):
        pub.publish("Hi", PostExtras())


def test_youtube_expired_sign_in_and_quota_are_explained(tmp_path):
    http = Router([("POST", "oauth2.googleapis.com/token", FakeResponse(400, {"error": "invalid_grant"}))])
    with pytest.raises(PublishError, match="Sign in with YouTube again"):
        youtube.YouTubePublisher(YT, http=http).publish("x", PostExtras(video=video_file(tmp_path)))
    http = yt_router()
    http.routes.insert(0, ("POST", "uploadType=resumable", FakeResponse(403, {"error": {
        "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})))
    with pytest.raises(PublishError, match="daily upload quota"):
        youtube.YouTubePublisher(YT, http=http).publish("x", PostExtras(video=video_file(tmp_path)))


def test_youtube_sign_in_asks_for_a_refresh_token():
    url = youtube.sign_in_url(YT, "http://localhost:8080/api/connect/youtube/callback", "st")
    assert "access_type=offline" in url and "prompt=consent" in url and "youtube.upload" in url


# ---- TikTok -------------------------------------------------------------------------------

TT = {"TIKTOK_CLIENT_KEY": "ck", "TIKTOK_CLIENT_SECRET": "cs", "TIKTOK_REFRESH_TOKEN": "old"}


def tt_router(options=("SELF_ONLY",), status="PUBLISH_COMPLETE"):
    return Router([
        ("POST", "/oauth/token/", FakeResponse(200, {"access_token": "a", "expires_in": 7200, "refresh_token": "new",
                                                     "refresh_expires_in": 86400, "open_id": "o1"})),
        ("POST", "/creator_info/query/", FakeResponse(200, {"data": {"privacy_level_options": list(options),
                                                                     "creator_username": "rogue",
                                                                     "max_video_post_duration_sec": 600},
                                                            "error": {"code": "ok"}})),
        ("POST", "/video/init/", FakeResponse(200, {"data": {"publish_id": "p1", "upload_url": "https://up.test/1"},
                                                    "error": {"code": "ok"}})),
        ("PUT", "https://up.test/1", FakeResponse(201, {})),
        ("POST", "/status/fetch/", FakeResponse(200, {"data": {"status": status, "publicaly_available_post_id": [777]},
                                                      "error": {"code": "ok"}})),
    ])


def test_tiktok_posts_privately_when_unaudited_and_keeps_the_new_refresh_token(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(base, "_token_saver", saved.append)
    secrets = dict(TT)
    http = tt_router()
    result = tiktok.TikTokPublisher(secrets, http=http, sleep=lambda s: None).publish(
        "Caption", PostExtras(video=video_file(tmp_path), privacy="public"))
    init = http.sent("POST", "/video/init/")[0]["json"]
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 3000, "chunk_size": 3000, "total_chunk_count": 1}
    assert http.sent("PUT", "up.test")[0]["headers"]["Content-Range"] == "bytes 0-2999/3000"
    assert result.external_url == "https://www.tiktok.com/@rogue/video/777"
    assert "private" in result.warning
    assert secrets["TIKTOK_REFRESH_TOKEN"] == "new" and saved[0]["TIKTOK_REFRESH_TOKEN"] == "new"


def test_tiktok_failure_and_chunk_maths(tmp_path):
    http = tt_router(options=("PUBLIC_TO_EVERYONE", "SELF_ONLY"), status="FAILED")
    with pytest.raises(PublishError, match="couldn't publish"):
        tiktok.TikTokPublisher(dict(TT), http=http, sleep=lambda s: None).publish(
            "x", PostExtras(video=video_file(tmp_path)))
    assert http.sent("POST", "/video/init/")[0]["json"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    parts = tiktok.chunks(55 * 1024 * 1024)
    assert len(parts) == 5 and parts[-1][1] == 55 * 1024 * 1024 and parts[0] == (0, tiktok.CHUNK)


# ---- Instagram ------------------------------------------------------------------------------

def test_instagram_posts_a_reel_through_the_resumable_upload(tmp_path):
    http = Router([
        ("POST", "/123/media_publish", FakeResponse(200, {"id": "M1"})),
        ("POST", "/123/media", FakeResponse(200, {"id": "C1"})),
        ("POST", "rupload.facebook.com", FakeResponse(200, {"success": True})),
        ("GET", "/C1", FakeResponse(200, {"status_code": "FINISHED"})),
        ("GET", "/M1", FakeResponse(200, {"permalink": "https://www.instagram.com/reel/abc/"})),
    ])
    pub = instagram.InstagramPublisher({"INSTAGRAM_ACCESS_TOKEN": "t", "INSTAGRAM_USER_ID": "123"},
                                       http=http, sleep=lambda s: None)
    result = pub.publish("Caption", PostExtras(video=video_file(tmp_path)))
    assert http.sent("POST", "/123/media")[0]["data"]["media_type"] == "REELS"
    upload = http.sent("POST", "rupload")[0]
    assert upload["headers"]["file_size"] == "3000" and upload["data"] == b"v" * 3000
    assert result.external_url == "https://www.instagram.com/reel/abc/"
    with pytest.raises(PublishError, match="Reels"):
        pub.publish("No video")


# ---- LinkedIn video ------------------------------------------------------------------------

def test_linkedin_uploads_a_video_in_parts_then_posts_it(tmp_path, monkeypatch):
    http = Router([
        ("GET", "/v2/userinfo", FakeResponse(200, {"sub": "abc", "name": "B"})),
        ("POST", "videos?action=initializeUpload", FakeResponse(200, {"value": {
            "video": "urn:li:video:V1", "uploadToken": "",
            "uploadInstructions": [{"uploadUrl": "https://up.li/1", "firstByte": 0, "lastByte": 1999},
                                   {"uploadUrl": "https://up.li/2", "firstByte": 2000, "lastByte": 4999}]}})),
        ("PUT", "https://up.li/1", FakeResponse(200, None, {"etag": "e1"})),
        ("PUT", "https://up.li/2", FakeResponse(200, None, {"etag": "e2"})),
        ("POST", "videos?action=finalizeUpload", FakeResponse(200, {})),
        ("GET", "/rest/videos/", FakeResponse(200, {"status": "AVAILABLE"})),
        ("POST", "/rest/posts", FakeResponse(201, None, {"x-restli-id": "urn:li:share:1"})),
    ])
    for name in ("get", "post", "put"):
        monkeypatch.setattr(linkedin.requests, name, getattr(http, name))
    pub = linkedin.LinkedInPublisher({"LINKEDIN_ACCESS_TOKEN": "t"})
    pub.publish("Watch", PostExtras(video=video_file(tmp_path, size=5000, duration_ms=10000), title="Demo"))
    assert [len(kw["data"]) for kw in http.sent("PUT", "up.li")] == [2000, 3000]
    assert http.sent("POST", "finalizeUpload")[0]["json"]["finalizeUploadRequest"]["uploadedPartIds"] == ["e1", "e2"]
    assert http.sent("POST", "/rest/posts")[-1]["json"]["content"] == {"media": {"id": "urn:li:video:V1", "title": "Demo"}}
    with pytest.raises(PublishError, match="3 seconds to 30 minutes"):
        pub.publish("x", PostExtras(video=video_file(tmp_path, duration_ms=1000)))


# ---- Bluesky pictures and video ---------------------------------------------------------------

BSKY = {"BLUESKY_HANDLE": "me.bsky.social", "BLUESKY_APP_PASSWORD": "pw"}
SESSION = {"accessJwt": "jwt", "did": "did:plc:me", "handle": "me.bsky.social",
           "didDoc": {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example.net"}]}}


def bsky_router():
    jobs = iter([{"jobStatus": {"jobId": "j1", "state": "JOB_STATE_ENCODING"}},
                 {"jobStatus": {"jobId": "j1", "state": "JOB_STATE_COMPLETED", "blob": {"ref": "video-blob"}}}])
    return Router([
        ("POST", "createSession", FakeResponse(200, SESSION)),
        ("POST", "uploadBlob", FakeResponse(200, {"blob": {"ref": "pic"}})),
        ("POST", "createRecord", FakeResponse(200, {"uri": "at://did:plc:me/app.bsky.feed.post/r1"})),
        ("GET", "getServiceAuth", FakeResponse(200, {"token": "svc"})),
        ("POST", "app.bsky.video.uploadVideo", FakeResponse(200, {"jobId": "j1", "state": "JOB_STATE_CREATED"})),
        ("GET", "getJobStatus", lambda: FakeResponse(200, next(jobs))),
    ])


def record(http):
    return http.sent("POST", "createRecord")[0]["json"]["record"]


def test_bluesky_posts_a_picture_with_its_shape():
    http = bsky_router()
    BlueskyPublisher(BSKY, http=http).publish("Pic", PostExtras(image=ImageFile(PNG, "image/png", "A chart")))
    assert record(http)["embed"] == {"$type": "app.bsky.embed.images", "images": [
        {"image": {"ref": "pic"}, "alt": "A chart", "aspectRatio": {"width": 1200, "height": 627}}]}
    with pytest.raises(PublishError, match="up to 1 MB"):
        BlueskyPublisher(BSKY, http=bsky_router()).publish(
            "Big", PostExtras(image=ImageFile(PNG + bytes(1_000_001), "image/png")))


def test_bluesky_posts_a_video_after_the_video_service_processes_it(tmp_path):
    http = bsky_router()
    pub = BlueskyPublisher(BSKY, http=http)
    pub._sleep = lambda s: None
    pub.publish("Clip", PostExtras(video=video_file(tmp_path, width=1080, height=1920, duration_ms=9000)))
    assert http.sent("GET", "getServiceAuth")[0]["params"]["aud"] == "did:web:pds.example.net"
    upload = http.sent("POST", "uploadVideo")[0]
    assert upload["params"]["did"] == "did:plc:me" and upload["headers"]["Authorization"] == "Bearer svc"
    assert record(http)["embed"] == {"$type": "app.bsky.embed.video", "video": {"ref": "video-blob"},
                                     "aspectRatio": {"width": 1080, "height": 1920}}
