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
| Monthly content calendar | ✅ Done | Click a day to schedule; click an entry to edit. Colour-coded by platform. |
| Custom posts | ✅ Done | Write your own text, pick platforms, date and time. |
| Blog import | ✅ Done | Reads the blog's sitemap and post metadata (19 posts). |
| Blog auto-plan | ✅ Done | Select posts, choose a start date and spacing; one post per slot is created. |
| Background publisher | ✅ Done | Sends due posts every 30 s while the backend runs. Failed posts can be retried. |
| Docker | ✅ Done | Separate frontend (nginx) and backend (FastAPI) containers, one command to start. |
| X (Twitter) connection | ⚠️ Blocked by X | Credentials valid (@Rogue0015). Posting is blocked until the X developer app is attached to a Project ([details](#x-api-access)). |
| X test post | ✅ Built, waiting on X | Editable message in the Connections card. Will work once the blocker above is fixed. |
| LinkedIn, Facebook, Instagram, Threads, Bluesky, Mastodon | 🕓 Planned | Can already be planned on the calendar; marked *failed: not connected* when due. |
| Images / media | 🕓 Planned | |
| Always-on hosting | 🚧 Partly | In Docker, posts go out whenever this machine and Docker are on. Remote hosting is planned. |

Legend: ✅ done · ⚠️ blocked · 🚧 in progress · 🕓 planned

---

## Quick start

### 1. Add your keys

```bash
cp .env.example env      # note: the real file is `env`, with no dot
```

Fill in the X keys in `env`. It is gitignored; never commit it.
Without keys the app still runs and X shows as *not connected*.

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
.venv/bin/python -m pytest                              # 25 tests, never posts anything
.venv/bin/python test_credentials/test_x_credentials.py # check X keys without posting
```

---

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
 Browser                frontend container (nginx)        backend container (FastAPI)
┌───────────┐  :8080   ┌─────────────────────────┐       ┌───────────────────────────────┐
│ calendar  │ ───────▶ │ /        static UI      │       │ routes/api.py   HTTP layer    │ ──▶ X API
│ composer  │          │ /api/*   ─────────────────────▶ │ services/       logic         │ ──▶ blog sitemap
│ blog      │          │ /docs    ─────────────────────▶ │ platforms/      adapters      │
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
[0002: Docker and the frontend/backend split](docs/decisions/0002-docker.md)).

### Data model

- **posts**: one piece of content: `body`, `scheduled_at` (UTC), `source` (`custom` | `blog`), `blog_url`.
- **post_targets**: one row per platform per post. `status`: `scheduled → publishing → published | failed`. Stores the live post link or the error.
- **blog_posts**: cache of the blog: title, summary, date, tags, image.

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
│   │   │   ├── scheduler.py   background publisher
│   │   │   └── errors.py      NotFound / Conflict / InvalidInput
│   │   └── platforms/
│   │       ├── __init__.py    platform registry (name, colour, character limit)
│   │       ├── base.py        Publisher interface and shared types
│   │       └── x.py           X adapter (OAuth 1.0a, POST /2/tweets)
│   └── tests/                 pytest suite (fake publisher, never posts)
│
├── frontend/                  the UI: static files, no build step
│   ├── Dockerfile             nginx image
│   ├── nginx.conf             serves the UI, forwards /api to the backend
│   ├── index.html
│   ├── css/app.css            design tokens, light/dark themes, responsive rules
│   └── js/
│       ├── main.js            entry point: load data, wire the top bar, render
│       ├── state.js           shared state and loaders
│       ├── api.js             fetch wrapper for the backend API
│       ├── calendar.js        monthly calendar
│       ├── composer.js        create / edit post dialog
│       ├── blog.js            blog library card and plan dialog
│       ├── connections.js     platform status and X test post
│       └── util.js            DOM building, dates, text length, toasts, storage
│
├── docs/decisions/            architecture decision records (ADRs)
├── scripts/dev.sh             run locally (API + UI in one process)
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
| POST | `/api/platforms/{id}/test` | ⚠️ Publish `{text}` **publicly, right now** | 200 `PublishResult` |
| GET | `/api/posts?start=&end=` | Posts in a time range | 200 `Post[]` |
| POST | `/api/posts` | Schedule a post | 201 `Post` |
| PUT | `/api/posts/{id}` | Edit a post (only if nothing is published yet) | 200 `Post` |
| DELETE | `/api/posts/{id}` | Remove from the planner | 204 |
| POST | `/api/posts/{id}/retry` | Re-queue the platforms that failed | 200 `Post` |
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
[.env.example](.env.example)). Real environment variables with the same
names take priority. Docker loads the file into the backend container at
start-up; it is never copied into an image.

| Variable | Needed for |
|---|---|
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` | Posting to X (OAuth 1.0a) |
| `X_BEARER_TOKEN`, `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_APP_ID` | Only the credential checker in `test_credentials/` |

### Settings

| Variable | Default | Purpose |
|---|---|---|
| `SM_ENV_FILE` | `./env` | File holding platform keys |
| `SM_DB_PATH` | `./data/social_manager.db` (Docker: `/data/social_manager.db`) | SQLite file |
| `SM_BLOG_BASE_URL` | `https://rogue0015.github.io` | Blog to import from |
| `SM_SCHEDULER` | `on` | `off` disables background publishing |
| `SM_SCHEDULER_INTERVAL` | `30` | Seconds between checks |
| `SM_SERVE_FRONTEND` | `on` (Docker backend: `off`) | Whether FastAPI also serves the UI |
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

## Adding a platform

1. Add a `PlatformInfo` (id, name, colour, character limit) to
   `backend/app/platforms/__init__.py`. It immediately appears on the calendar.
2. Write a `Publisher` subclass with `publish(text)` and `check()`
   (see `platforms/x.py`) and register it in `build_publishers()`.
3. Add its keys to `.env.example` and `env`. If they don't start with `X_`,
   also let `config.py` read them from the environment (Docker passes keys that way).
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
