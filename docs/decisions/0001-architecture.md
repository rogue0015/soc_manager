# 0001: Overall architecture

- **Status:** Accepted (frontend hosting amended by [0002](0002-docker.md))
- **Date:** 2026-11-07

## Context

A single person wants one place to plan and publish posts across several
social platforms, starting with X, and to turn their blog
(rogue0015.github.io/blogs) into a posting schedule. Requirements:

- HTML/JS user interface, Python backend.
- Runs on their own machine; credentials stay local.
- Easy to add platforms later.

## Decision

| Area | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python 3.12) | Typed request validation, auto-generated API docs at `/docs`, small. |
| Storage | **SQLite** (stdlib `sqlite3`, no ORM) | One user, zero setup, one file to back up. The schema is small enough that plain SQL stays readable. |
| Frontend | **Plain HTML + CSS + ES modules**, no build step | Nothing to install or compile; the backend serves the files directly. |
| Scheduling | **Background task inside the API process**, checks every 30 s | No extra services (no Redis, no Celery). |
| Platforms | **Registry + adapter** (`backend/app/platforms/`) | Adding a platform touches one folder. |
| Blog import | **sitemap.xml + Open Graph meta tags** | The blog has no RSS feed; every post already has clean meta tags. |
| Times | **Stored as UTC ISO-8601 strings**, shown in the browser's local time | Avoids timezone bugs; string order equals time order. |

## Consequences

- **Posts only go out while the server is running.** Anything missed while it
  was off goes out on the next start. If always-on posting matters, run it as
  a systemd user service or on a small always-on host (see roadmap).
- **No double posting:** due posts are claimed atomically
  (`scheduled → publishing`) before sending. A post interrupted mid-send is
  marked *failed*, not retried automatically, because we can't tell whether
  the platform received it.
- Each platform on a post succeeds or fails independently (`post_targets` table).
- The frontend duplicates two small pieces of backend logic (character
  counting and blog share text) for instant feedback. The backend stays the
  authority and re-validates everything.
- No authentication on the web UI: it binds to `127.0.0.1` only. Must be
  revisited before exposing it on a network.
