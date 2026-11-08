# 0010: Post now in the request; account profiles from the platforms, kept 10 minutes

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Two requests: a **Post now** button next to Schedule, and a Social tab sidebar that shows the
accounts you're signed in to (name, handle, picture, counts).

Post now must never send a delivery twice, also when the background scheduler checks at the
same moment, and it must give a result straight away (CLAUDE.md: instant feedback, and confirm
before anything public).

Profiles need a call to each platform. What they share differs: X (v1.1 verify_credentials)
gives counts; Bluesky's public profile gives counts without signing in; dev.to gives name,
picture and bio (no follower count); LinkedIn's OpenID userinfo gives only name and picture.

## Options considered

Post now:

1. Save the post with time = now and let the scheduler pick it up: no new code path, but up to
   30 s of waiting, nothing at all if the scheduler is off, and no direct result to show.
2. **Send in the request** (chosen): `POST /api/posts/{id}/publish` moves the post's time to now,
   claims its waiting deliveries with the same atomic `UPDATE … RETURNING` as the scheduler
   (`claim_post`), and sends them through the scheduler's own `send_targets`. Whoever claims a
   delivery first sends it; the other finds nothing.

Profiles:

1. Ask the platforms every time the tab opens: always fresh, but slow and wasteful of rate limits.
2. Store profiles in the database: survives restarts, but it's data we don't need to keep.
3. **Ask in parallel and keep them in memory for 10 minutes** (chosen; problems for 1 minute), with
   a Refresh button. Nothing is stored; a restart forgets them.

## Decision

- Post now: the composer saves the post (create or update), then calls `/publish`; the browser
  asks for confirmation first. All platforms succeeding closes the dialog with a toast; any failure
  reopens the post showing each platform's result, with Retry failed. A post handed to LinkedIn's
  own scheduler (ADR 0008) is refused with a sentence saying how to post it now instead.
- Profiles: `Publisher.profile()` returns an `AccountProfile` (name, handle, url, avatar, followers,
  following, posts, bio; missing = not shared). `GET /api/profiles` lists connected platforms in
  registry order via `services/profiles.py`. The sidebar shows each account's picture (ringed in the
  platform's colour) and handle; the selected account gets a profile card. Pictures load straight
  from the platforms' servers with `referrerpolicy="no-referrer"`.

## Consequences

- Post now sends in the HTTP request, so a slow platform (an article fetch for dev.to, an image
  upload for LinkedIn) keeps the button busy for a few seconds; nginx allows 120 s.
- Profile counts can be up to 10 minutes old; Refresh asks again.
- LinkedIn shows no follower numbers, and dev.to shows the number of published articles only;
  the card says so rather than showing zeros.
- Opening the Social tab calls each connected platform at most once per 10 minutes (X's
  verify_credentials allows 75 calls per 15 minutes).
