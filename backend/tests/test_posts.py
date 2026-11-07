from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.scheduler import publish_due


def future(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def make_post(client, **overrides):
    payload = {"body": "Hello", "scheduled_at": future(), "platforms": ["x"]} | overrides
    return client.post("/api/posts", json=payload)


def force_due(app, post_id: int) -> None:
    """Move a post's time into the past so the scheduler picks it up."""
    with app.state.posts.db.connect() as conn:
        conn.execute("UPDATE posts SET scheduled_at='2000-01-01T00:00:00+00:00' WHERE id=?", (post_id,))


def test_create_and_list(client):
    r = make_post(client, platforms=["x", "linkedin"])
    assert r.status_code == 201
    post = r.json()
    assert [t["platform"] for t in post["targets"]] == ["x", "linkedin"]
    assert all(t["status"] == "scheduled" for t in post["targets"])
    assert len(client.get("/api/posts").json()) == 1


def test_list_filters_by_range(client):
    make_post(client, scheduled_at=future(1))
    make_post(client, scheduled_at=future(24 * 40))
    end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert len(client.get("/api/posts", params={"end": end}).json()) == 1


def test_rejects_past_time(client):
    r = make_post(client, scheduled_at="2020-01-01T09:00:00+00:00")
    assert r.status_code == 422
    assert "past" in r.json()["detail"]


def test_rejects_time_without_timezone(client):
    assert make_post(client, scheduled_at="2030-01-01T09:00:00").status_code == 422


def test_rejects_text_over_x_limit(client):
    r = make_post(client, body="a" * 281)
    assert r.status_code == 422
    assert "280" in r.json()["detail"]


def test_x_counts_links_as_23_chars(client):
    body = "a" * 250 + " https://example.com/" + "b" * 100  # 251 + 23 = 274 weighted
    assert make_post(client, body=body).status_code == 201


def test_unknown_platform(client):
    assert make_post(client, platforms=["myspace"]).status_code == 422


def test_scheduler_publishes_due_posts(app, client, fake_x):
    post = make_post(client, body="Due now").json()
    force_due(app, post["id"])
    assert publish_due(app.state.posts, app.state.publishers) == 1
    target = client.get("/api/posts").json()[0]["targets"][0]
    assert target["status"] == "published"
    assert target["external_url"] == "https://x.test/1"
    assert fake_x.sent == ["Due now"]
    # Running again doesn't post twice.
    assert publish_due(app.state.posts, app.state.publishers) == 0


def test_unconnected_platform_fails_clearly(app, client):
    post = make_post(client, platforms=["linkedin"]).json()
    force_due(app, post["id"])
    publish_due(app.state.posts, app.state.publishers)
    target = client.get("/api/posts").json()[0]["targets"][0]
    assert target["status"] == "failed"
    assert "not connected" in target["error"]


def test_failure_then_retry(app, client, fake_x):
    post = make_post(client).json()
    force_due(app, post["id"])
    fake_x.fail_with = "HTTP 403: blocked"
    publish_due(app.state.posts, app.state.publishers)
    assert client.get("/api/posts").json()[0]["targets"][0]["error"] == "HTTP 403: blocked"

    fake_x.fail_with = None
    assert client.post(f"/api/posts/{post['id']}/retry").status_code == 200
    publish_due(app.state.posts, app.state.publishers)
    assert client.get("/api/posts").json()[0]["targets"][0]["status"] == "published"


def test_cannot_edit_published_post(app, client):
    post = make_post(client).json()
    force_due(app, post["id"])
    publish_due(app.state.posts, app.state.publishers)
    r = client.put(f"/api/posts/{post['id']}", json={"body": "edit", "scheduled_at": future(), "platforms": ["x"]})
    assert r.status_code == 409


def test_update_and_delete(client):
    post = make_post(client).json()
    r = client.put(f"/api/posts/{post['id']}",
                   json={"body": "Changed", "scheduled_at": future(5), "platforms": ["bluesky"]})
    assert r.status_code == 200
    assert r.json()["body"] == "Changed"
    assert [t["platform"] for t in r.json()["targets"]] == ["bluesky"]
    assert client.delete(f"/api/posts/{post['id']}").status_code == 204
    assert client.get("/api/posts").json() == []
    assert client.delete(f"/api/posts/{post['id']}").status_code == 404


def test_interrupted_publish_is_marked_failed(app, client):
    post = make_post(client).json()
    with app.state.posts.db.connect() as conn:
        conn.execute("UPDATE post_targets SET status='publishing' WHERE post_id=?", (post["id"],))
    assert app.state.posts.recover_interrupted() == 1
    assert client.get("/api/posts").json()[0]["targets"][0]["status"] == "failed"


def test_send_test_post(client, fake_x):
    r = client.post("/api/platforms/x/test", json={"text": "Hello X"})
    assert r.status_code == 200
    assert fake_x.sent == ["Hello X"]


def test_send_test_post_shows_platform_error(client, fake_x):
    fake_x.fail_with = "HTTP 403: app not in a Project"
    r = client.post("/api/platforms/x/test", json={"text": "Hello X"})
    assert r.status_code == 422
    assert "Project" in r.json()["detail"]


def test_platform_list(client):
    platforms = {p["id"]: p for p in client.get("/api/platforms").json()}
    assert platforms["x"]["has_adapter"] is True
    assert platforms["linkedin"]["has_adapter"] is False
    assert len({p["color"] for p in platforms.values()}) == len(platforms)  # colours are distinct
