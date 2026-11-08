# 0013: In-app dialogs instead of browser pop-ups; live Analytics with background stats

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The owner asked for all browser alerts to become in-app modals, and noted that the Analytics
tab wasn't showing real-time data: it loaded once when opened, and platform numbers (dev.to,
Bluesky, YouTube) only changed when someone pressed Refresh stats on the Social tab.
For AI drafts they asked to trash drafts, to remove drafts once posted, and, when a draft has a
link, to offer moving it to the first comment through a modal.

## Options considered

- Dialogs: keep `confirm()` (unstyled, can't offer three choices, blocks the page), or **one
  reusable `<dialog>`** (`js/modal.js`): `ask()` for any set of buttons, `confirmAction()` for yes/no.
- Live data: poll the platforms from the browser (calls platforms on every view, multiplies rate
  limits), push updates over a socket (new moving parts for one user), or **fetch platform numbers
  in the background job and let the tab reload from the database every minute while open** (chosen).

## Decision

- All eight `confirm()` calls use `confirmAction()`. Destructive questions focus Cancel; others focus
  the main button. Esc closes as "no".
- The scheduler loop also runs `LiveService.refresh_stats` at start and then every
  `SM_STATS_INTERVAL` seconds (default 1800). "No platform shares stats" is not an error; other
  failures are recorded (`stats_problem`) and never stop publishing. `/api/system` shows
  `stats_refreshed_at`, `stats_every_seconds` and `stats_problem`.
- Analytics reloads quietly every 60 s while its tab is open (not while hidden), shows "Live · updated
  …" with when platform numbers were last fetched, and has **Refresh now**.
- AI drafts: `trashed` flag per draft (restorable); drafts that became posts or were trashed leave
  the list, behind a "Show posted and trashed" toggle. Opening a draft with a link on a platform with
  a first comment (LinkedIn) asks: keep it, or move it to the first comment (the text says
  "(link in the first comment)" and no link preview is attached).

## Consequences

- Platform numbers are at most `SM_STATS_INTERVAL` old, plus a minute in the tab. LinkedIn numbers
  still come only from its analytics file (no API access for personal accounts).
- The background refresh calls each stats-sharing platform once per interval; with a few posts that
  is well inside their limits.
- Only LinkedIn has a first comment in the app; links on X or Bluesky stay in the post.
