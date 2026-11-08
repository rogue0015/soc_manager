"""Post now: a planned post goes out in the request, once, through the scheduler's own path. Fakes only."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.scheduler import publish_due

from .test_post_management import c, fake_li, li_app, schedule, target  # noqa: F401 (fixtures)
from .test_posts import future, make_post


def test_post_now_sends_at_once_and_moves_the_time_to_now(app, client, fake_x):
    post = make_post(client, body="Right now", scheduled_at=future(48)).json()
    r = client.post(f"/api/posts/{post['id']}/publish")
    assert r.status_code == 200
    sent = r.json()
    assert fake_x.sent == ["Right now"] and sent["targets"][0]["status"] == "published"
    assert datetime.fromisoformat(sent["scheduled_at"]) <= datetime.now(timezone.utc)
    # The scheduler finds nothing left to send, so it never goes out twice.
    assert publish_due(app.state.posts, app.state.publishers, app.state.attachments) == 0
    assert fake_x.sent == ["Right now"]


def test_each_platform_succeeds_or_fails_on_its_own(client, fake_x):
    post = make_post(client, body="Both", platforms=["x", "linkedin"]).json()
    targets = {t["platform"]: t for t in client.post(f"/api/posts/{post['id']}/publish").json()["targets"]}
    assert targets["x"]["status"] == "published"
    assert (targets["linkedin"]["status"], targets["linkedin"]["error"]) == ("failed", "LinkedIn is not connected yet.")


def test_nothing_left_to_send_is_a_clear_conflict(client):
    post = make_post(client).json()
    client.post(f"/api/posts/{post['id']}/publish")
    r = client.post(f"/api/posts/{post['id']}/publish")
    assert r.status_code == 409 and r.json()["detail"].startswith("Nothing is waiting to go out")
    assert client.post("/api/posts/999/publish").status_code == 404


def test_a_post_for_linkedins_own_scheduler_is_not_posted_by_the_app(c, fake_li):
    post = schedule(c, options={"linkedin": {"handoff": True}}).json()
    r = c.post(f"/api/posts/{post['id']}/publish")
    assert r.status_code == 422 and r.json()["detail"] == (
        "LinkedIn's own scheduler is chosen for this post. Pick “This app” in the LinkedIn options to post it now.")
    assert fake_li.sent == [] and target(c.get(f"/api/posts/{post['id']}").json())["status"] == "scheduled"
