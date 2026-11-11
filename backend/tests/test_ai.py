"""AI generation: the Gemini client, personas, drafts within each platform's limit, history. Gemini is faked."""

from __future__ import annotations

import json

import pytest
import requests

from backend.app.services.ai_writer import Usage, pacific_day
from backend.app.services.gemini import API, DEFAULT_MODEL, Gemini, GeminiError, GeminiRateLimited
from backend.app.services.personas import STARTERS, system_instructions

from .test_blog import _seed
from .test_devto import PAGE, Page
from .test_linkedin import FakeResponse

PERSONA = {"name": "Plain", "tone": "Calm and clear.", "never": "Hype."}


def gemini_answer(drafts: list[dict]) -> FakeResponse:
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": json.dumps({"drafts": drafts})}]},
                                              "finishReason": "STOP"}]})


class FakeHttp:
    """Stands in for requests: answers each POST with the next queued response."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeGemini:
    """Stands in for services.gemini.Gemini: records prompts, returns queued answers."""

    def __init__(self, *answers, ready=True, rpm=15, rpd=500):
        self.answers = list(answers)
        self.prompts: list[tuple[str, str]] = []
        self.ready = ready
        self.model = "gemini-test"
        self.rpm, self.rpd = rpm, rpd

    def generate_json(self, system, prompt, schema, on_request=lambda: None):
        on_request()   # counted against the limits, like the real client
        self.prompts.append((system, prompt))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"drafts": answer}


@pytest.fixture
def fake_gemini(app) -> FakeGemini:
    fake = FakeGemini()
    app.state.ai.gemini = lambda: fake
    return fake


def persona(client, **overrides) -> dict:
    return client.post("/api/ai/personas", json={**PERSONA, **overrides}).json()


# ---- the Gemini client ----------------------------------------------------------------------------

def test_client_sends_system_instructions_and_asks_for_json():
    http = FakeHttp(gemini_answer([{"platform": "x", "text": "Hi"}]))
    out = Gemini({"GEMINI_API_KEY": "k"}, http=http).generate_json("SYSTEM", "PROMPT", {"type": "OBJECT"})
    assert out == {"drafts": [{"platform": "x", "text": "Hi"}]}
    url, kw = http.calls[0]
    assert url == f"{API}/models/{DEFAULT_MODEL}:generateContent" and kw["headers"]["x-goog-api-key"] == "k"
    body = kw["json"]
    assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM" and body["contents"][0]["parts"][0]["text"] == "PROMPT"
    assert body["generationConfig"] == {"responseMimeType": "application/json", "responseSchema": {"type": "OBJECT"}}


def test_client_uses_the_model_from_env_and_skips_thoughts():
    answer = FakeResponse(200, {"candidates": [{"content": {"parts": [
        {"text": "thinking…", "thought": True}, {"text": '{"drafts": []}'}]}}]})
    http = FakeHttp(answer)
    assert Gemini({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-other"}, http=http).generate_json("s", "p", {}) == {"drafts": []}
    assert "/models/gemini-other:generateContent" in http.calls[0][0]


@pytest.mark.parametrize("answer, message", [
    (FakeResponse(400, {"error": {"message": "API key not valid. Please pass a valid API key."}}),
     "Gemini didn't accept the API key"),
    (FakeResponse(429, {"error": {"message": "Quota exceeded"}}), "Gemini's per-minute limit was reached"),
    (FakeResponse(404, {"error": {"message": "models/x is not found"}}), "Gemini doesn't know that model"),
    (FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}), r"Gemini refused this request \(SAFETY\)"),
    (FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}, "finishReason": "MAX_TOKENS"}]}),
     "Gemini ran out of room"),
    (requests.ConnectionError("down"), r"Couldn't reach Gemini \(ConnectionError\)"),
])
def test_client_errors_read_as_plain_sentences(answer, message):
    with pytest.raises(GeminiError, match=message):
        Gemini({"GEMINI_API_KEY": "k"}, http=FakeHttp(answer)).generate_json("s", "p", {})


def test_client_without_a_key_says_where_to_put_it():
    with pytest.raises(GeminiError, match="Add GEMINI_API_KEY to the env file"):
        Gemini({}).generate_json("s", "p", {})


# ---- personas -------------------------------------------------------------------------------------------

def test_persona_round_trip_and_rules(client):
    p = persona(client)
    assert (p["name"], p["tone"], p["voice"]) == ("Plain", "Calm and clear.", "")
    assert client.post("/api/ai/personas", json={**PERSONA, "name": "plain"}).status_code == 409   # names are unique
    r = client.post("/api/ai/personas", json={"name": "Empty"})
    assert r.status_code == 422 and r.json()["detail"].startswith("Fill in at least one field")
    updated = client.put(f"/api/ai/personas/{p['id']}", json={**PERSONA, "tone": "Bold."}).json()
    assert updated["tone"] == "Bold."
    assert client.delete(f"/api/ai/personas/{p['id']}").status_code == 204
    assert client.get("/api/ai/personas").json() == []


def test_starter_personas_are_added_once(client):
    names = [p["name"] for p in client.post("/api/ai/personas/starters").json()]
    assert sorted(names) == sorted(s["name"] for s in STARTERS)
    assert len(client.post("/api/ai/personas/starters").json()) == len(STARTERS)


def test_system_instructions_include_only_filled_in_fields():
    text = system_instructions({"name": "P", "tone": "Calm.", "voice": "", "never": "Hype."})
    assert text == "Persona: P\n\nTone:\nCalm.\n\nNever:\nHype."


# ---- generating ---------------------------------------------------------------------------------------

def test_generate_writes_one_draft_per_platform_and_remembers_it(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "x", "text": "Short one #ai"},
                                {"platform": "linkedin", "text": "A longer LinkedIn post."}])
    r = client.post("/api/ai/generate", json={
        "source": "idea", "idea": "We ran a hackathon", "platforms": ["x", "linkedin"], "persona_id": p["id"],
        "event": {"name": "DevFest Harare", "day": "8 Nov", "venue": "Harare", "takeaways": "Ship small"}})
    assert r.status_code == 201
    gen = r.json()
    assert gen["drafts"]["x"] == {"text": "Short one #ai", "length": 13, "limit": 280, "over": False,
                                  "post_id": None, "error": None, "unknown_links": []}
    assert gen["persona"]["name"] == "Plain" and gen["model"] == "gemini-test"
    system, prompt = fake_gemini.prompts[0]
    assert "Never invent numbers" in system and "Tone:\nCalm and clear." in system
    assert "- x (X (Twitter)): at most 280 characters (each link counts as 23)" in prompt
    assert "We ran a hackathon" in prompt and "- Event: DevFest Harare" in prompt and "- Notable takeaways: Ship small" in prompt
    assert client.get("/api/ai/generations").json()[0]["id"] == gen["id"]
    assert client.get("/api/activity").json()[0]["kind"] == "ai_generated"


def test_a_draft_over_the_limit_is_sent_back_once_then_flagged(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers += [[{"platform": "x", "text": "x" * 300}], [{"platform": "x", "text": "y" * 290}]]
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Idea", "platforms": ["x"],
                                                "persona_id": p["id"]}).json()
    assert "- x: the text was 300 characters; rewrite it to at most 252." in fake_gemini.prompts[1][1]
    assert gen["drafts"]["x"]["over"] is True and gen["drafts"]["x"]["length"] == 290


def test_a_missing_draft_says_to_regenerate(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "x", "text": "Only X"}])
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Idea", "platforms": ["x", "bluesky"],
                                                "persona_id": p["id"]}).json()
    assert gen["drafts"]["bluesky"]["error"].startswith("Gemini didn't write a draft for Bluesky")


def test_from_a_blog_post_the_article_and_link_go_in_the_prompt(app, client, fake_gemini):
    _seed(app)
    app.state.articles.fetch = lambda url, **kw: Page(PAGE)
    p = persona(client)
    fake_gemini.answers.append([{"platform": "bluesky", "text": "Read it https://blog.test/blogs/one.html"}])
    gen = client.post("/api/ai/generate", json={"source": "blog", "blog_url": "https://blog.test/blogs/one.html",
                                                "platforms": ["bluesky"], "persona_id": p["id"]}).json()
    prompt = fake_gemini.prompts[0][1]
    assert "Link: https://blog.test/blogs/one.html" in prompt and "## The context" in prompt
    assert gen["blog_url"] == "https://blog.test/blogs/one.html"


@pytest.mark.parametrize("body, message", [
    ({"source": "idea", "idea": "  ", "platforms": ["x"]}, "Write the idea for the post first."),
    ({"source": "blog", "platforms": ["x"]}, "Pick a blog post to write about."),
    ({"source": "idea", "idea": "Hi", "platforms": ["devto"]}, "dev.to publishes the whole blog post"),
    ({"source": "blog", "blog_url": "https://blog.test/blogs/nope.html", "platforms": ["x"]}, "That post isn't in the blog library"),
])
def test_bad_requests_are_explained(client, fake_gemini, body, message):
    p = persona(client)
    r = client.post("/api/ai/generate", json={**body, "persona_id": p["id"]})
    assert r.status_code == 422 and r.json()["detail"].startswith(message) and fake_gemini.prompts == []


def test_gemini_problems_reach_the_user(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append(GeminiError("Gemini's usage limit was reached."))
    r = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["x"], "persona_id": p["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "Gemini's usage limit was reached."
    assert client.get("/api/ai/generations").json() == []


def test_youtube_drafts_get_a_title_checked_against_its_limit(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "youtube", "text": "Description", "title": "A title"}])
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["youtube"],
                                                "persona_id": p["id"]}).json()
    yt = gen["drafts"]["youtube"]
    assert (yt["title"], yt["title_over"], yt["title_missing"]) == ("A title", False, False)
    assert "also write a title (in the title field) of at most 100 characters" in fake_gemini.prompts[0][1]


def test_a_missing_title_is_asked_for_once(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers += [[{"platform": "youtube", "text": "Description"}],
                            [{"platform": "youtube", "text": "Description", "title": "Study jam recap"}]]
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["youtube"],
                                                "persona_id": p["id"]}).json()
    assert "- youtube: write a title (in the title field) of at most 100 characters." in fake_gemini.prompts[1][1]
    assert gen["drafts"]["youtube"]["title"] == "Study jam recap"


# ---- regenerate, mark used, history -------------------------------------------------------------------

def test_regenerate_one_platform_mark_used_and_delete(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers += [[{"platform": "x", "text": "First"}, {"platform": "bluesky", "text": "Sky"}],
                            [{"platform": "x", "text": "Second"}]]
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["x", "bluesky"],
                                                "persona_id": p["id"]}).json()
    again = client.post(f"/api/ai/generations/{gen['id']}/regenerate", json={"platform": "x"}).json()
    assert again["drafts"]["x"]["text"] == "Second" and again["drafts"]["bluesky"]["text"] == "Sky"
    assert "Write a different version from this earlier one:\nFirst" in fake_gemini.prompts[1][1]
    used = client.put(f"/api/ai/generations/{gen['id']}/drafts/x/post", json={"post_id": 42}).json()
    assert used["drafts"]["x"]["post_id"] == 42
    assert client.post(f"/api/ai/generations/{gen['id']}/regenerate", json={"platform": "tiktok"}).status_code == 404
    trashed = client.put(f"/api/ai/generations/{gen['id']}/drafts/bluesky/trash", json={"trashed": True}).json()
    assert trashed["drafts"]["bluesky"]["trashed"] is True
    restored = client.put(f"/api/ai/generations/{gen['id']}/drafts/bluesky/trash", json={"trashed": False}).json()
    assert restored["drafts"]["bluesky"]["trashed"] is False
    assert client.put(f"/api/ai/generations/{gen['id']}/drafts/tiktok/trash", json={}).status_code == 404
    assert client.delete(f"/api/ai/generations/{gen['id']}").status_code == 204
    assert client.get(f"/api/ai/generations/{gen['id']}").status_code == 404


def test_history_keeps_its_persona_copy_after_the_persona_is_deleted(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "x", "text": "Hi"}])
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["x"],
                                                "persona_id": p["id"]}).json()
    client.delete(f"/api/ai/personas/{p['id']}")
    assert client.get(f"/api/ai/generations/{gen['id']}").json()["persona"]["name"] == "Plain"


def test_status_says_whether_a_key_is_set(app, client):
    app.state.ai.gemini = lambda: FakeGemini(ready=False)
    assert client.get("/api/ai/status").json() == {
        "ready": False, "model": "gemini-test", "per_minute": 15, "per_day": 500, "used_today": 0, "left_today": 500,
        "message": "Add GEMINI_API_KEY to the env file, then restart the app."}


# ---- rate limits ---------------------------------------------------------------------------------------

def quota_error(delay: str | None, daily: bool = False) -> FakeResponse:
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily
                                else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return FakeResponse(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded",
                                        "details": details}})


def test_default_model_is_flash_lite():
    assert DEFAULT_MODEL == "gemini-3.1-flash-lite" and Gemini({}).model == "gemini-3.1-flash-lite"


def test_a_short_per_minute_limit_is_waited_out_once():
    slept, counted = [], []
    http = FakeHttp(quota_error("4s"), gemini_answer([{"platform": "x", "text": "Hi"}]))
    out = Gemini({"GEMINI_API_KEY": "k"}, http=http, sleep=slept.append).generate_json(
        "s", "p", {}, on_request=lambda: counted.append(1))
    assert out["drafts"][0]["text"] == "Hi" and slept == [4.0] and len(counted) == 2 and len(http.calls) == 2


@pytest.mark.parametrize("answer, message, daily", [
    (quota_error("40s"), "Gemini's per-minute limit was reached. Try again in about 40 seconds.", False),
    (quota_error(None), "Gemini's per-minute limit was reached. Try again in a minute.", False),
    (quota_error("3s", daily=True), "Today's Gemini limit is used up. It resets at midnight Pacific time.", True),
])
def test_longer_limits_are_explained_not_waited(answer, message, daily):
    slept = []
    with pytest.raises(GeminiRateLimited) as caught:
        Gemini({"GEMINI_API_KEY": "k"}, http=FakeHttp(answer), sleep=slept.append).generate_json("s", "p", {})
    assert str(caught.value) == message and caught.value.daily is daily and slept == []


def test_limits_come_from_env_with_safe_defaults():
    g = Gemini({"GEMINI_RPM": "30", "GEMINI_RPD": "nonsense"})
    assert (g.rpm, g.rpd) == (30, 500)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_usage_waits_briefly_then_refuses_past_the_per_minute_budget(app):
    clock = Clock()
    usage = Usage(app.state.posts.db, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        usage.take(per_minute=3, per_day=100)
    clock.t += 55                       # the oldest frees up in 5 s: wait for it
    usage.take(per_minute=3, per_day=100)
    assert clock.t == 1060.0             # waited; the three old ones are now out of the window
    usage.take(per_minute=3, per_day=100)
    usage.take(per_minute=3, per_day=100)
    with pytest.raises(Exception, match="That's the 3 Gemini requests allowed per minute. Try again in about 60 seconds."):
        usage.take(per_minute=3, per_day=100)   # the next one frees up in 60 s: too long, refuse
    assert usage.used_today() == 6


def test_the_daily_budget_survives_a_restart_and_stops_generating(app, client, fake_gemini):
    usage = Usage(app.state.posts.db)
    usage.take(per_minute=15, per_day=2)
    assert Usage(app.state.posts.db).used_today() == 1   # a new instance (a restart) still knows
    p = persona(client)
    fake_gemini.rpd = 1
    r = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["x"], "persona_id": p["id"]})
    assert r.status_code == 422 and r.json()["detail"].startswith("Today's 1 Gemini requests are used up")
    assert fake_gemini.prompts == []


def test_generating_counts_every_request(app, client, fake_gemini):
    p = persona(client)
    fake_gemini.answers += [[{"platform": "x", "text": "x" * 300}], [{"platform": "x", "text": "Short"}]]
    client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["x"], "persona_id": p["id"]})
    status = client.get("/api/ai/status").json()
    assert (status["used_today"], status["left_today"]) == (2, 498)   # the draft, then the shortening


def test_pacific_day_rolls_over_at_midnight_pacific():
    from datetime import datetime, timezone
    assert pacific_day(datetime(2026, 11, 9, 7, 59, tzinfo=timezone.utc)) == "2026-11-08"   # 23:59 PST
    assert pacific_day(datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)) == "2026-11-09"


# ---- links Gemini wasn't given ------------------------------------------------------------------

def test_a_made_up_link_is_flagged_and_the_prompt_says_there_is_none(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "x", "text": "Great day! Read more https://blog.link."}])
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Great day", "platforms": ["x"],
                                                "persona_id": p["id"]}).json()
    assert gen["drafts"]["x"]["unknown_links"] == ["https://blog.link"]
    assert "There is no link to share: don't add any link or web address." in fake_gemini.prompts[0][1]


def test_links_you_gave_are_fine(app, client, fake_gemini):
    _seed(app)
    app.state.articles.fetch = lambda url, **kw: Page(PAGE)
    p = persona(client)
    fake_gemini.answers.append([{"platform": "x", "text": "New post https://blog.test/blogs/one.html and https://my.site/x"}])
    gen = client.post("/api/ai/generate", json={"source": "blog", "blog_url": "https://blog.test/blogs/one.html",
                                                "idea": "Also see https://my.site/x", "platforms": ["x"],
                                                "persona_id": p["id"]}).json()
    assert gen["drafts"]["x"]["unknown_links"] == []
    assert "There is no link to share" not in fake_gemini.prompts[0][1]


# ---- link in the first comment ------------------------------------------------------------------

def test_link_in_first_comment_keeps_the_link_out_of_the_text(app, client, fake_gemini):
    _seed(app)
    app.state.articles.fetch = lambda url, **kw: Page(PAGE)
    p = persona(client)
    fake_gemini.answers.append([{"platform": "linkedin", "text": "A reflection. Read: https://blog.test/blogs/one.html"},
                                {"platform": "bluesky", "text": "Short take. Link in the first comment."}])
    gen = client.post("/api/ai/generate", json={
        "source": "blog", "blog_url": "https://blog.test/blogs/one.html", "platforms": ["linkedin", "bluesky"],
        "persona_id": p["id"], "link_in_first_comment": True}).json()
    prompt = fake_gemini.prompts[0][1]
    assert "the link is posted as the first comment" in prompt and "Share it and include its link" not in prompt
    li = gen["drafts"]["linkedin"]
    assert li["text"] == "A reflection. Read:" and li["first_comment"] == "https://blog.test/blogs/one.html"
    assert gen["drafts"]["bluesky"]["first_comment"] == "https://blog.test/blogs/one.html"
    assert gen["link_in_comment"] is True


def test_drafts_labelled_with_platform_names_are_understood(client, fake_gemini):
    p = persona(client)
    fake_gemini.answers.append([{"platform": "LinkedIn", "text": "Long one"}, {"platform": "X (Twitter)", "text": "Short"}])
    gen = client.post("/api/ai/generate", json={"source": "idea", "idea": "Hi", "platforms": ["linkedin", "x"],
                                                "persona_id": p["id"]}).json()
    assert (gen["drafts"]["linkedin"]["text"], gen["drafts"]["x"]["text"]) == ("Long one", "Short")
