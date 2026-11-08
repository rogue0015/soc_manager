# Social Manager

A personal portal for managing my digital footprint: plan, schedule and
publish posts across social platforms from one monthly calendar, and turn
my blog ([rogue0015.github.io/blogs](https://rogue0015.github.io/blogs/))
into a posting schedule.

Two parts: a **frontend** (plain HTML/CSS/JS, no build step) and a
**backend** (Python, FastAPI, SQLite). FastAPI is the bridge: the browser
only talks to the API, and the API talks to the database, the blog and
the social platforms. Runs on your own machine, in Docker or directly
with Python.

---

## Project status

_Last updated: 2026-11-07_

| Area | Status | Notes |
|---|---|---|
| Tabs: Status · Analytics · Scheduler | ✅ Done | Links work (`#status`, `#analytics`, `#scheduler`); the last tab is remembered. |
| Status tab | ✅ Done | System monitor (backend, scheduler, queue, blog), problems that need attention, connectors, recent activity. Refreshes every 30 s. |
| Connectors | ✅ Done | One card per platform on the Status tab: state, setup checklist (key names only), next step, live **Check**, developer-portal link. |
| Analytics tab | ✅ Done | Published, failed and success rate; per day/week chart; per-platform table; why posts failed; post list. Filters: period, platform, source, status. |
| LinkedIn post management | ✅ Done | Own LinkedIn text, image or link preview card, who can see it, reposts on/off, first comment; **Edit on LinkedIn** and **Delete on LinkedIn** for live posts. |
| LinkedIn per-post stats | ✅ By import | Import the file from LinkedIn analytics; impressions, reactions, comments, … show per post and in totals. |
| Automatic LinkedIn stats | ⚠️ Needs LinkedIn approval | Built, switched off. LinkedIn only grants it to registered companies (Community Management API). |
| X likes and views | ⚠️ Blocked | X's current API tier can't read them. |
| Monthly content calendar | ✅ Done | Scheduler tab. Click a day to schedule; click an entry to edit. Colour-coded by platform. |
| Custom posts | ✅ Done | Write your own text, pick platforms, date and time. |
| Blog import | ✅ Done | Reads the blog's sitemap and post metadata (19 posts). |
| Blog auto-plan | ✅ Done | Select posts, choose a start date and spacing; one post per slot is created. |
| Background publisher | ✅ Done | Sends due posts every 30 s while the backend runs. Failed posts can be retried. |
| Docker | ✅ Done | Separate frontend (nginx) and backend (FastAPI) containers, one command to start. |
| X (Twitter) connection | ⚠️ Blocked by X | Credentials valid (@Rogue0015). Posting is blocked until the X developer app is attached to a Project ([details](#x-api-access)). |
| LinkedIn connection | ✅ Built, needs sign-in | Posts to your own feed. Press **Sign in with LinkedIn** on the Status tab ([LinkedIn setup](#linkedin-setup)). The sign-in lasts 60 days. |
| Test post | ✅ Done | Connections card: pick a connected platform, edit the message, post. (X still waits on the blocker above.) |
| Facebook, Instagram, Threads, Bluesky, Mastodon | 🕓 Planned | Can already be planned on the calendar; marked *failed: not connected* when due. |
| Images / media | 🕓 Planned | |
| Always-on hosting | 🚧 Partly | In Docker, posts go out whenever this machine and Docker are on. Remote hosting is planned. |

Legend: ✅ done · ⚠️ blocked · 🚧 in progress · 🕓 planned

---

## Quick start

### 1. Add your keys

```bash
cp .env.example env && chmod 660 env   # the real file is `env`, with no dot; keep it private
```

Fill in your platform keys in `env`. It is gitignored; never commit it.
Without keys the app still runs and the platform shows as *not connected*.
LinkedIn needs one extra sign-in step: see [LinkedIn setup](#linkedin-setup).

### 2a. Run with Docker (recommended)

Needs Docker with Compose v2.24 or newer.

```bash
docker compose up --build -d
```

Open **http://127.0.0.1:8080**. API docs: http://127.0.0.1:8080/docs

| Command | What it does |
|---|---|
| `docker compose ps` | Status and health of both containers |
| `docker compose logs -f backend` | API and scheduler logs |
| `docker compose up --build -d` | Rebuild and restart after a code change |
| `docker compose down` | Stop (your data is kept) |
| `docker compose down -v` | Stop **and delete the database** |

Use another port with `SM_PORT=9000 docker compose up -d`.

### 2b. Run locally with Python (development)

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
#   or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/dev.sh
```

Open **http://127.0.0.1:8000**. In this mode one process serves both the
UI and the API, and reloads on code changes. Local and Docker use separate
databases (`./data/` and the `sm-data` volume).

### Tests and checks

```bash
.venv/bin/python -m pytest                              # 113 tests, never posts anything
.venv/bin/python test_credentials/test_x_credentials.py # check X keys without posting
```

---

## Using it

Three tabs at the top: **Status** (is everything working?), **Analytics** (how did
posts do?) and **Scheduler** (plan and schedule). **+ New post** works from any tab.

- **Status:** the banner says *All systems normal* or lists what needs attention
  (failed posts, scheduler off or stalled, overdue posts). Tiles show the backend,
  scheduler, post queue, connected apps and blog library; *Recent activity* lists
  what happened, newest first.
- **Connectors** (Status tab): one card per platform. The badge says *Connected*,
  *Can’t post yet*, *Problem*, *Needs keys*, *Needs sign-in* or *Planned*; the checklist
  shows which `env` keys are set (never their values) and *Next* says what to do.
  **Check** tests a connected platform live. Planned platforms are listed below.
- **Analytics:** pick a period, platform, source and status in the filter row; every
  number, the chart, the platform table and the post list follow it. Hover the chart,
  or focus it and use `←` / `→`, to read each day; *Show as a table* lists them all.
- **Schedule a post:** click a day → write text (or pick *From my blog*) →
  tick platforms → set the time → **Schedule**.
- **LinkedIn options** (shown when LinkedIn is ticked): a separate, longer LinkedIn text
  when other platforms are ticked too; attach an **image** or a **link preview** card
  (blog posts get the card by default); **who can see it**; **allow reposts**; and a
  **first comment** posted right after the post.
- **Manage a live LinkedIn post:** open it on the calendar, or click it in Analytics' post list →
  **Edit on LinkedIn** changes
  its text, **Delete on LinkedIn** takes it down (both ask first). Its stats show there too.
- **Per-post stats:** LinkedIn analytics → *Export* → Analytics tab → *LinkedIn post stats*
  → **Import analytics file…**. Each post shows its impressions, reactions and comments.
- **Edit or delete:** click a coloured entry on the calendar. Posts that are
  already published are read-only; deleting them only removes them from the
  planner, not from the platform.
- **Plan from the blog:** in *From my blog*, tick posts → **Plan selected** →
  pick the first date, time and spacing. They are scheduled oldest first.
- **Send a test post:** Status tab → *Send a test post* → pick the platform.
  It posts publicly, right away, and is kept in the planner (today on the calendar, and in
  Analytics), so a LinkedIn test post can be edited or deleted later like any other.
- **Failed posts** show a red outline. Open one to see why and press **Retry failed**.
- Keyboard: `←` / `→` move between tabs when a tab has focus, and change month on the
  Scheduler tab; `Enter` on a focused day opens it; `Esc` closes dialogs.

---

## Architecture

```
 Browser                frontend container (nginx)        backend container (FastAPI)
┌───────────┐  :8080   ┌─────────────────────────┐       ┌───────────────────────────────┐
│ status    │ ───────▶ │ /        static UI      │       │ routes/api.py   HTTP layer    │ ──▶ X, LinkedIn APIs
│ analytics │          │ /api/*   ─────────────────────▶ │ services/       logic         │ ──▶ blog sitemap
│ scheduler │          │ /docs    ─────────────────────▶ │ platforms/      adapters      │
└───────────┘          └─────────────────────────┘       │ scheduler       every 30 s    │
                        published on 127.0.0.1 only      │ db.py ──▶ SQLite (sm-data)    │
                                                         └───────────────────────────────┘
                                                          no host port: reachable via nginx only
```

- **The browser only talks to the frontend server.** nginx serves the UI and
  forwards `/api/*`, `/docs`, `/redoc` and `/openapi.json` to FastAPI, so
  everything is one origin: no CORS, and the backend has no public port.
- **FastAPI is the only way to the data and the platforms.** It validates
  every request, stores posts in SQLite, imports the blog and runs the
  background publisher that sends due posts.
- **Only `127.0.0.1` is published.** There is no login yet, so the app must
  not be reachable from the network.
- In local dev (`./scripts/dev.sh`) FastAPI serves the UI itself; the API is identical.

Decisions and their reasons: [docs/decisions/](docs/decisions/)
([0001: overall architecture](docs/decisions/0001-architecture.md),
[0002: Docker and the frontend/backend split](docs/decisions/0002-docker.md),
[0003: LinkedIn sign-in](docs/decisions/0003-linkedin-sign-in.md),
[0004: tabs, activity log, analytics](docs/decisions/0004-tabs-status-analytics.md)).

### Data model

- **posts**: one piece of content: `body`, `scheduled_at` (UTC), `source` (`custom` | `blog`), `blog_url`.
- **post_targets**: one row per platform per post. `status`: `scheduled → publishing → published | failed`. Stores the live post link or the error.
- **blog_posts**: cache of the blog: title, summary, date, tags, image.
- **post_targets** also has `body_override` (that platform's own text), `options` (JSON:
  `visibility`, `allow_reshare`, `attachment` none/image/link_card, `image_id`, `image_alt`,
  `first_comment`), `warning` (went out, but e.g. the first comment didn't), `removed_at`
  (deleted on the platform from the app) and `edited_at`.
- **media**: uploaded images (`id`, `mime`, `size`); files live in `data/media/` (Docker: `/data/media`).
- **post_metrics**: one row per delivery with stats: `source` (`api` | `import`), `impressions`,
  `members_reached`, `reactions`, `comments`, `reshares`, `saves`, `link_clicks`, `clicks`,
  `engagements`. ([ADR 0006](docs/decisions/0006-linkedin-post-management.md))
- **activity**: what happened, as one plain sentence per row (`kind`, `message`, optional
  `platform`, `post_id`, `url`). Written by the services; the newest 1000 are kept.
  Feeds *Recent activity*. ([ADR 0004](docs/decisions/0004-tabs-status-analytics.md))

---

## Project structure

```
social_manager/
├── compose.yaml               runs frontend + backend containers together
├── .env.example               template for the `env` secrets file (copy, then fill in)
├── requirements.txt           local dev: backend runtime deps + test tools
├── pytest.ini
│
├── backend/                   the API: FastAPI + scheduler + SQLite
│   ├── Dockerfile             API-only image (python:3.12-slim, non-root)
│   ├── requirements.txt       runtime dependencies (pinned)
│   ├── app/
│   │   ├── main.py            app factory, error → HTTP mapping, scheduler start/stop
│   │   ├── config.py          settings from environment variables and the env file
│   │   ├── db.py              SQLite schema and connections
│   │   ├── schemas.py         request validation models (Pydantic)
│   │   ├── timeutil.py        UTC helpers
│   │   ├── routes/api.py      HTTP endpoints (thin: parse → call service → return)
│   │   ├── services/
│   │   │   ├── posts.py       create / edit / delete / retry, state changes
│   │   │   ├── blog.py        blog import and auto-planning
│   │   │   ├── activity.py    activity log ("Recent activity")
│   │   │   ├── monitor.py     system snapshot for the Status tab
│   │   │   ├── analytics.py   filtered counts for the Analytics tab
│   │   │   ├── signin.py      "Sign in with …" from the app (OAuth redirect and callback)
│   │   │   ├── live.py        edit / delete live posts, fetch their stats
│   │   │   ├── media.py       uploaded images (files next to the database)
│   │   │   ├── attachments.py image / link card / options for a post as it publishes
│   │   │   ├── stats_import.py LinkedIn analytics file (.xlsx / .csv) → per-post stats
│   │   │   ├── scheduler.py   background publisher (and what it last did)
│   │   │   └── errors.py      NotFound / Conflict / InvalidInput
│   │   └── platforms/
│   │       ├── __init__.py    platform registry (name, colour, character limit)
│   │       ├── base.py        Publisher interface and shared types
│   │       ├── connectors.py  connector cards: setup state per platform (Status tab)
│   │       ├── x.py           X adapter (OAuth 1.0a, POST /2/tweets)
│   │       └── linkedin.py    LinkedIn adapter (OAuth 2.0 token, POST /rest/posts) and sign-in helpers
│   └── tests/                 pytest suite (fake publisher, never posts)
│
├── frontend/                  the UI: static files, no build step
│   ├── Dockerfile             nginx image
│   ├── nginx.conf             serves the UI, forwards /api to the backend
│   ├── index.html
│   ├── css/app.css            design tokens, light/dark themes, responsive rules
│   └── js/
│       ├── main.js            entry point: load data, wire the tabs and top bar
│       ├── tabs.js            Status / Analytics / Scheduler tabs (URL hash, keyboard)
│       ├── status.js          Status tab: health tiles, attention list, recent activity
│       ├── analytics.js       Analytics tab: filters, figures, platform table, post list
│       ├── chart.js           small column chart + its table view (no library)
│       ├── post-options.js    LinkedIn options in the composer (own text, image, card, …)
│       ├── live.js            a post's deliveries: status, stats, Edit / Delete on LinkedIn
│       ├── state.js           shared state and loaders
│       ├── api.js             fetch wrapper for the backend API
│       ├── calendar.js        monthly calendar
│       ├── composer.js        create / edit post dialog
│       ├── blog.js            blog library card and plan dialog
│       ├── connections.js     connector cards and the test post
│       └── util.js            DOM building, dates, text length, toasts, storage
│
├── docs/decisions/            architecture decision records (ADRs)
├── scripts/
│   ├── dev.sh                 run locally (API + UI in one process)
│   └── linkedin_login.py      fallback LinkedIn sign-in from the terminal
└── test_credentials/          standalone X credential checker
```

---

## API reference

Interactive docs (try requests from the browser): **`/docs`** (Swagger) and
**`/redoc`**, at http://127.0.0.1:8080 in Docker or http://127.0.0.1:8000 locally.

### Conventions

- JSON in and out. All paths start with `/api`.
- **Times need a timezone**, e.g. `2026-11-09T09:00:00+02:00` or `...Z`.
  They are stored and returned in UTC (`2026-11-09T07:00:00+00:00`).
- **Errors** always have a `detail`. For app errors it is one plain sentence
  meant to be shown to the user:

  | Status | When | Example `detail` |
  |---|---|---|
  | 404 | Unknown post or platform | `"Post 12 not found"` |
  | 409 | Clashes with the current state | `"Nothing to retry: no platform failed for this post."` |
  | 422 | Invalid input | `"That time is in the past. Pick a future time."` |
  | 422 | Malformed request (FastAPI validation) | a list: `[{"loc": [...], "msg": "...", "type": "..."}]` |
  | 503 | Docker only: backend container is down | `"The backend is not responding. ..."` |

### Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/api/health` | Liveness check | 200 `{"ok": true}` |
| GET | `/api/platforms` | All platforms with colour, limit, and whether connected | 200 `Platform[]` |
| GET | `/api/platforms/{id}/status` | Live credential and posting check (posts nothing) | 200 `AccountStatus` |
| POST | `/api/platforms/{id}/test` | ⚠️ Publish `{text}` **publicly, right now**; kept in the planner | 200 `PublishResult` + `post_id` |
| GET | `/api/posts?start=&end=` | Posts in a time range | 200 `Post[]` |
| GET | `/api/posts/{id}` | One post | 200 `Post` |
| POST | `/api/posts` | Schedule a post | 201 `Post` |
| PUT | `/api/posts/{id}` | Edit a post (only if nothing is published yet) | 200 `Post` |
| DELETE | `/api/posts/{id}` | Remove from the planner | 204 |
| POST | `/api/posts/{id}/retry` | Re-queue the platforms that failed | 200 `Post` |
| PUT | `/api/posts/{id}/published/{platform}` | ⚠️ Change the text of the **live** post `{text}` | 200 `Post` |
| DELETE | `/api/posts/{id}/published/{platform}` | ⚠️ Delete the post **on the platform** (planner keeps a record) | 200 `Post` |
| POST | `/api/media` | Upload an image (raw body, JPEG/PNG/GIF, ≤ 8 MB) | 201 `{id, mime, size, url}` |
| GET | `/api/media/{id}` | The uploaded image | 200 image |
| GET | `/api/stats` | Per platform: are automatic stats on, and if not why | 200 |
| POST | `/api/stats/refresh` | Fetch stats for live posts (only when automatic stats are on) | 200 `{updated, problems}` |
| POST | `/api/stats/import` | Import LinkedIn's analytics export (raw body, .xlsx/.csv) | 200 summary |
| GET | `/api/connectors` | Setup state of every platform: key names only, never values (no platform calls) | 200 `Connector[]` |
| GET | `/api/connect/{id}` | Browser: start “Sign in with …” (redirects to the platform) | 303 |
| GET | `/api/connect/{id}/callback` | Browser: the platform returns here; saves the token, back to `/#status` | 303 |
| GET | `/api/system` | Backend, scheduler, queue and blog health (no platform calls) | 200 `System` |
| GET | `/api/activity?limit=` | Recent activity, newest first (`limit` 1–100, default 20) | 200 `Activity[]` |
| GET | `/api/analytics?start=&end=&platform=&source=&status=` | Counts and posts matching the filters | 200 `Analytics` |
| GET | `/api/blog/posts` | Cached blog library, newest first | 200 `BlogPost[]` |
| POST | `/api/blog/sync` | Re-import from the blog | 200 `BlogPost[]` |
| POST | `/api/blog/plan` | Schedule several blog posts at a fixed spacing | 201 `Post[]` |

#### Platforms

**`GET /api/platforms`** returns every platform the app knows. `has_adapter`
is `true` when the platform can post (its keys are set). Others can still be
planned; they fail with *not connected* when due.

```json
[{"id": "x", "name": "X (Twitter)", "color": "#1d9bf0", "text_color": "#ffffff",
  "char_limit": 280, "url_weight": 23, "has_adapter": true}]
```

`url_weight`: platforms that shorten links count each URL as this many characters (`null` = counted as typed).

**`GET /api/platforms/{id}/status`** asks the platform whether the keys work.
Nothing is posted. 404 unknown platform; 422 platform not connected.

```json
{"connected": true, "can_post": false, "account": "@Rogue0015",
 "message": "Signed in, but X blocks posting: ..."}
```

**`POST /api/platforms/{id}/test`** publishes immediately and publicly.
Body `{"text": "Hello"}`. 422 if the text is too long, the platform is not
connected, or the platform rejects it.

```json
{"external_id": "1854...", "external_url": "https://x.com/Rogue0015/status/1854..."}
```

#### Scheduled posts

**`GET /api/posts`**: `start` (inclusive) and `end` (exclusive) are optional
timezone-aware datetimes. Without them, all posts are returned, oldest first.

**`POST /api/posts`** schedules one post on one or more platforms:

```json
{
  "body": "New blog post is up!",
  "scheduled_at": "2026-11-09T09:00:00+02:00",
  "platforms": ["x", "linkedin"],
  "blog_url": null
}
```

| Field | Rules |
|---|---|
| `body` | Required, not blank, within every chosen platform's character limit |
| `scheduled_at` | Required, with timezone, not in the past (2 minutes of grace) |
| `platforms` | At least one known platform id; duplicates are ignored |
| `blog_url` | Optional; marks the post as coming from the blog (`source: "blog"`) |
| `variants` | Optional `{platform: text}`: that platform's own text, checked against its own limit |
| `options` | Optional `{platform: {visibility, allow_reshare, attachment, image_id, image_alt, first_comment}}`; 422 if the platform can't do it (see `features` in `/api/platforms`) |

Each target in a `Post` also carries `text` (what that platform gets), `options`, `warning`,
`removed_at`, `edited_at` and `metrics` (`null`, or the stats with their `source` and `updated_at`).

Returns the created `Post`:

```json
{
  "id": 1, "body": "New blog post is up!", "source": "custom", "blog_url": null,
  "scheduled_at": "2026-11-09T07:00:00+00:00",
  "created_at": "2026-11-07T10:31:06+00:00", "updated_at": "2026-11-07T10:31:06+00:00",
  "targets": [
    {"id": 1, "post_id": 1, "platform": "x", "status": "scheduled",
     "external_id": null, "external_url": null, "error": null, "published_at": null}
  ]
}
```

Each target goes `scheduled → publishing → published | failed` on its own.
A published target has `external_url` (link to the live post); a failed one
has `error`.

**`PUT /api/posts/{id}`** takes the same body and rules. Saving resets every
target to `scheduled`. 409 if any platform has already published or is publishing.

**`DELETE /api/posts/{id}`** removes it from the planner only; anything already
posted stays on the platform. 409 while it is being published.

**`POST /api/posts/{id}/retry`** moves `failed` targets back to `scheduled`.
If the time has passed they go out on the next scheduler check (≤ 30 s).
409 if nothing failed.

#### Status

**`GET /api/connectors`** returns one entry per platform, in registry order. It reads
which `env` keys are present, never their values, and calls no platform; the live
check is `/api/platforms/{id}/status`. `state` is `connected`, `needs_keys`,
`needs_sign_in` or `planned`; `next_step` is a sentence to show the user.
`sign_in_url` is set when the platform signs in through the browser and its app keys are
in place; `redirect_uri` is the address to register with the platform.

```json
[{"platform": "linkedin", "state": "needs_sign_in", "built": true,
  "auth": "Browser sign-in (OAuth 2.0); lasts 60 days",
  "portal_url": "https://www.linkedin.com/developers/apps",
  "checklist": [{"label": "Client ID", "key": "LINKEDIN_CLIENT_ID", "done": true},
                {"label": "Client secret", "key": "LINKEDIN_CLIENT_SECRET", "done": true},
                {"label": "Signed in", "key": "LINKEDIN_ACCESS_TOKEN", "done": false}],
  "next_step": "Run .venv/bin/python scripts/linkedin_login.py, press Allow, then restart the app.",
  "note": "The app needs the “Share on LinkedIn” and “Sign In with LinkedIn using OpenID Connect” products.",
  "expires_at": null}]
```

**`GET /api/system`** reads only local state, so it is cheap to poll. `attention`
lists problems as sentences to show the user; empty means all is well.

```json
{"started_at": "2026-11-07T13:53:57+00:00", "uptime_seconds": 5400,
 "scheduler": {"enabled": true, "interval_seconds": 30, "last_run_at": "2026-11-07T15:23:40+00:00",
               "last_attempted": 0, "last_error": null},
 "queue": {"scheduled": 6, "publishing": 0, "published": 26, "failed": 1,
           "next_due_at": "2026-11-08T09:00:00+00:00"},
 "blog": {"posts": 19, "last_synced_at": "2026-11-07T10:53:56+00:00"},
 "platforms": {"known": 7, "connected": 2}, "database": {"size_bytes": 57344},
 "attention": ["1 post failed to publish. Open them on the Scheduler tab to retry."]}
```

**`GET /api/activity`** returns the newest events first. `kind` is one of `started`,
`scheduled`, `edited`, `deleted`, `retried`, `published`, `failed`, `interrupted`,
`test_post`, `signed_in`, `blog_synced`, `blog_planned`.

**`GET /api/connect/{id}`** and **`/api/connect/{id}/callback`** are opened by the
browser, not called with `fetch`. Every outcome is a redirect: to the platform, or back to
`/?signin_ok=…#status` / `/?signin_error=…#status` with a sentence the UI shows. The
callback accepts a sign-in only once, within 10 minutes of starting it.

```json
[{"id": 41, "at": "2026-11-07T15:20:11+00:00", "kind": "published", "platform": "linkedin",
  "post_id": 12, "message": "Published “Five lessons from shipping a side project” on LinkedIn.",
  "url": "https://www.linkedin.com/feed/update/urn:li:share:7.../"}]
```

#### Analytics

**`GET /api/analytics`**: every filter is optional. Each delivery to one platform is
counted once and dated by when it was published, or by its scheduled time if it
hasn't been. `start` (inclusive) and `end` (exclusive) need a timezone; `platform`
is a platform id; `source` is `custom` or `blog`; `status` is `scheduled`,
`publishing`, `published` or `failed`. 422 for an unknown platform or `start ≥ end`.

```json
{"totals": {"scheduled": 0, "publishing": 0, "published": 26, "failed": 6,
            "success_rate": 0.8125, "posts": 26},
 "by_platform": [{"platform": "x", "scheduled": 0, "publishing": 0, "published": 13,
                  "failed": 3, "success_rate": 0.8125}],
 "failure_reasons": [{"error": "HTTP 429: Too many requests. Try again later.", "count": 4}],
 "posts": [{"id": 12, "body": "...", "source": "custom", "blog_url": null,
            "scheduled_at": "...", "targets": [{"platform": "x", "status": "published",
            "external_url": "...", "error": null, "published_at": "...", "happened_at": "..."}]}]}
```

`success_rate` is published ÷ (published + failed), or `null` when nothing has
finished. Posts are newest first. Likes and views are not included (see *Project status*).

#### Blog

**`GET /api/blog/posts`** returns the cached library. `scheduled_count` is how
many planner posts already use that blog post.

```json
[{"url": "https://rogue0015.github.io/blogs/icarus-testing-browser-limits.html",
  "title": "Icarus: Testing the Limits of What a Browser Can Do",
  "description": "How I pushed the browser to run my offline tools, ...",
  "published_on": "2026-11-06", "tags": ["ai", "developers"],
  "image": "https://rogue0015.github.io/assets/brandon-bande.jpg",
  "fetched_at": "2026-11-07T10:31:12+00:00", "scheduled_count": 0}]
```

**`POST /api/blog/sync`** re-reads the blog's `sitemap.xml` and each post's
meta tags, then returns the updated library. 422 if the blog can't be reached.
Can take several seconds.

**`POST /api/blog/plan`** creates one post per URL: the first at `start`, then
one every `every_days` days. The text (title, summary, link) is trimmed to fit
the strictest chosen platform. Everything is validated first, so a bad input
never leaves a half-made plan.

```json
{
  "urls": ["https://rogue0015.github.io/blogs/icarus-testing-browser-limits.html"],
  "start": "2026-11-10T09:00:00+02:00",
  "every_days": 2,
  "platforms": ["x"]
}
```

`every_days` is 1–60. 422 if a URL isn't in the library (sync first) or
`start` is in the past. Returns the created `Post[]`.

---

## Configuration

### Secrets

Platform keys live in a file named `env` at the project root (template:
[.env.example](.env.example)). Non-empty environment variables with the same
names take priority. Docker mounts the file into the backend container
(`/config/env`) so a sign-in can save its token there; it is never copied into
an image. Keep it at mode 660: the backend joins its group to write it.

| Variable | Needed for |
|---|---|
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` | Posting to X (OAuth 1.0a) |
| `X_BEARER_TOKEN`, `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_APP_ID` | Only the credential checker in `test_credentials/` |
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | The LinkedIn sign-in script |
| `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_TOKEN_EXPIRES_AT` | Posting to LinkedIn. Written by the sign-in script; leave empty |
| `LINKEDIN_REDIRECT_URI` | Optional. Sign-in redirect for the fallback script, if port 8765 is taken |
| `META_APP_ID`, `META_APP_SECRET` | Not used yet: for the planned Facebook and Instagram adapters |
| `THREADS_APP_ID`, `THREADS_APP_SECRET` | Not used yet: for the planned Threads adapter |
| `LINKEDIN_SCOPES` | Optional. Permissions to ask for at sign-in (default `openid profile w_member_social`); add `r_member_postAnalytics` only once LinkedIn approved it |
| `LINKEDIN_GRANTED_SCOPES` | Written by the sign-in: what LinkedIn granted. Automatic stats turn on when it includes `r_member_postAnalytics` |

### Settings

| Variable | Default | Purpose |
|---|---|---|
| `SM_ENV_FILE` | `./env` | File holding platform keys |
| `SM_DB_PATH` | `./data/social_manager.db` (Docker: `/data/social_manager.db`) | SQLite file |
| `SM_BLOG_BASE_URL` | `https://rogue0015.github.io` | Blog to import from |
| `SM_SCHEDULER` | `on` | `off` disables background publishing |
| `SM_SCHEDULER_INTERVAL` | `30` | Seconds between checks |
| `SM_SERVE_FRONTEND` | `on` (Docker backend: `off`) | Whether FastAPI also serves the UI |
| `SM_PUBLIC_URL` | `http://localhost:$PORT` (Docker: `http://localhost:$SM_PORT`) | Where the browser reaches the app; sign-in redirects return here |
| `SM_ENV_GID` | `1000` | Docker: group id of the `env` file, so the backend can save sign-in tokens (`stat -c %g env`) |
| `PORT` | `8000` | Port for `scripts/dev.sh` |
| `SM_PORT` | `8080` | Host port for the Docker UI (read by `compose.yaml`) |

### Docker data: backup and restore

The database lives in the `sm-data` Docker volume.

```bash
# Backup
docker compose cp backend:/data/social_manager.db ./backup.db

# Restore (or move a local ./data/social_manager.db into Docker)
docker compose cp ./backup.db backend:/data/social_manager.db
docker compose exec -u root backend chown app:app /data/social_manager.db
docker compose restart backend
```

Don't run the local and Docker versions against copies of the same database
at the same time: each would publish the same due posts.

---

## X API access

As of 2026-11-07:

- All credentials are valid and the account is **@Rogue0015** (read + write + DM permissions).
- **Blocker:** X rejects every v2 request, including posting, with
  *"you must use keys and tokens from a developer App that is attached to a Project"*.
  **Fix:** developer.x.com → Projects & Apps → move the app from
  *Standalone Apps* into a Project. Then press **Check** in the Connections card;
  the dot turns green.
- The current access level can **publish** but can't **read** much back
  (no timelines, mentions or analytics).
- X uses pay-per-use pricing (reported ~$0.015 per post, more with links).
  Check your balance in the developer portal.

## LinkedIn setup

Once per LinkedIn app, at [linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) → your app:

1. **Products**: add **Share on LinkedIn** (posting) and **Sign In with LinkedIn using
   OpenID Connect** (tells the app whose feed to post to).
2. **Auth** → *Authorized redirect URLs for your app*: add the URL shown on the LinkedIn
   card on the Status tab:
   - Docker: `http://localhost:8080/api/connect/linkedin/callback`
   - Local dev: `http://localhost:8000/api/connect/linkedin/callback`
3. Put the **Client ID** and **Primary Client Secret** from the Auth tab in `env` as
   `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`, then restart the backend
   (`docker compose up -d --force-recreate backend`, or restart `./scripts/dev.sh`).

Then, and again every 60 days:

4. **Status** tab → LinkedIn card → **Sign in with LinkedIn** → press **Allow** on LinkedIn.
   You come back to the Status tab with *LinkedIn connected as …*. The token is saved to
   `env` and posting works straight away, without a restart.

Good to know:

- The card shows the days left and warns in the last week. Once the sign-in has expired,
  LinkedIn posts fail with a "sign in again" message and can be retried after signing in.
- The app opens on `http://localhost:…` after signing in, because that is the address LinkedIn
  returns to. Override it with `SM_PUBLIC_URL` and register the matching redirect URL.
- Fallback without the app (e.g. the backend isn't reachable from your browser):
  `.venv/bin/python scripts/linkedin_login.py`, with redirect URL
  `http://localhost:8765/linkedin/callback` registered instead.
- Limits: 3000 characters per post, 150 posts per day. Links are posted as text;
  LinkedIn may not show a preview card for them.
- The adapter pins Posts API version `202610` (`API_VERSION` in
  `backend/app/platforms/linkedin.py`). LinkedIn retires versions after about a
  year; posts failing with HTTP 426 mean it needs moving to a newer month.

Decision records: [0003](docs/decisions/0003-linkedin-sign-in.md),
[0005](docs/decisions/0005-sign-in-from-the-app.md).

## Adding a platform

1. Add a `PlatformInfo` (id, name, colour, character limit) to
   `backend/app/platforms/__init__.py`. It immediately appears on the calendar.
2. Write a `Publisher` subclass with `publish(text)` and `check()`
   (see `platforms/x.py`) and register it in `build_publishers()`. Give the module a
   `CONNECTOR` (how it signs in, which keys it needs, its portal) and use it in
   `platforms/connectors.py` in place of the *planned* entry, so its Status card shows setup progress.
3. Add its keys to `.env.example` and `env`, and add their prefix to
   `SECRET_PREFIXES` in `config.py` (Docker passes keys as environment variables).
4. Add tests with a fake, and record any notable choice (auth flow, API limits)
   as a new ADR in `docs/decisions/`.
5. Update the status table above.

## Contributing conventions

- Every architectural or behavioural decision gets a short ADR in
  `docs/decisions/` (copy `TEMPLATE.md`, number sequentially).
- Update the **Project status** table and **Changelog** in the same change as the feature.
- New backend behaviour comes with tests; tests never call real platform APIs.
- Runtime dependencies go in `backend/requirements.txt` (pinned); test tools in `requirements.txt`.
- Secrets live only in `env`. Never commit them, log them or return them from the API.

## Changelog

### 2026-11-07: Test posts can be managed; Meta and Threads keys
- Test posts are now kept in the planner, so LinkedIn test posts can be edited or deleted
  later (LinkedIn offers no way to list your posts afterwards). Your earlier LinkedIn test
  post was added from the activity log.
- Analytics: click a post to open it (Edit / Delete on LinkedIn are there). On live posts the
  dialog's planner-only button now says *Remove from planner*.
- `META_*` and `THREADS_*` keys are read from `env` (adapters still planned). 116 backend tests.

### 2026-11-07: Manage LinkedIn posts, per-post stats
- LinkedIn options when scheduling: own text, image or link preview card, who can see it,
  reposts on/off, first comment. Blog posts get a card and the full summary on LinkedIn.
- **Edit on LinkedIn** / **Delete on LinkedIn** for live posts; their state, warnings and
  stats show in the post dialog. Deleted posts are crossed out on the calendar.
- Analytics: *LinkedIn post stats* card. Import LinkedIn's analytics file; totals as tiles
  and numbers on each post. Automatic stats are built but off until LinkedIn approves.
- New tables `media`, `post_metrics`; new `post_targets` columns (added on start-up).
  ADR [0006](docs/decisions/0006-linkedin-post-management.md). 113 backend tests.

### 2026-11-07: Sign in with LinkedIn from the app
- **Sign in with LinkedIn** on the Status tab's LinkedIn card: LinkedIn's Allow page, then
  back to the Status tab. The token is saved to `env` and posting works without a restart.
  The card shows the exact redirect URL to register. ADR [0005](docs/decisions/0005-sign-in-from-the-app.md).
- New `GET /api/connect/{id}` and `/callback` (single-use `state`, 10 minutes).
- Docker: `env` is mounted into the backend (group `SM_ENV_GID`), not passed as
  variables; `env` should be mode 660. New setting `SM_PUBLIC_URL`.
- Blank environment variables no longer override values in `env`.
- 76 backend tests.

### 2026-11-07: Connectors on the Status tab
- One card per platform (X, LinkedIn, Facebook, Instagram, Threads, Bluesky, Mastodon):
  status badge, how it signs in, a checklist of the `env` keys it needs (names only),
  the next step, live **Check**, and a link to the developer portal. Built platforms
  come first; planned ones are listed below with what connecting them will need.
- New `GET /api/connectors` (`platforms/connectors.py`); never returns key values.
- The test post moved to its own card. 68 backend tests.

### 2026-11-07: Status, Analytics and Scheduler tabs
- The page is now three tabs. **Status**: connected apps, system monitor tiles,
  a problems banner, recent activity (refreshes every 30 s). **Analytics**: filters
  for period, platform, source and status; published, failed and success rate; a
  per day/week chart with hover, keyboard read-out and table view; per-platform
  table; failure reasons; post list. **Scheduler**: the calendar and blog planner.
- New `activity` table and `GET /api/activity`; `GET /api/system`; `GET /api/analytics`.
  ADR [0004](docs/decisions/0004-tabs-status-analytics.md).
- The scheduler records its last check, so a stalled scheduler shows on the Status tab.
- 61 backend tests.

### 2026-11-07: LinkedIn
- LinkedIn adapter: posts to your own feed with the Posts API (`POST /rest/posts`, version 202610).
  Text is escaped for LinkedIn's format; hashtags are kept.
- `scripts/linkedin_login.py`: browser sign-in that saves a 60-day token to `env`.
  ADR [0003](docs/decisions/0003-linkedin-sign-in.md).
- Connections card shows the LinkedIn account and days left on the sign-in.
  The test post can go to any connected platform.
- Long error messages wrap instead of widening the page; toasts use the full phone width.
- `config.py` reads platform keys from the environment by prefix (`X_`, `LINKEDIN_`).
- 47 backend tests.

### 2026-11-07: Docker, separate frontend and backend
- `compose.yaml` runs two containers: nginx (UI, forwards `/api` and `/docs`) and FastAPI (API, scheduler, SQLite).
- Backend is API-only in Docker (`SM_SERVE_FRONTEND=off`) and has no host port; the UI is on `127.0.0.1:8080`.
- Keys are loaded from `env` at runtime, never baked into images. `.env.example` added as a template.
- Database in a named volume; plain-language 503 when the backend is down.
- Runtime dependencies moved to `backend/requirements.txt`.
- README: full project structure and API reference. ADR [0002](docs/decisions/0002-docker.md).
- 25 backend tests.

### 2026-11-07: first version
- Monthly calendar with platform colour coding, compose/edit/delete/retry.
- Blog import from sitemap + Open Graph tags; auto-plan with spacing and preview.
- Background publisher with at-most-once sending and interrupted-send recovery.
- X adapter (OAuth 1.0a, `POST /2/tweets`), live status check, editable test post.
- Responsive layout (phone to desktop), light/dark themes, keyboard support.
- 22 backend tests.
