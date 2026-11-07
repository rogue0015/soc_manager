# Backend: the FastAPI API, the background publisher and the SQLite database.
# API-only: the UI is served by the frontend container, which proxies /api here.
# Build context is ./backend (see compose.yaml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first so code changes don't reinstall them.
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Run as a normal user. /data holds the SQLite file (a named volume in compose).
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data

# The code is the `backend` Python package (imports are backend.app.*).
COPY --chown=app:app . /srv/backend

ENV SM_DB_PATH=/data/social_manager.db \
    SM_SERVE_FRONTEND=off

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# One worker on purpose: the scheduler runs inside the API process.
# 0.0.0.0 is inside the container only; compose publishes no backend port.
CMD ["uvicorn", "--factory", "backend.app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
