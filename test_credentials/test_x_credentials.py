"""Check that the X (Twitter) API credentials in ../env work.

Run:  python3 test_credentials/test_x_credentials.py
Secret values are never printed.
"""

import base64
import hashlib
import hmac
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

import requests

ENV_FILE = Path(__file__).resolve().parent.parent / "env"
API = "https://api.x.com"


def load_env(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def explain(resp):
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("title") or body.get("errors") or body
    except ValueError:
        detail = resp.text[:200]
    return f"HTTP {resp.status_code}: {detail}"


def test_app_key_and_secret(env):
    """API key + secret: ask X for an app-only token (free on every tier)."""
    resp = requests.post(
        f"{API}/oauth2/token",
        auth=(env["X_API_KEY"], env["X_API_SECRET"]),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if resp.ok and resp.json().get("token_type") == "bearer":
        return True, "valid (X issued an app token)"
    return False, explain(resp)


def test_bearer_token(env):
    """Bearer token: look up a public account."""
    resp = requests.get(
        f"{API}/2/users/by/username/X",
        headers={"Authorization": f"Bearer {env['X_BEARER_TOKEN']}"},
        timeout=15,
    )
    if resp.ok:
        return True, "valid"
    if resp.status_code in (403, 429):
        # X accepted the token but the plan limits this endpoint or rate.
        return True, f"accepted, but access limited -> {explain(resp)}"
    return False, explain(resp)


def oauth1_header(method, url, env):
    params = {
        "oauth_consumer_key": env["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": env["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    q = lambda s: urllib.parse.quote(s, safe="")
    param_str = "&".join(f"{q(k)}={q(v)}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), q(url), q(param_str)])
    key = f"{q(env['X_API_SECRET'])}&{q(env['X_ACCESS_TOKEN_SECRET'])}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in params.items())


def test_user_access_tokens(env):
    """Access token + secret (with API key/secret): fetch your own account."""
    url = f"{API}/1.1/account/verify_credentials.json"
    resp = requests.get(url, headers={"Authorization": oauth1_header("GET", url, env)}, timeout=15)
    if resp.ok:
        return True, f"valid (signed in as @{resp.json().get('screen_name', '?')})"
    return False, explain(resp)


def test_oauth2_client(env):
    """Client ID + secret: send a fake sign-in code. Valid credentials get past
    the auth check and only the code is rejected (400); bad ones get 401."""
    if not env.get("X_CLIENT_ID") or not env.get("X_CLIENT_SECRET"):
        return False, "X_CLIENT_ID / X_CLIENT_SECRET not set in env"
    resp = requests.post(
        f"{API}/2/oauth2/token",
        auth=(env["X_CLIENT_ID"], env["X_CLIENT_SECRET"]),
        data={
            "grant_type": "authorization_code",
            "code": "fake",
            "redirect_uri": "http://localhost",
            "code_verifier": "x" * 43,
        },
        timeout=15,
    )
    if resp.status_code == 400 and resp.json().get("error") == "invalid_request":
        return True, "valid (X accepted the client, rejected only the fake code)"
    return False, explain(resp)


def test_v2_access(env):
    """Can the app use the v2 API (needed for posting)? Fails if the app isn't in a Project."""
    url = f"{API}/2/users/me"
    resp = requests.get(url, headers={"Authorization": oauth1_header("GET", url, env)}, timeout=15)
    if resp.ok:
        return True, "v2 API available"
    return False, explain(resp)


def main():
    if not ENV_FILE.exists():
        sys.exit(f"Missing env file: {ENV_FILE}")
    env = load_env(ENV_FILE)

    required = ["X_API_KEY", "X_API_SECRET", "X_BEARER_TOKEN", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        sys.exit(f"Empty or missing in env: {', '.join(missing)}")

    tests = [
        ("API key + secret", test_app_key_and_secret),
        ("Bearer token", test_bearer_token),
        ("Access token + secret", test_user_access_tokens),
        ("OAuth 2.0 client ID + secret", test_oauth2_client),
        ("v2 API access", test_v2_access),
    ]
    all_ok = True
    for name, fn in tests:
        try:
            ok, msg = fn(env)
        except requests.RequestException as e:
            ok, msg = False, f"network error: {e}"
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
