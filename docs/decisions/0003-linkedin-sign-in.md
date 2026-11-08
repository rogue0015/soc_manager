# 0003: Sign in to LinkedIn with a one-off script and keep the token in `env`

- **Status:** Accepted; amended by [0005](0005-sign-in-from-the-app.md) (sign in from the app; the script is now a fallback)
- **Date:** 2026-11-07

## Context

Posting to LinkedIn needs a member access token with the `w_member_social`
scope. LinkedIn only issues one through the 3-legged OAuth flow: the member
approves the app in a browser, LinkedIn redirects back with a one-time code,
and the app swaps that code (plus client ID and secret) for a token. The
client ID and secret on their own cannot post.

- Tokens last **60 days**. Refresh tokens are only for approved partners, so a
  personal app has to repeat the browser sign-in when the token runs out.
- A post's author is the member's person URN. Finding it needs the `openid`
  and `profile` scopes (product *Sign In with LinkedIn using OpenID Connect*)
  and a call to `/v2/userinfo`.
- The Posts API (`POST /rest/posts`) replaces `ugcPosts`. It needs a monthly
  `LinkedIn-Version` header, and LinkedIn retires each version after about a year.

Constraints from earlier decisions: secrets live only in `env`, the web UI has
no login, and the app is reachable on `127.0.0.1` only.

## Options considered

1. **"Connect LinkedIn" button in the app**: the backend handles the redirect
   and stores the token in SQLite. Smoothest, but it puts a secret in the
   database, needs callback routes for both run modes (ports 8000 and 8080)
   registered with LinkedIn, and adds OAuth state handling to a web app with
   no login.
2. **One-off sign-in script on the host**: it opens the browser, catches the
   redirect on `localhost:8765`, and writes the token and its expiry into
   `env`. Secrets stay in `env`, there are no new HTTP routes, and it works the
   same for local and Docker. Cost: a terminal step plus a backend restart
   every 60 days.
3. **Paste a token from LinkedIn's developer-portal token generator**: no
   code, but you copy a secret by hand and the app never learns the expiry date.

## Decision

Option 2: `scripts/linkedin_login.py` signs in and saves `LINKEDIN_ACCESS_TOKEN`
and `LINKEDIN_TOKEN_EXPIRES_AT` to `env`. The adapter
(`backend/app/platforms/linkedin.py`) posts with the Posts API, pinned to one
version (`API_VERSION`). Option 3 still works as a fallback because the adapter
only needs the token.

## Consequences

- Every 60 days you run the script and restart the backend. The Connections
  card shows the days left and warns during the last week. Once the token has
  expired, posts fail straight away with a "sign in again" message, without
  calling LinkedIn.
- The LinkedIn app must list `http://localhost:8765/linkedin/callback` as a
  redirect URL and have both products: *Share on LinkedIn* and *Sign In with
  LinkedIn using OpenID Connect*.
- `API_VERSION` must be moved forward about once a year. LinkedIn answers
  HTTP 426 when a version is retired, and the error message says what to change.
- Post text is escaped for LinkedIn's "little" text format, which otherwise
  drops the rest of a post at characters such as `(` or `_`. `#hashtags` are kept.
- Links go out as plain text. LinkedIn may not draw a preview card for them;
  a guaranteed card needs an article post with an uploaded thumbnail (future work).
- `config.py` now reads platform keys from the environment by prefix
  (`SECRET_PREFIXES`), so Docker passes `LINKEDIN_*` the same way as `X_*`.
- No new dependencies: the script uses the standard library's `http.server`,
  and `requests` was already pinned.
