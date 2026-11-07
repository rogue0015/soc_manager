# Social Manager

A personal portal for managing my digital footprint: plan, schedule and
publish posts across social platforms from one monthly calendar, and turn
my blog ([rogue0015.github.io/blogs](https://rogue0015.github.io/blogs/))
into a posting schedule.

Runs locally. Python (FastAPI) backend, plain HTML/CSS/JS frontend, SQLite storage.

---

## Project status

_Last updated: 2026-11-07_

| Area | Status | Notes |
|---|---|---|
| Monthly content calendar | ✅ Done | Click a day to schedule; click an entry to edit. Colour-coded by platform. |
| Custom posts | ✅ Done | Write your own text, pick platforms, date and time. |
| Blog import | ✅ Done | Reads the blog's sitemap and post metadata. 19 posts imported on first run. |
| Blog auto-plan | ✅ Done | Select posts, choose a start date and spacing; one post per slot is created. |
| Background publisher | ✅ Done | Sends due posts every 30 s while the server runs. Failed posts can be retried. |
| X (Twitter) connection | ⚠️ Blocked by X | Credentials valid (@Rogue0015). Posting is blocked until the X developer app is attached to a Project ([details](#x-api-access)). |
| X test post | ✅ Built, waiting on X | Editable message in the Connections card. Will work once the blocker above is fixed. |
| LinkedIn, Facebook, Instagram, Threads, Bluesky, Mastodon | 🕓 Planned | Can already be planned on the calendar; marked *failed: not connected* when due. |
| Images / media | 🕓 Planned | |
| Always-on hosting | 🕓 Planned | Posts currently go out only while the app is running. |

Legend: ✅ done · ⚠️ blocked · 🚧 in progress · 🕓 planned

---

## Quick start

```bash
# 1. Create the virtual environment (Python 3.12+)
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
#    or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Put credentials in a file named `env` at the project root (never committed)
#    X_API_KEY=...  X_API_SECRET=...  X_ACCESS_TOKEN=...  X_ACCESS_TOKEN_SECRET=...

# 3. Run
./scripts/dev.sh
#    open http://127.0.0.1:8000
```

Run the tests: `.venv/bin/python -m pytest`

Check the X credentials without posting: `.venv/bin/python test_credentials/test_x_credentials.py`

## Using it

- **Schedule a post:** click a day → write text (or pick *From my blog*) →
  tick platforms → set the time → **Schedule**.
- **Edit or delete:** click a coloured entry on the calendar. Posts that are
  already published are read-only; deleting them only removes them from the
  planner, not from the platform.
- **Plan from the blog:** in *From my blog*, tick posts → **Plan selected** →
  pick the first date, time and spacing. They are scheduled oldest first.
- **Send a test post to X:** *Connections* → *Send a test post to X*. The
  message is editable and remembered in your browser. It posts publicly, right away.
- **Failed posts** show a red outline. Open one to see why and press **Retry failed**.
- Keyboard: `←` / `→` change month; `Enter` on a focused day opens it; `Esc` closes dialogs.

---

## Architecture

```
Browser (frontend/)                      Python backend (backend/app/)
┌─────────────────────────┐   JSON    ┌───────────────────────────────────────┐
│ calendar · composer     │ ───────▶ │ routes/api.py   thin HTTP layer        │
│ connections · blog      │  /api/*   │ services/       posts, blog, scheduler │
└─────────────────────────┘           │ platforms/      registry + adapters    │
                                      │ db.py           SQLite                 │
                                      └──────────────┬────────────────────────┘
                                                     │ every 30 s
                                       scheduler ──▶ platform adapter ──▶ X API
```

Key decisions and their reasons are recorded in [docs/decisions/](docs/decisions/)
([0001: overall architecture](docs/decisions/0001-architecture.md)).

### Data model

- **posts**: one piece of content: `body`, `scheduled_at` (UTC), `source` (`custom` | `blog`), `blog_url`.
- **post_targets**: one row per platform per post. `status`: `scheduled → publishing → published | failed`. Stores the live post link or the error.
- **blog_posts**: cache of the blog: title, summary, date, tags, image.

### Project layout

```
backend/app/
  main.py            app factory, error handling, scheduler start/stop
  config.py          settings (env vars, env file)
  db.py              SQLite schema and connections
  schemas.py         request validation models
  timeutil.py        UTC helpers
  routes/api.py      HTTP endpoints
  services/          posts.py, blog.py, scheduler.py, errors.py
  platforms/         __init__.py (registry), base.py, x.py
backend/tests/       pytest suite (uses a fake publisher, never posts)
frontend/
  index.html
  css/app.css        design tokens, light/dark, responsive rules
  js/                main, state, api, calendar, composer, blog, connections, util
docs/decisions/      architecture decision records (ADRs)
scripts/dev.sh       run the app locally
test_credentials/    standalone X credential checker
```

### API

Interactive docs at `http://127.0.0.1:8000/docs` while running.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/platforms` | Platforms with colour, character limit, and whether connected |
| GET | `/api/platforms/{id}/status` | Live credential and posting check (posts nothing) |
| POST | `/api/platforms/{id}/test` | Publish `{text}` immediately |
| GET | `/api/posts?start=&end=` | Posts in a time range |
| POST | `/api/posts` | Create `{body, scheduled_at, platforms, blog_url?}` |
| PUT | `/api/posts/{id}` | Edit (only if nothing is published yet) |
| DELETE | `/api/posts/{id}` | Remove from the planner |
| POST | `/api/posts/{id}/retry` | Re-queue failed platforms |
| GET | `/api/blog/posts` | Cached blog library |
| POST | `/api/blog/sync` | Re-import from the blog |
| POST | `/api/blog/plan` | Schedule `{urls, start, every_days, platforms}` |

`scheduled_at` / `start` must include a timezone (e.g. `2026-11-09T09:00:00+02:00`).

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SM_ENV_FILE` | `./env` | File holding platform credentials |
| `SM_DB_PATH` | `./data/social_manager.db` | SQLite file |
| `SM_BLOG_BASE_URL` | `https://rogue0015.github.io` | Blog to import from |
| `SM_SCHEDULER` | `on` | `off` disables background publishing |
| `SM_SCHEDULER_INTERVAL` | `30` | Seconds between checks |
| `PORT` | `8000` | Used by `scripts/dev.sh` |

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

## Adding a platform

1. Add a `PlatformInfo` (id, name, colour, character limit) to
   `backend/app/platforms/__init__.py`. It immediately appears on the calendar.
2. Write a `Publisher` subclass with `publish(text)` and `check()`
   (see `platforms/x.py`) and register it in `build_publishers()`.
3. Add its credentials to `env`, add tests with a fake, and record any
   notable choice (auth flow, API limits) as a new ADR in `docs/decisions/`.
4. Update the status table above.

## Contributing conventions

- Every architectural or behavioural decision gets a short ADR in
  `docs/decisions/` (copy `TEMPLATE.md`, number sequentially).
- Update the **Project status** table and **Changelog** in the same change as the feature.
- New backend behaviour comes with tests; tests never call real platform APIs.
- Secrets live only in `env`. Never commit them, log them or return them from the API.

## Changelog

### 2026-11-07: first version
- Monthly calendar with platform colour coding, compose/edit/delete/retry.
- Blog import from sitemap + Open Graph tags; auto-plan with spacing and preview.
- Background publisher with at-most-once sending and interrupted-send recovery.
- X adapter (OAuth 1.0a, `POST /2/tweets`), live status check, editable test post.
- Responsive layout (phone to desktop), light/dark themes, keyboard support.
- 22 backend tests.
