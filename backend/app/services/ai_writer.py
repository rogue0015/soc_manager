"""AI generation: one draft per platform from an idea or a blog post, in a persona's style.

The persona says how to write (services/personas.py); the platform registry says how
long each post may be, so personas stay interchangeable. Every draft is checked against
its platform's limit; drafts that are too long are sent back once to be shortened, and
marked "over" if they still are. Nothing is ever posted from here: drafts are opened in
the post window, where you schedule or post them yourself.

Every generation is remembered (the `generations` table): the inputs, a copy of the
persona as it was, the model, and the drafts, including which became posts.

Rate limits: every request to Gemini first passes `Usage`, which keeps the app under the
plan's requests per minute (in memory; a short wait is waited out) and per day (in the
`ai_usage` table, counted by Pacific day because that's when Google resets the quota).
See docs/decisions/0012-ai-generation.md.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import Database
from ..platforms import PLATFORMS, PlatformInfo
from ..platforms.base import URL_RE
from ..timeutil import now_iso
from .activity import ActivityLog, platform_names
from .article import ArticleReader
from .errors import InvalidInput, NotFound
from .gemini import Gemini, GeminiError
from .personas import PersonaService, system_instructions

MAX_ARTICLE = 15000      # characters of blog text sent to Gemini; plenty for a post
MAX_HISTORY = 200
WAIT_FOR_MINUTE = 10     # if the per-minute budget frees up within this many seconds, wait instead of refusing
KEEP_USAGE_DAYS = 30
EVENT_FIELDS = {"name": "Event", "day": "Day", "venue": "Venue", "takeaways": "Notable takeaways"}

# How posts usually read on each platform. Style hints only: the limits come from the registry.
WRITING_HINTS = {
    "x": "Punchy: one idea, a strong hook first. Each link counts as 23 characters.",
    "linkedin": "Professional but personal. The first two lines must hook the reader (LinkedIn hides the rest "
                "behind 'see more'). Short paragraphs with a blank line between them.",
    "threads": "Conversational, like talking to a friend. Keep it short.",
    "bluesky": "Casual and direct. Keep it short.",
    "facebook": "Friendly and personal; it can be a little longer.",
    "instagram": "A caption for a video (Reel): a strong first line, short lines, hashtags at the end.",
    "youtube": "A video description: the first two lines summarise the video, then the details. "
               "Also write a short, clear title.",
    "tiktok": "A short, energetic caption for a video, with a few hashtags.",
    "mastodon": "Plain and thoughtful. Write hashtags in CamelCase so screen readers can read them.",
}

BASE_RULES = """You write social media posts for one person, in their voice, as described in the persona below.

