"""Google Gemini over plain HTTPS (generateContent), for the AI generation tab.

Uses `requests`, which the app already has, rather than Google's SDK. Asks for JSON
that matches a schema, so drafts arrive as data, not prose to pick apart.

Settings from the env file: GEMINI_API_KEY, GEMINI_MODEL (default gemini-3.1-flash-lite,
cheap and fast), and the plan's limits GEMINI_RPM / GEMINI_RPD, which the app keeps
under (services/ai_writer.py). If Google still answers 429, a short per-minute limit is
waited out once and retried; anything longer, or the daily quota, becomes a plain
sentence saying when to try again. See docs/decisions/0012-ai-generation.md.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from typing import Any

import requests

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
# The plan's limits; check yours at aistudio.google.com/rate-limit and set GEMINI_RPM / GEMINI_RPD.
DEFAULT_RPM = 15
DEFAULT_RPD = 500
TIMEOUT = 90           # seconds; nginx allows 120 for the whole request
WAIT_AND_RETRY = 15    # a 429 asking to wait at most this long is waited out once


class GeminiError(Exception):
    """Gemini couldn't write the drafts. The message is shown to the user."""


class GeminiRateLimited(GeminiError):
    """Google refused because a limit was reached (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


def _int_setting(secrets: dict[str, str], key: str, default: int) -> int:
    try:
        return max(1, int(secrets.get(key) or default))
    except ValueError:
        return default


def _error(resp: requests.Response) -> dict[str, Any]:
    try:
        return resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return {}


def retry_delay(resp: requests.Response) -> float | None:
    """How long Google asks us to wait: RetryInfo.retryDelay ("34s") in the error, or a Retry-After header."""
    for detail in _error(resp).get("details") or []:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    header = (getattr(resp, "headers", None) or {}).get("Retry-After")
    return float(header) if header and str(header).isdigit() else None


def is_daily(resp: requests.Response) -> bool:
    """True when the exhausted quota is a per-day one (e.g. GenerateRequestsPerDayPerProjectPerModel)."""
    err = _error(resp)
    ids = [v.get("quotaId", "") for d in err.get("details") or [] for v in d.get("violations") or []]
    return any("PerDay" in i for i in ids) or "per day" in (err.get("message") or "").lower()


def _rate_limited(resp: requests.Response) -> GeminiRateLimited:
    delay = retry_delay(resp)
    if is_daily(resp):
        return GeminiRateLimited("Today's Gemini limit is used up. It resets at midnight Pacific time.", delay, True)
    when = f"in about {math.ceil(delay)} seconds" if delay else "in a minute"
    return GeminiRateLimited(f"Gemini's per-minute limit was reached. Try again {when}.", delay)


def _explain(resp: requests.Response) -> str:
    err = _error(resp)
    message = err.get("message") or resp.text[:200]
    if resp.status_code in (400, 401, 403) and ("API key" in message or err.get("status") in ("PERMISSION_DENIED",
                                                                                              "UNAUTHENTICATED")):
        return "Gemini didn't accept the API key. Check GEMINI_API_KEY in the env file, then restart the app."
    if resp.status_code == 404:
        return "Gemini doesn't know that model. Check GEMINI_MODEL in the env file (or remove it to use the default)."
    if resp.status_code >= 500:
        return "Gemini is having trouble right now. Try again in a minute."
    return f"Gemini answered HTTP {resp.status_code}: {message}"


class Gemini:
    """`http` is the requests module and `sleep` time.sleep; tests pass fakes."""

    def __init__(self, secrets: dict[str, str], http: Any = requests, sleep: Callable[[float], None] = time.sleep):
        self.key = secrets.get("GEMINI_API_KEY", "")
        self.model = secrets.get("GEMINI_MODEL") or DEFAULT_MODEL
        self.rpm = _int_setting(secrets, "GEMINI_RPM", DEFAULT_RPM)
        self.rpd = _int_setting(secrets, "GEMINI_RPD", DEFAULT_RPD)
        self.http = http
        self.sleep = sleep

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def _post(self, body: dict[str, Any]) -> requests.Response:
        try:
            return self.http.post(f"{API}/models/{self.model}:generateContent", json=body, timeout=TIMEOUT,
                                  headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise GeminiError(f"Couldn't reach Gemini ({type(exc).__name__}). "
                              "Check the internet connection and try again.") from exc

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any],
                      on_request: Callable[[], None] = lambda: None) -> dict[str, Any]:
        """Run one prompt with system instructions; return the JSON answer. `on_request` runs before each
        request actually sent (to count it against the limits). Raises GeminiError / GeminiRateLimited."""
        if not self.key:
            raise GeminiError("Add GEMINI_API_KEY to the env file (from Google AI Studio), then restart the app.")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
        }
        on_request()
        resp = self._post(body)
        if resp.status_code == 429:
            limited = _rate_limited(resp)
            if limited.daily or limited.retry_after is None or limited.retry_after > WAIT_AND_RETRY:
                raise limited
            self.sleep(limited.retry_after)   # a short per-minute limit: wait it out once
            on_request()
            resp = self._post(body)
            if resp.status_code == 429:
                raise _rate_limited(resp)
        if not resp.ok:
            raise GeminiError(_explain(resp))
        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict[str, Any]) -> dict[str, Any]:
        if block := (data.get("promptFeedback") or {}).get("blockReason"):
            raise GeminiError(f"Gemini refused this request ({block}). Rephrase the idea and try again.")
        candidates = data.get("candidates") or []
        if not candidates:
            raise GeminiError("Gemini sent back no drafts. Try again.")
        first = candidates[0]
        text = "".join(p.get("text", "") for p in (first.get("content") or {}).get("parts", [])
                       if not p.get("thought"))
        if not text:
            raise GeminiError(f"Gemini stopped without writing anything (reason: {first.get('finishReason', 'unknown')}). "
                              "Try again.")
        try:
            return json.loads(text)
        except ValueError as exc:
            if first.get("finishReason") == "MAX_TOKENS":
                raise GeminiError("Gemini ran out of room before finishing. Pick fewer platforms and try again.") from exc
            raise GeminiError("Gemini's answer wasn't in the expected format. Try again.") from exc
