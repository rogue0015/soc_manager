"""Activity log, system monitor and analytics. Platforms are fakes; nothing is sent."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.app.services import activity as activity_module
from backend.app.services.monitor import MonitorService
from backend.app.services.scheduler import SchedulerState, publish_due, run_scheduler
from backend.app.timeutil import now_iso, now_utc

from .test_blog import _seed
from .test_posts import force_due, future, make_post


def publish_now(app, *post_ids: int) -> None:
    for pid in post_ids:
        force_due(app, pid)
    publish_due(app.state.posts, app.state.publishers)


def kinds(client) -> list[str]:
    return [a["kind"] for a in client.get("/api/activity").json()]


# ---- activity log -------------------------------------------------------------

def test_activity_follows_a_post_through_its_life(app, client, fake_x):
    pid = make_post(client, body="Launch day!\nMore below", platforms=["x"]).json()["id"]
    client.put(f"/api/posts/{pid}", json={"body": "Launch day!", "scheduled_at": future(), "platforms": ["x"]})
    fake_x.fail_with = "HTTP 503: try later"
    publish_now(app, pid)
    client.post(f"/api/posts/{pid}/retry")
    fake_x.fail_with = None
    publish_now(app, pid)

    events = client.get("/api/activity").json()
    assert [e["kind"] for e in events] == ["published", "retried", "failed", "edited", "scheduled", "started"]
    published, _, failed, *_ = events
    assert published["message"] == "Published “Launch day!” on X (Twitter)."
    assert published["url"] == "https://x.test/1" and published["platform"] == "x"
    assert failed["message"] == "Couldn't publish “Launch day!” on X (Twitter): HTTP 503: try later"


def test_deleting_a_post_is_logged_even_though_the_post_is_gone(client):
    pid = make_post(client, body="Short-lived").json()["id"]
    client.delete(f"/api/posts/{pid}")
    latest = client.get("/api/activity", params={"limit": 1}).json()[0]
    assert latest["kind"] == "deleted" and latest["message"] == "Removed “Short-lived” from the planner."


def test_blog_plan_logs_one_summary_instead_of_one_line_per_post(app, client):
    _seed(app)
    client.post("/api/blog/plan", json={
        "urls": ["https://blog.test/blogs/one.html", "https://blog.test/blogs/two.html"],
        "start": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "every_days": 2, "platforms": ["x", "linkedin"],
    })
    assert kinds(client)[:2] == ["blog_planned", "started"]
    assert client.get("/api/activity").json()[0]["message"] == (
        "Planned 2 blog posts for X (Twitter) and LinkedIn, one every 2 days."
    )


def test_long_post_text_is_shortened_in_messages():
    assert activity_module.snippet("x" * 100) == "“" + "x" * 59 + "…”"


def test_activity_keeps_only_the_newest_entries(app, monkeypatch):
    monkeypatch.setattr(activity_module, "KEEP", 3)
    for i in range(5):
        app.state.activity.record("test_post", f"event {i}")
    assert [e["message"] for e in app.state.activity.recent(10)] == ["event 4", "event 3", "event 2"]


def test_activity_limit_is_validated(client):
    assert client.get("/api/activity", params={"limit": 0}).status_code == 422


# ---- system monitor -------------------------------------------------------------

def test_system_reports_queue_and_flags_problems(app, client, fake_x):
    upcoming = make_post(client, scheduled_at=future(5)).json()
    failing = make_post(client).json()["id"]
    fake_x.fail_with = "nope"
    publish_now(app, failing)

    s = client.get("/api/system").json()
    assert s["queue"]["scheduled"] == 1 and s["queue"]["failed"] == 1
    assert s["queue"]["next_due_at"] == upcoming["scheduled_at"]
    assert s["platforms"] == {"known": 7, "connected": 1}
    assert s["scheduler"]["enabled"] is False
    assert s["database"]["size_bytes"] > 0
    assert any("scheduler is off" in n for n in s["attention"])
    assert any(n.startswith("1 post failed to publish.") for n in s["attention"])


def test_system_is_quiet_when_all_is_well(app, fake_x):
    state = SchedulerState(enabled=True, interval_seconds=30, last_run_at=now_iso())
    monitor = MonitorService(app.state.posts.db, app.state.settings, state, {"x": fake_x}, now_utc())
    assert monitor.snapshot()["attention"] == []


def test_system_notices_a_stalled_scheduler(app, fake_x):
    stalled = (now_utc() - timedelta(minutes=10)).isoformat()
    state = SchedulerState(enabled=True, interval_seconds=30, last_run_at=stalled)
    monitor = MonitorService(app.state.posts.db, app.state.settings, state, {"x": fake_x}, now_utc())
    assert any("hasn't checked" in n for n in monitor.snapshot()["attention"])


def test_scheduler_records_each_check(app):
    state = SchedulerState(enabled=True, interval_seconds=1)

    async def one_check():
        stop = asyncio.Event()
        task = asyncio.create_task(run_scheduler(app.state.posts, {}, 1, stop, state))
        await asyncio.sleep(0.2)
        stop.set()
        await task

    asyncio.run(one_check())
    assert state.last_run_at is not None and state.last_error is None


# ---- analytics -------------------------------------------------------------------

def test_analytics_counts_deliveries_per_platform(app, client):
    both = make_post(client, body="Both", platforms=["x", "linkedin"]).json()["id"]
    make_post(client, body="Later", platforms=["x"], scheduled_at=future(48))
    publish_now(app, both)  # X works; LinkedIn has no adapter in tests, so it fails

    r = client.get("/api/analytics").json()
    assert r["totals"] == {"scheduled": 1, "publishing": 0, "published": 1, "failed": 1,
                           "success_rate": 0.5, "posts": 2, "stats": {"posts": 0}}
    assert [(p["platform"], p["published"], p["failed"]) for p in r["by_platform"]] == [("x", 1, 0), ("linkedin", 0, 1)]
    assert r["failure_reasons"] == [{"error": "LinkedIn is not connected yet.", "count": 1}]
    assert [p["body"] for p in r["posts"]] == ["Later", "Both"]  # newest first


def test_analytics_filters(app, client):
    both = make_post(client, body="Both", platforms=["x", "linkedin"]).json()["id"]
    make_post(client, body="Later", platforms=["x"], scheduled_at=future(48))
    publish_now(app, both)

    only_x = client.get("/api/analytics", params={"platform": "x"}).json()
    assert {t["platform"] for p in only_x["posts"] for t in p["targets"]} == {"x"}

    failed = client.get("/api/analytics", params={"status": "failed"}).json()
    assert failed["totals"]["failed"] == 1 and failed["totals"]["posts"] == 1

    assert client.get("/api/analytics", params={"source": "blog"}).json()["totals"]["posts"] == 0

    next_day = client.get("/api/analytics", params={"start": future(24)}).json()
    assert [p["body"] for p in next_day["posts"]] == ["Later"]

    assert client.get("/api/analytics").json()["totals"]["success_rate"] == 0.5
    assert client.get("/api/analytics", params={"source": "custom", "end": future(1)}).json()["totals"]["posts"] == 1


def test_analytics_with_nothing_finished_has_no_success_rate(client):
    make_post(client)
    assert client.get("/api/analytics").json()["totals"]["success_rate"] is None


def test_analytics_rejects_bad_filters(client):
    r = client.get("/api/analytics", params={"platform": "myspace"})
    assert r.status_code == 422 and r.json()["detail"] == "Unknown platform: myspace"
    r = client.get("/api/analytics", params={"start": future(5), "end": future(1)})
    assert r.json()["detail"] == "The start of the range must be before its end."
    assert client.get("/api/analytics", params={"status": "lost"}).status_code == 422
