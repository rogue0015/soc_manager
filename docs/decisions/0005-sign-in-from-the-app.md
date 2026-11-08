# 0005: Sign in to LinkedIn from the app; mount the env file into the backend

- **Status:** Accepted
- **Date:** 2026-11-07
- **Amends:** [0003](0003-linkedin-sign-in.md) (the terminal script becomes a fallback) and
  [0002](0002-docker.md) (the backend reads `env` as a mounted file instead of variables)

## Context

ADR 0003 signed in to LinkedIn with a terminal script. In practice you want to press a
button on the LinkedIn card and get LinkedIn's Allow page. For that, the backend must:

- send the browser to LinkedIn and receive the redirect back (OAuth callback), and
- save the 60-day token somewhere it survives restarts, without breaking the rule that
  secrets live only in the gitignored `env` file.

In Docker the backend received `env` once, as variables, at container start. It couldn't
write to the file, and a token saved anywhere else would be lost or would break that rule.
The container runs as uid 10001; `env` belongs to the host user (uid/gid 1000).

## Options considered

1. **Store the token in SQLite or the data volume**: no Docker change, but it puts a
   secret outside `env`.
2. **Run the backend as the host user**: it could write `env`, but the existing data
   volume belongs to uid 10001, so the database would break.
3. **Mount `env` into the backend and add the container to the file's group**, with the
   file at mode 660: the backend can update `env` in place; nothing else changes. On a
   typical Linux desktop, gid 1000 is the user's private group, so no one else gains access.

## Decision

Option 3.

- `GET /api/connect/{platform}` redirects to the platform's Allow page with a random
  `state`. `GET /api/connect/{platform}/callback` checks that state (single use, valid
  10 minutes), swaps the code for a token, writes it to `env`, updates the in-memory keys
  and rebuilds that platform's adapter. Posting works straight away, without a restart.
  Both routes end with a redirect back to `/#status` carrying a plain message.
- The redirect URL is `SM_PUBLIC_URL` + `/api/connect/linkedin/callback`:
  `http://localhost:8080` in Docker, `http://localhost:8000` locally. Register it with LinkedIn.
- `compose.yaml` mounts `./env` at `/config/env` (`SM_ENV_FILE`), adds the backend to
  group `SM_ENV_GID` (default 1000), and drops `env_file`. `env` should be mode 660.
- Blank environment variables no longer override values from the file.
- `scripts/linkedin_login.py` stays as a fallback, for example when the app runs
  somewhere LinkedIn can't redirect back to.

## Consequences

- One click to sign in, and the same click to renew every 60 days.
- The backend can write `env`. It only ever replaces lines for the keys a sign-in returns,
  and keeps every other line as it is (`config.set_env_values`).
- `docker compose up` now fails clearly if `env` is missing (`create_host_path: false`),
  instead of starting without keys: create it from `.env.example` first.
- If your user's group id isn't 1000, set `SM_ENV_GID=$(stat -c %g env)`.
- Keys you edit by hand still need a backend restart to load; only sign-in updates them live.
- Pending sign-ins live in memory: restarting the backend mid-sign-in means pressing the button again.
