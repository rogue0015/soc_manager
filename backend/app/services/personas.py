"""Personas for AI generation: how the posts should sound (tone, voice, language, format, ...).

A persona is the writer's style only. It never sets lengths or platforms, so any persona
works with any platform: the limits come from the platform registry (services/ai_writer.py).
Each field becomes a section of Gemini's system instructions. Stored in the `personas`
table; a generation keeps a copy of the persona it used. See ADR 0012.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import Database
from ..timeutil import now_iso
from .errors import Conflict, InvalidInput, NotFound

# Field -> heading in the system instructions, in this order.
FIELDS = {
    "tone": "Tone",
    "voice": "Voice and audience",
    "language": "Language",
    "format": "Format",
    "hashtags": "Hashtags",
    "emoji": "Emoji",
    "always": "Always",
    "never": "Never",
    "example": "Example of a post in this style (copy the style, not the content)",
}

# Starting points written from the owner's blog voice. Added only when asked for.
STARTERS: list[dict[str, str]] = [
    {
        "name": "Community Builder",
        "tone": "Warm, confident and encouraging, with a little self-deprecating humour.",
        "voice": "First person. Speaks to developers, community organisers and tech leaders in Zimbabwe and Africa.",
        "language": "English with UK spelling. An occasional Shona phrase is fine.",
        "format": "A hook in the first line. Short paragraphs. One clear lesson or takeaway. End with a question "
                  "that invites replies.",
        "hashtags": "2 to 3 at the end, lowercase, relevant (e.g. #techcommunity #ai).",
        "emoji": "At most 2, never in the first line.",
        "always": "Credit people and organisations by name. Link the blog post when there is one.",
        "never": "Corporate buzzwords (synergy, leverage, game-changer), clickbait, invented facts or numbers.",
        "example": "Some people do not know where to stop... and when. In recent weeks I have been making a bunch of "
                   "small tools that run fully offline in your browser. Here is what it taught me about edge compute "
                   "and privacy.",
    },
    {
        "name": "Builder in Public",
        "tone": "Casual, playful and honest, like telling a friend what you built this week.",
        "voice": "First person, relaxed. Speaks to fellow builders and curious readers.",
        "language": "Plain English, UK spelling. Informal words like 'lol' or 'stuff' are fine.",
        "format": "Short. One idea per post. Open with what happened, then why it matters.",
        "hashtags": "At most 1, only if it helps people find the post.",
        "emoji": "1 or 2 where they add feeling.",
        "always": "Be specific about what was built or learned.",
        "never": "Hype, humble-bragging, invented facts or numbers.",
        "example": "So yeah, I went on a wave of tinkering to see what the browser could do lol. Turns out: a lot.",
    },
]


def system_instructions(persona: dict[str, Any]) -> str:
    """The persona as a block of Gemini system instructions: one section per filled-in field."""
    parts = [f"Persona: {persona['name']}"]
    parts += [f"{title}:\n{persona[key].strip()}" for key, title in FIELDS.items() if (persona.get(key) or "").strip()]
    return "\n\n".join(parts)


class PersonaService:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM personas ORDER BY name COLLATE NOCASE")]

    def get(self, persona_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM personas WHERE id = ?", (persona_id,)).fetchone()
        if row is None:
            raise NotFound("That persona doesn't exist any more. Pick another one.")
        return dict(row)

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, str]:
        name = (data.get("name") or "").strip()
        if not name:
            raise InvalidInput("Give the persona a name.")
        clean = {"name": name, **{k: (data.get(k) or "").strip() for k in FIELDS}}
        if not any(clean[k] for k in FIELDS):
            raise InvalidInput("Fill in at least one field (the tone, for example), so the persona says how to write.")
        return clean

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        clean, now = self._clean(data), now_iso()
        cols = ["name", *FIELDS]
        try:
            with self.db.connect() as conn:
                persona_id = conn.execute(
                    f"INSERT INTO personas ({', '.join(cols)}, created_at, updated_at) "
                    f"VALUES ({', '.join('?' * len(cols))}, ?, ?)",
                    (*(clean[c] for c in cols), now, now),
                ).lastrowid
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"A persona called “{clean['name']}” already exists. Pick another name.") from exc
        return self.get(persona_id)

    def update(self, persona_id: int, data: dict[str, Any]) -> dict[str, Any]:
        self.get(persona_id)
        clean = self._clean(data)
        cols = ["name", *FIELDS]
        try:
            with self.db.connect() as conn:
                conn.execute(f"UPDATE personas SET {', '.join(f'{c}=?' for c in cols)}, updated_at=? WHERE id=?",
                             (*(clean[c] for c in cols), now_iso(), persona_id))
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"A persona called “{clean['name']}” already exists. Pick another name.") from exc
        return self.get(persona_id)

    def delete(self, persona_id: int) -> None:
        """Earlier generations keep their own copy of the persona, so their history stays readable."""
        self.get(persona_id)
        with self.db.connect() as conn:
            conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))

    def add_starters(self) -> list[dict[str, Any]]:
        """Add the starter personas that aren't there yet (by name)."""
        have = {p["name"].lower() for p in self.list()}
        for starter in STARTERS:
            if starter["name"].lower() not in have:
                self.create(starter)
        return self.list()
