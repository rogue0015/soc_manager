# 0009: Bluesky with an app password and a session per action

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner gave their Bluesky handle and an app password and wants to post
there from the app. Bluesky runs on the AT Protocol: you sign in with `createSession` (handle +
password) to get a short-lived access token, then write records to your repository. Links and
hashtags are only clickable when the post lists them as *facets* with UTF-8 byte offsets, and
link previews are an embed the client builds itself. Public counts (likes, reposts, replies) are
readable without signing in.

## Options considered

1. **OAuth** (Bluesky's newer browser sign-in): no password stored, but needs a public client
   metadata URL and a redirect back to this local app; much more setup for one personal account.
2. **App password, new session per action** (chosen): two keys in `env`, nothing to refresh or
   store. Each post, delete or check signs in once; Bluesky's sign-in limits (dozens per few
   minutes) are far above what a personal scheduler does.
3. **App password with a cached session**: fewer sign-ins, but tokens to keep and refresh.

## Decision

`platforms/bluesky.py` signs in per action with `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` (a bare
handle gets `.bsky.social`), writes `app.bsky.feed.post` records with link and hashtag facets, and
adds an `app.bsky.embed.external` card when the post has a link card (blog posts, and posts with a
link from the composer). Card pictures over 1 MB are left out with a warning. Delete uses
`deleteRecord`. Stats read `app.bsky.feed.getPosts` from the public AppView: likes → reactions,
replies → comments, reposts + quotes → reshares. `BLUESKY_SERVICE` can point at another server.

## Consequences

- No images from the composer yet (only card pictures); adding them means uploading blobs and a
  Bluesky options section in the composer.
- No editing: Bluesky doesn't support it.
- The 300 limit is counted in characters; Bluesky counts graphemes, so an emoji-heavy post may be
  refused here although Bluesky would take it. Never the other way round.
- The app password sits in `env` like the other keys and can be revoked on its own in Bluesky.
