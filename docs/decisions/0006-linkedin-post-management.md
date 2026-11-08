# 0006: Manage LinkedIn posts from the app, and get per-post stats by import

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

With LinkedIn signed in (ADR 0005), the owner approved eight features: delete and edit
live posts, images and link preview cards, a first comment, visibility and repost
options, a separate LinkedIn text, automatic per-post statistics, and importing
LinkedIn's analytics file.

What the app's access allows. The products are *Share on LinkedIn* (`w_member_social`) and
*Sign In with LinkedIn using OpenID Connect*:

- `w_member_social` lets the app post, comment and like for the member, upload images
  owned by the member (Images API), and update or delete the member's own posts.
- Per-post statistics (`memberCreatorPostAnalytics`, scope `r_member_postAnalytics`) belong to
  the Community Management API. It is only for registered legal organisations: business
  email, a company LinkedIn Page, a new app with no other products, and a LinkedIn review.
- Reading posts and comments back (`r_member_social`) is closed to new applicants.

## Options considered

**Per-post statistics**
1. *API only*: the cleanest data, but unavailable to a personal account today.
2. *Import the analytics file the member exports from linkedin.com/analytics*: works
   today; manual; the columns aren't documented, and it only covers the posts LinkedIn includes.
3. *Both*: build the API path switched off, and the import path working now.

**Where post options live**
1. *New columns per option on `post_targets`*: rigid; every new option means a migration.
2. *A JSON `options` column per target, plus `body_override` for per-platform text*:
   platforms honour what `PlatformInfo.features` lists, and the service validates it.

## Decision

- Stats: option 3. The API path turns on only when a sign-in actually grants
  `r_member_postAnalytics`; the scopes asked for come from `LINKEDIN_SCOPES`, and the
  scopes granted are saved as `LINKEDIN_GRANTED_SCOPES`. The import reads `.xlsx` (with
  the standard library) or `.csv`, finds a table with a post link column, and matches
  rows to posts by the ID in the link, or else by publish date when only one post fits.
  An import never overwrites numbers from the API.
- Options: option 2. New columns `body_override`, `options`, `warning`, `removed_at`,
  `edited_at` on `post_targets` (added on start-up by `db.py`), and new tables `media`
  (uploaded images, files under the data folder) and `post_metrics`.
- Live actions (`services/live.py`) check that the post really is live and not deleted,
  and that the platform supports the action. The planner keeps its record: a deleted post
  is marked, not removed.
- Anything that fails *after* the post is live (the first comment, the preview picture)
  becomes a warning on that post, never a failure. A retry would post it twice.
- Uploads are sent as the raw request body, so there is no `python-multipart` dependency.
  Images are checked by their first bytes (JPEG, PNG or GIF) and capped at 8 MB; nginx
  allows 10 MB on `/api/`.

## Consequences

- Image uploads, link cards, comments, edits and deletes add LinkedIn calls. Each failure
  becomes a readable message on the post or in the dialog.
- If LinkedIn has not finished processing an uploaded image, a post can be rejected. The
  post then fails with LinkedIn's message and can be retried; watch for this in practice.
- The import's column guesses may need adjusting after the first real export.
- Automatic stats need a separate company app and a fresh sign-in with
  `LINKEDIN_SCOPES` including `r_member_postAnalytics`. Per LinkedIn's terms, check its
  data-storage rules then; stats are refreshed on demand, not kept forever.
- X ignores the new options for now. Its images would need X's media upload.
