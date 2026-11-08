"""Sign in to LinkedIn so Social Manager can post for you.

    .venv/bin/python scripts/linkedin_login.py

Opens LinkedIn in your browser. After you press "Allow", LinkedIn sends the
browser back to a one-off server this script runs on localhost, and the
script saves a 60-day access token into the env file. Run it again when the
Connections card says the sign-in is about to expire.

Needs LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in env, and the redirect
URL (default http://localhost:8765/linkedin/callback) added to the app's Auth
tab. Tokens are never printed. See README "LinkedIn setup" and
docs/decisions/0003-linkedin-sign-in.md.
"""

from __future__ import annotations

import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so it runs from any folder

from backend.app.config import Settings, set_env_values  # noqa: E402
from backend.app.platforms.linkedin import (  # noqa: E402
    DEFAULT_REDIRECT_URI, LinkedInPublisher, SignInError, authorization_url, exchange_code,
)
from backend.app.timeutil import to_utc_iso  # noqa: E402

WAIT_SECONDS = 300
DONE_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>Social Manager</title>"
    "<p style='font:16px system-ui;margin:2rem'>Done. You can close this tab and go back to the terminal.</p>"
).encode()


def wait_for_redirect(redirect_uri: str, wait_seconds: float = WAIT_SECONDS) -> dict[str, str]:
    """Serve the redirect URL on 127.0.0.1 until LinkedIn's redirect arrives.

    Returns its query parameters, or {} on timeout.
    """
    target = urlparse(redirect_uri)
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path != target.path:
                self.send_error(404)
                return
            received.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(DONE_PAGE)))
            self.end_headers()
            self.wfile.write(DONE_PAGE)

        def log_message(self, *args: object) -> None:
            pass  # the request line holds the one-time code: keep it off the terminal

    deadline = time.monotonic() + wait_seconds
    with HTTPServer(("127.0.0.1", target.port or 80), Handler) as server:
        while not received and (left := deadline - time.monotonic()) > 0:
            server.timeout = left
            server.handle_request()
    return received


def main() -> int:
    settings = Settings.from_environment()
    keys = settings.secrets
    missing = [k for k in ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET") if not keys.get(k)]
    if missing:
        print(f"Add {' and '.join(missing)} to {settings.env_file} first.")
        print("Find them at linkedin.com/developers/apps → your app → Auth.")
        return 1

    redirect_uri = keys.get("LINKEDIN_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    state = secrets.token_urlsafe(24)
    url = authorization_url(keys["LINKEDIN_CLIENT_ID"], redirect_uri, state)
    print("Opening LinkedIn in your browser. If nothing opens, visit:\n")
    print(f"  {url}\n")
    print(f"Waiting up to {WAIT_SECONDS // 60} minutes for you to press Allow (Ctrl+C to cancel).")
    print("If LinkedIn shows an error about the redirect URL or a scope, see README \"LinkedIn setup\".")
    webbrowser.open(url)

    try:
        reply = wait_for_redirect(redirect_uri)
    except OSError as exc:
        print(f"Could not listen on {redirect_uri}: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was saved.")
        return 1

    if not reply:
        print("No answer from LinkedIn in time. Nothing was saved; run the script again.")
        return 1
    if reply.get("state") != state:
        print("That reply did not come from this sign-in, so it was ignored. Nothing was saved.")
        return 1
    if "error" in reply:
        print(f"LinkedIn did not sign you in: {reply.get('error_description') or reply['error']}")
        return 1

    try:
        token = exchange_code(keys["LINKEDIN_CLIENT_ID"], keys["LINKEDIN_CLIENT_SECRET"],
                              reply.get("code", ""), redirect_uri)
    except SignInError as exc:
        print(exc)
        return 1

    saved = {"LINKEDIN_ACCESS_TOKEN": token.access_token,
             "LINKEDIN_TOKEN_EXPIRES_AT": to_utc_iso(token.expires_at)}
    set_env_values(settings.env_file, saved)
    status = LinkedInPublisher(saved).check()
    who = f" as {status.account}" if status.account else ""
    print(f"\nSigned in{who}. Token saved to {settings.env_file}, valid until {token.expires_at:%d %b %Y}.")
    if not status.can_post:
        print(f"But: {status.message}")
    print("\nRestart the app so it uses the new sign-in:")
    print("  Docker: docker compose up -d --force-recreate backend")
    print("  Local:  stop ./scripts/dev.sh (Ctrl+C) and start it again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
