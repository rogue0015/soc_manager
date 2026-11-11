# Render image: ONE web service that serves both the UI and the API.
#
# Local/compose still uses the two-container setup (backend/Dockerfile +
# frontend/Dockerfile). This image is only for Render, where a single service
# is simpler: FastAPI serves the static UI itself, so the browser talks to one
# origin and no nginx or CORS is needed.
#
# Build context is the repository root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first so code changes don't reinstall them.
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# The code is the `backend` Python package. PROJECT_ROOT resolves to /srv, so
# the UI must sit at /srv/frontend for SM_SERVE_FRONTEND to find it.
COPY backend /srv/backend
COPY frontend /srv/frontend

# Serve the UI from FastAPI, and keep the SQLite file, the uploaded media and
# the platform-keys file on the persistent disk mounted at /data.
ENV SM_DB_PATH=/data/social_manager.db \
    SM_ENV_FILE=/data/env \
    SM_SERVE_FRONTEND=on

EXPOSE 8000

# Runs as root on purpose: Render mounts the disk at /data owned by root, and
# the app must be able to write the database and refreshed sign-in tokens.
#
# Platform keys arrive as a read-only Render Secret File at /etc/secrets/env.
# Seed them onto the writable disk once; afterwards the disk file is
# authoritative so sign-in tokens survive restarts and redeploys. To change or
# add a key later, set it as a Render environment variable (env vars win over
# the file) or delete /data/env so the secret file is copied again.
CMD ["sh", "-c", "mkdir -p /data && if [ ! -f /data/env ] && [ -f /etc/secrets/env ]; then cp /etc/secrets/env /data/env; fi; exec uvicorn --factory backend.app.main:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
