# 0004: Split the UI into Status, Analytics and Scheduler tabs, backed by an activity log

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

The app was one page: the calendar plus a sidebar. Three things needed their own place:

- **Status**: which apps are signed in, whether the backend and scheduler are
  healthy, and what happened recently.
- **Analytics**: how posts did, with filters.
- **Scheduler**: the calendar and blog planner that already existed.

Constraints: no framework or build step, one origin (nginx proxies `/api`),
SQLite as the only storage, and the backend as the authority for any counting.

What "post performance" can mean today: X's current API access can't read
timelines or metrics, and LinkedIn limits post analytics to approved partners.
The app only knows its own records: what it published, when, where, and what failed.

## Options considered

**Recent activity**
1. *Derive it from `posts` / `post_targets`*: no schema change, but deletions,
   edits, retries and blog syncs leave no trace, and failures carry no timestamp.
2. *An `activity` table written by the services*: one row per event, written
   as a plain sentence. Cost: one table and one write per event, capped at 1000 rows.

**Analytics bucketing (day / week / month)**
1. *In SQL on the backend*: needs the browser's time zone, and Python's
   `zoneinfo` needs tzdata, which the slim image may lack.
2. *Backend counts totals and filters; browser groups the matching
   deliveries by local day/week*: the same split the calendar already uses.

**Charts**
1. *A chart library*: needs npm or a CDN, against the no-build rule.
2. *Plain elements and CSS*: a small single-series column chart with hover,
   keyboard read-out and a table view.

## Decision

- Three tabs in the top bar (WAI-ARIA tabs; the hash in the URL keeps the
  tab; the last tab is remembered). `+ New post` stays global.
- `activity` table + `services/activity.py`. Services record events as they
  happen. `GET /api/activity` serves the newest first.
- `GET /api/system` (`services/monitor.py`) reports local facts only: uptime,
  scheduler's last check, queue counts, blog cache, database size. It never
  calls a platform, so the Status tab can poll it every 30 s. It also returns
  `attention`, a list of plain-language problems. Live platform checks stay
  on demand (`/api/platforms/{id}/status`).
- `GET /api/analytics` (`services/analytics.py`) filters by time, platform,
  source and status. It returns totals, per-platform counts, top failure
  reasons and the matching posts. The browser buckets by local time.
- Charts are plain HTML/CSS, one series in the accent colour. The platform
  colours fail colour-blind and contrast checks as a chart palette (Threads'
  grey and LinkedIn's teal read as grey; several fall outside the dark-mode
  lightness band). So the chart never colours by platform: the platform filter
  and the per-platform table (name + swatch) carry identity instead.

## Consequences

- Each service write now also writes an activity row. Cheap at this scale; the
  table prunes itself to 1000 rows.
- `PostService.create(..., log=False)` lets the blog planner record one
  summary instead of one line per post.
- The Status tab shows the scheduler stalling, failures and overdue posts
  without anyone opening the logs.
- Analytics shows publishing performance only. When a platform grants read
  access, engagement (likes, views) can be added per platform as a separate
  section, without changing the existing counts.
- If platform colours are ever made chart-safe (validated with the dataviz
  palette checker, with a dark-mode step per platform), the timeline could
  stack by platform.