Rules that always apply:
- Use only the facts given in the request: the idea, the blog post and the event details. Never invent numbers, \
names, quotes, dates, results or links.
- Write exactly one post for each platform listed, in that platform's style, and stay well under its character limit.
- If a blog post link is given, include it once in each post.
- Write every post ready to publish: no placeholders, no notes to the author, no quotation marks around it.
- Answer with JSON only, as asked."""

DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "drafts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "platform": {"type": "STRING", "description": "The platform id, exactly as given."},
                    "text": {"type": "STRING"},
                    "title": {"type": "STRING", "description": "Only for platforms that need a title."},
                },
                "required": ["platform", "text"],
            },
        },
    },
    "required": ["drafts"],
}


def pacific_day(now: datetime) -> str:
    """The day as Google counts its daily quota (midnight Pacific time)."""
    try:
        tz = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:   # no time zone data installed: use Pacific standard time
        tz = timezone(timedelta(hours=-8))
    return now.astimezone(tz).date().isoformat()


class Usage:
    """Keeps requests to Gemini within the plan's limits, so Google rarely has to refuse one."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep):
        self.db = db
        self.clock = clock
        self.now = now
        self.sleep = sleep
        self._recent: deque[float] = deque()   # when the requests of the last minute were sent
        self._lock = threading.Lock()

    def used_today(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT requests FROM ai_usage WHERE day = ?", (pacific_day(self.now()),)).fetchone()
        return row["requests"] if row else 0

    def _forget_old(self, now: float) -> None:
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()

    def take(self, per_minute: int, per_day: int) -> None:
        """Count one request. Waits briefly, or refuses with a sentence, when a limit would be passed."""
        if self.used_today() >= per_day:
            raise InvalidInput(f"Today's {per_day} Gemini requests are used up. They reset at midnight Pacific time. "
                               "(Your plan may allow more: set GEMINI_RPD in the env file.)")
        with self._lock:
            now = self.clock()
            self._forget_old(now)
            if len(self._recent) >= per_minute:
                wait = 60 - (now - self._recent[0])
                if wait > WAIT_FOR_MINUTE:
                    raise InvalidInput(f"That's the {per_minute} Gemini requests allowed per minute. "
                                       f"Try again in about {math.ceil(wait)} seconds.")
                self.sleep(wait)
                now = self.clock()
                self._forget_old(now)
            self._recent.append(now)
        day = pacific_day(self.now())
        oldest = (datetime.fromisoformat(day) - timedelta(days=KEEP_USAGE_DAYS)).date().isoformat()
        with self.db.connect() as conn:
            conn.execute("INSERT INTO ai_usage (day, requests) VALUES (?, 1) "
                         "ON CONFLICT(day) DO UPDATE SET requests = requests + 1", (day,))
            conn.execute("DELETE FROM ai_usage WHERE day < ?", (oldest,))


def writable(info: PlatformInfo) -> bool:
    """Platforms that take post text. dev.to publishes the whole blog post, so there's nothing to write."""
    return "article" not in info.features


def _limit_line(info: PlatformInfo) -> str:
    line = f"- {info.id} ({info.name}): at most {info.char_limit} characters"
    if info.url_weight:
        line += f" (each link counts as {info.url_weight})"
    if info.title_limit:
        line += f"; also write a title (in the title field) of at most {info.title_limit} characters"
    hint = WRITING_HINTS.get(info.id, f"A post that suits {info.name}.")
    return f"{line}. Style: {hint}"


def _links(text: str) -> list[str]:
    return [m.group().rstrip(".,;:!?)]}'\"").rstrip("/") for m in URL_RE.finditer(text or "")]


def allowed_links(inputs: dict[str, Any], article: dict[str, Any] | None) -> set[str]:
    """The links you gave: the blog post, and any in the idea or the event details."""
    found = set(_links(inputs.get("idea", "")))
    for value in (inputs.get("event") or {}).values():
        found |= set(_links(value))
    if article:
        found.add(article["canonical_url"].rstrip("/"))
    return found


def _check(info: PlatformInfo, text: str, title: str, allowed: set[str] = frozenset()) -> dict[str, Any]:
    """A draft with its length measured the way the platform counts it, and any link you didn't give."""
    length = info.text_length(text)
    draft: dict[str, Any] = {"text": text, "length": length, "limit": info.char_limit,
                             "over": length > info.char_limit, "post_id": None, "error": None,
                             "unknown_links": [link for link in _links(text) if link not in allowed]}
    if info.title_limit:
        draft.update(title=title, title_over=len(title) > info.title_limit, title_missing=not title)
    return draft


class AIWriter:
    def __init__(self, db: Database, personas: PersonaService, articles: ArticleReader, activity: ActivityLog,
                 gemini: Callable[[], Gemini]):
        self.db = db
        self.personas = personas
        self.articles = articles
        self.activity = activity
        self.gemini = gemini   # a factory, so a new key in env is used without rebuilding the service
        self.usage = Usage(db)

    def status(self) -> dict[str, Any]:
        """Key set? Which model, the plan's limits, and how many requests were used today. No call to Gemini."""
        g = self.gemini()
        used = self.usage.used_today()
        return {"ready": g.ready, "model": g.model, "per_minute": g.rpm, "per_day": g.rpd,
                "used_today": used, "left_today": max(0, g.rpd - used),
                "message": "Ready." if g.ready else "Add GEMINI_API_KEY to the env file, then restart the app."}

    # ---- generating ----

    def generate(self, source: str, idea: str, blog_url: str | None, event: dict[str, str],
                 platforms: list[str], persona_id: int) -> dict[str, Any]:
        platforms = self._platforms(platforms)
        persona = self.personas.get(persona_id)
        idea = idea.strip()
        if source == "idea" and not idea:
            raise InvalidInput("Write the idea for the post first.")
        article = None
        if source == "blog":
            if not blog_url:
                raise InvalidInput("Pick a blog post to write about.")
            article = self.articles.preview(blog_url)   # must be in the blog library
        event = {k: (event.get(k) or "").strip() for k in EVENT_FIELDS}
        inputs = {"source": source, "idea": idea, "blog_url": blog_url if source == "blog" else None, "event": event}
        drafts = self._write(persona, inputs, article, platforms)
        now = now_iso()
        with self.db.connect() as conn:
            gen_id = conn.execute(
                "INSERT INTO generations (created_at, source, idea, blog_url, event, platforms, persona_id, persona, "
                "model, drafts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, source, idea, inputs["blog_url"], json.dumps(event), json.dumps(platforms), persona["id"],
                 json.dumps(persona), self.gemini().model, json.dumps(drafts)),
            ).lastrowid
            conn.execute(f"DELETE FROM generations WHERE id NOT IN "
                         f"(SELECT id FROM generations ORDER BY id DESC LIMIT {MAX_HISTORY})")
        self.activity.record("ai_generated", f"Wrote drafts for {platform_names(platforms)} "
                                             f"with the “{persona['name']}” persona.")
        return self.get(gen_id)

    def regenerate(self, gen_id: int, platform: str) -> dict[str, Any]:
        """A new draft for one platform, from the same inputs and persona as before."""
        gen = self.get(gen_id)
        if platform not in gen["drafts"]:
            raise NotFound("That platform isn't part of this generation.")
        article = self.articles.preview(gen["blog_url"]) if gen["source"] == "blog" else None
        inputs = {k: gen[k] for k in ("source", "idea", "blog_url", "event")}
        old = gen["drafts"][platform].get("text", "")
        fresh = self._write(gen["persona"], inputs, article, [platform],
                            extra=f"Write a different version from this earlier one:\n{old}" if old else "")
        gen["drafts"][platform] = fresh[platform]
        with self.db.connect() as conn:
            conn.execute("UPDATE generations SET drafts=? WHERE id=?", (json.dumps(gen["drafts"]), gen_id))
        return self.get(gen_id)

    def _platforms(self, platforms: list[str]) -> list[str]:
        platforms = list(dict.fromkeys(platforms))
        if not platforms:
            raise InvalidInput("Choose at least one platform.")
        for pid in platforms:
            if pid not in PLATFORMS:
                raise InvalidInput(f"Unknown platform: {pid}")
            if not writable(PLATFORMS[pid]):
                raise InvalidInput(f"{PLATFORMS[pid].name} publishes the whole blog post, so there's nothing to "
                                   f"write for it. Untick {PLATFORMS[pid].name}.")
        return platforms

    def _prompt(self, inputs: dict[str, Any], article: dict[str, Any] | None, platforms: list[str],
                extra: str = "") -> str:
        parts = ["Write one post for each of these platforms:", *(_limit_line(PLATFORMS[p]) for p in platforms)]
        if article:
            text = article["markdown"]
            if len(text) > MAX_ARTICLE:
                text = text[:MAX_ARTICLE] + "\n[…the rest of the post is left out]"
            parts += ["", "What the posts are about: this blog post. Share it and link to it.",
                      f"Title: {article['title']}", f"Summary: {article['description']}",
                      f"Link: {article['canonical_url']}", "Full text:", text]
        if inputs["idea"]:
            parts += ["", "The idea, in the author's words:" if not article else "What the author wants to say about it:",
                      inputs["idea"]]
        if not allowed_links(inputs, article):
            parts += ["", "There is no link to share: don't add any link or web address."]
        event = [f"- {label}: {inputs['event'][k]}" for k, label in EVENT_FIELDS.items() if inputs["event"].get(k)]
        if event:
            parts += ["", "Event recap (make it part of the post):", *event]
        if extra:
            parts += ["", extra]
        return "\n".join(parts)

    def _ask(self, system: str, prompt: str) -> dict[str, dict[str, str]]:
        g = self.gemini()
        try:
            answer = g.generate_json(system, prompt, DRAFT_SCHEMA, on_request=lambda: self.usage.take(g.rpm, g.rpd))
        except GeminiError as exc:
            raise InvalidInput(str(exc)) from exc
        out = {}
        for d in answer.get("drafts") or []:
            if isinstance(d, dict) and d.get("platform") in PLATFORMS and isinstance(d.get("text"), str):
                out[d["platform"]] = {"text": d["text"].strip(), "title": (d.get("title") or "").strip()}
        return out

    def _write(self, persona: dict[str, Any], inputs: dict[str, Any], article: dict[str, Any] | None,
               platforms: list[str], extra: str = "") -> dict[str, dict[str, Any]]:
        system = f"{BASE_RULES}\n\n{system_instructions(persona)}"
        allowed = allowed_links(inputs, article)
        written = self._ask(system, self._prompt(inputs, article, platforms, extra))
        drafts = {p: _check(PLATFORMS[p], written[p]["text"], written[p]["title"], allowed)
                  for p in platforms if p in written}
        too_long = [p for p, d in drafts.items() if d["over"] or d.get("title_over") or d.get("title_missing")]
        if too_long:   # one more try, for just those, saying exactly what to fix
            def fix(p: str) -> str:
                d, info = drafts[p], PLATFORMS[p]
                parts = []
                if d["over"]:
                    parts.append(f"the text was {d['length']} characters; rewrite it to at most {int(info.char_limit * 0.9)}.")
                if d.get("title_over"):
                    parts.append(f"keep the title under {info.title_limit} characters.")
                if d.get("title_missing"):
                    parts.append(f"write a title (in the title field) of at most {info.title_limit} characters.")
                return f"- {p}: " + " ".join(parts)
            notes = "\n".join(fix(p) for p in too_long)
            shorter = self._ask(system, self._prompt(inputs, article, too_long,
                                                     extra + "\n\nThese drafts need fixing:\n" + notes))
            for p in too_long:
                if p in shorter:
                    drafts[p] = _check(PLATFORMS[p], shorter[p]["text"], shorter[p]["title"], allowed)
        for p in platforms:
            if p not in drafts:
                drafts[p] = {"text": "", "length": 0, "limit": PLATFORMS[p].char_limit, "over": False,
                             "post_id": None, "unknown_links": [],
                             "error": f"Gemini didn't write a draft for {PLATFORMS[p].name}. Press Regenerate."}
        return {p: drafts[p] for p in platforms}

    # ---- history ----

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        gen = dict(row)
        for key in ("event", "platforms", "persona", "drafts"):
            gen[key] = json.loads(gen[key])
        return gen

    def get(self, gen_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM generations WHERE id = ?", (gen_id,)).fetchone()
        if row is None:
            raise NotFound("That generation isn't in the history any more.")
        return self._row(row)

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM generations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, gen_id: int) -> None:
        self.get(gen_id)
        with self.db.connect() as conn:
            conn.execute("DELETE FROM generations WHERE id = ?", (gen_id,))

    def set_trashed(self, gen_id: int, platform: str, trashed: bool) -> dict[str, Any]:
        """Trash a draft you won't use (or bring it back). The history keeps it either way."""
        gen = self.get(gen_id)
        if platform not in gen["drafts"]:
            raise NotFound("That platform isn't part of this generation.")
        gen["drafts"][platform]["trashed"] = trashed
        with self.db.connect() as conn:
            conn.execute("UPDATE generations SET drafts=? WHERE id=?", (json.dumps(gen["drafts"]), gen_id))
        return self.get(gen_id)

    def mark_used(self, gen_id: int, platform: str, post_id: int) -> dict[str, Any]:
        """Remember that a draft became a post (it was saved in the post window)."""
        gen = self.get(gen_id)
        if platform not in gen["drafts"]:
            raise NotFound("That platform isn't part of this generation.")
        gen["drafts"][platform]["post_id"] = post_id
        with self.db.connect() as conn:
            conn.execute("UPDATE generations SET drafts=? WHERE id=?", (json.dumps(gen["drafts"]), gen_id))
        return self.get(gen_id)
