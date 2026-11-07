#!/usr/bin/env bash
# Start the app locally at http://127.0.0.1:8000 (auto-reloads on code changes).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn --factory backend.app.main:create_app --reload --host 127.0.0.1 --port "${PORT:-8000}"
