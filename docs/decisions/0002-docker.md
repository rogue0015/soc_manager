# 0002: Run in Docker as separate frontend and backend containers

- **Status:** Accepted
- **Date:** 2026-11-07
- **Amends:** [0001](0001-architecture.md) ("the backend serves the files directly" now applies to local dev only)

## Context

The app should run the same way on any machine with one command, and the
frontend and backend should be separate pieces that can be built, replaced
and reasoned about on their own. Constraints carried over from 0001:

- The frontend has no build step and calls the API with relative URLs (`/api/...`).
- The scheduler runs inside the API process, so there must be exactly one API process.
- There is no login, so nothing may be reachable from the network.
- Secrets live only in the gitignored `env` file.

## Options considered

1. **One container, FastAPI serves the UI too** (what `./scripts/dev.sh` does):
   simplest, but the frontend and backend stay coupled in one image.
2. **Two containers; nginx serves the UI and proxies `/api` to FastAPI:**
   frontend and backend are separate images, the browser sees one origin, so
   no CORS and no frontend change. Adds nginx config to maintain.
3. **Two containers; the browser calls the backend on its own port:** needs
   CORS, an API base URL in the frontend, and a second published port.

## Decision

Option 2. `frontend/` builds an nginx image that serves the static files and
forwards `/api/*`, `/docs`, `/redoc` and `/openapi.json` to the `backend`
service. FastAPI is the only bridge to the database, the blog and the social
platforms; the browser never talks to them directly. `compose.yaml` wires the
two together. `./scripts/dev.sh` keeps working as before; the backend image
sets `SM_SERVE_FRONTEND=off` so it serves the API only.

## Consequences

- **Network exposure:** only the frontend is published, on `127.0.0.1:8080`.
  The backend has no host port; uvicorn binds `0.0.0.0` inside its container
  so nginx can reach it on the compose network. Adding auth is still required
  before publishing on any other interface.
- **Secrets** are passed at runtime through `env_file: ./env` and are never
  copied into an image (`.dockerignore` excludes them). They appear as
  environment variables inside the backend container.
- **Data** lives in the `sm-data` named volume (`/data/social_manager.db`).
  `docker compose down` keeps it; `down -v` deletes it.
- **One uvicorn worker.** More workers would start more schedulers. The
  atomic claim would still prevent double posting, but there is no reason to.
- **`restart: unless-stopped`:** while Docker is running, the scheduler runs,
  so posts go out on time without a terminal open. This moves "always-on"
  closer, but still only while this machine is on.
- nginx resolves the backend per request, so the UI loads even when the
  backend is down, and API calls get a plain-language 503 instead of a
  broken page.
- Two run modes now exist (local dev and Docker). Both must keep working;
  the tests cover both `serve_frontend` settings.
