"""Sign in to a platform from the app (browser OAuth), e.g. "Sign in with LinkedIn".

    GET /api/connect/{platform}            -> redirect to the platform's Allow page
    GET /api/connect/{platform}/callback   -> swap the code for a token, save it, back to #status

The token is written to the env file (secrets live only there) and the shared
secrets dict, and the platform's adapter is rebuilt, so posting works without
a restart. In Docker the env file is mounted into the backend for this
(compose.yaml). Each sign-in gets a random `state` that is accepted once,
within STATE_TTL, which stops forged callbacks. See ADR 0005.
"""

from __future__ import annotations

import logging
import secrets as random_tokens
from collections.abc import Callable
from datetime import datetime, timedelta

from ..config import Settings, set_env_values
from ..platforms import PLATFORMS, SIGN_IN, Publisher, build_publisher
from ..platforms.linkedin import SignInError
from ..timeutil import now_utc
from .activity import ActivityLog
from .errors import InvalidInput, NotFound

log = logging.getLogger(__name__)
STATE_TTL = timedelta(minutes=10)


class SignInService:
    def __init__(self, settings: Settings, publishers: dict[str, Publisher], activity: ActivityLog,
                 now: Callable[[], datetime] = now_utc):
        self.settings = settings
        self.publishers = publishers   # the same dict the scheduler and routes use
        self.activity = activity
        self.now = now
        self._pending: dict[str, tuple[str, datetime]] = {}  # state -> (platform, issued at)

    def redirect_uri(self, platform: str) -> str:
        """The address the platform sends the browser back to. Must be registered with it exactly."""
        return f"{self.settings.public_url}/api/connect/{platform}/callback"

    def redirect_uris(self) -> dict[str, str]:
        return {pid: self.redirect_uri(pid) for pid in SIGN_IN}

    def _provider(self, platform: str):
        if platform not in SIGN_IN:
            name = PLATFORMS[platform].name if platform in PLATFORMS else platform
            raise NotFound(f"{name} doesn't use a browser sign-in.")
        return SIGN_IN[platform]

    def start(self, platform: str) -> str:
        """The URL of the platform's Allow page for a fresh sign-in."""
        provider = self._provider(platform)
        now = self.now()
        self._pending = {s: v for s, v in self._pending.items() if now - v[1] < STATE_TTL}
        state = random_tokens.token_urlsafe(24)
        try:
            url = provider.sign_in_url(self.settings.secrets, self.redirect_uri(platform), state)
        except ValueError as exc:
            raise InvalidInput(str(exc)) from exc
        self._pending[state] = (platform, now)
        return url

    def finish(self, platform: str, code: str, state: str, error: str = "", error_description: str = "") -> str:
        """Complete the sign-in from the platform's redirect. Returns a sentence for the user."""
        provider = self._provider(platform)
        name = PLATFORMS[platform].name
        pending = self._pending.pop(state, None)
        if not pending or pending[0] != platform or self.now() - pending[1] >= STATE_TTL:
            raise InvalidInput(f"That {name} sign-in was out of date or wasn't started here. "
                               f"Press “Sign in with {name}” again.")
        if error:
            raise InvalidInput(f"{name} didn't sign you in: {error_description or error}")
        if not code:
            raise InvalidInput(f"{name} sent no sign-in code back. Try again.")
        try:
            values = provider.finish_sign_in(self.settings.secrets, code, self.redirect_uri(platform))
        except SignInError as exc:
            raise InvalidInput(str(exc)) from exc

        try:
            set_env_values(self.settings.env_file, values)
        except OSError as exc:
            log.error("Could not write the %s token to %s: %s", name, self.settings.env_file, exc)
            raise InvalidInput(f"Signed in, but the token couldn't be saved to the env file "
                               f"({exc.strerror or exc}). Check that the backend can write to it.") from exc
        self.settings.secrets.update(values)
        publisher = build_publisher(platform, self.settings.secrets)
        if publisher is None:
            raise InvalidInput(f"Signed in, but {name} still isn't ready. Check the Connectors card.")
        self.publishers[platform] = publisher

        status = publisher.check()  # one call, to name the account in the message
        who = f" as {status.account}" if status.account else ""
        self.activity.record("signed_in", f"Signed in to {name}{who}.", platform=platform)
        message = f"{name} connected{who}."
        return message if status.can_post else f"{message} But: {status.message}"
