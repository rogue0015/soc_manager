# 0008: Social tab, LinkedIn's own scheduler, past LinkedIn stats, downloadable reports

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner asked for four things:

1. Posts scheduled **on LinkedIn**, not held by this app, so they go out even when this
   computer is off.
2. Their **past** LinkedIn stats, not only for posts this app sent.
3. A **Social** tab with a sidebar of accounts; under each one its content, past stats and imports.
4. **Downloadable reports.**

What LinkedIn allows (Posts API docs, version 202610):

- Creating a post accepts only `lifecycleState: PUBLISHED`: an app can't create a scheduled
  post or a draft. LinkedIn's own scheduling exists only in its website and apps.
- Reading a member's own posts needs `r_member_social`, which is restricted to approved apps;
  per-post analytics need the Community Management API (registered companies only, ADR 0006).

## Options considered

For scheduling on LinkedIn:

1. **Keep the app as the scheduler** (as now): automatic, but needs this computer on.
2. **Hand the post to LinkedIn's composer** (chosen, as an option per post): the app copies the
   text and opens LinkedIn's post box; the owner sets the same time with LinkedIn's clock icon.
   LinkedIn then holds and posts it. One manual step per post, but it truly lives on LinkedIn.
3. **Host the app on an always-on server**: automatic and independent of this computer, but
   needs a login first (the app has none) and a hosting decision. Left for later.

For past stats:

1. Ask LinkedIn for approval for automatic stats: only for registered companies.
2. **Import LinkedIn's analytics export** (chosen; already supported for app posts, ADR 0006) and
   keep rows that match no app post as *imported past posts*.

For reports: CSV only (simple, opens anywhere) or an Excel workbook with a summary sheet.
We do **both**; the .xlsx is written with the standard library (zip + XML) like the .xlsx
reader, so no new dependency.

## Decision

- **Hand-off**: LinkedIn gets the `handoff` feature and a `compose_url` in the registry. A target
  with option `handoff: true` is never sent by the scheduler (`claim_due` skips it); once its time
  passes, `settle_handoffs` records it as published (`published_at` = the planned time) and logs
  that LinkedIn's scheduler posted it. A hand-off carries text only (no image, link card or first
  comment). The composer asks *Who posts it: This app / LinkedIn's scheduler*; after saving, a
  dialog copies the text, opens LinkedIn's post box and shows the time to set.
- **Past posts**: analytics rows that match no known post and have a date become imported
  posts (`post_targets.imported_at`, new column), dated noon UTC on that day, with their numbers.
  Importing again matches them by LinkedIn's post ID and updates the numbers. A hand-off that is
  matched gets its LinkedIn link from the file. Imported posts count as published but not towards
  the success rate.
- **Social tab** (`GET /api/social/{platform}`, `js/social.js`): sidebar of all registered
  platforms; per platform *Coming up*, *Stats* with *Best posts*, *Past posts*, *Import and refresh
  stats* and *Reports*. The LinkedIn import moved here from the Analytics tab.
- **Reports** (`GET /api/reports/posts?format=csv|xlsx`): one row per delivery with its status,
  who sent it, text, link and numbers, in the browser's time zone; the Excel file adds a summary
  sheet (totals per platform, best posts). Same filters as Analytics, so the numbers agree.

## Consequences

- LinkedIn posts can go out with this computer off, at the cost of finishing each one in LinkedIn.
  The app can't confirm a hand-off was scheduled or posted; it assumes it was. A later analytics
  import fills in its link and numbers.
- Changing a hand-off post in the app doesn't change it on LinkedIn; the post dialog says so.
- Whether LinkedIn pre-fills its post box from the `text` parameter isn't documented, so the app
  always copies the text too.
- Imported past posts have no text (the analytics file doesn't include it) and only cover what
  LinkedIn's export lists (its *top posts* for the chosen period). The importer's column names are
  still guessed (ADR 0006) and must be checked against a real export.
- Reports contain post text; they are files the owner downloads, so treat them like the data itself.
  CSV text cells that look like formulas are prefixed with `'`.
