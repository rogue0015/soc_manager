from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app


def make_settings(tmp_path: Path, serve_frontend: bool) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        env_file=tmp_path / "env",
        frontend_dir=PROJECT_ROOT / "frontend",
        blog_base_url="https://blog.test",
        scheduler_enabled=False,
        scheduler_interval_seconds=1,
        serve_frontend=serve_frontend,
    )


def test_serves_ui_and_api_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, True), publishers={})) as c:
        page = c.get("/")
        assert page.status_code == 200
        assert "<title>Social Manager</title>" in page.text
        assert c.get("/api/health").json() == {"ok": True}


def test_api_only_mode_does_not_serve_ui(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, False), publishers={})) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").json() == {"ok": True}


def test_serve_frontend_flag_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_ENV_FILE", str(tmp_path / "missing-env"))  # never read real secrets in tests
    monkeypatch.delenv("SM_SERVE_FRONTEND", raising=False)
    assert Settings.from_environment().serve_frontend is True
    monkeypatch.setenv("SM_SERVE_FRONTEND", "off")
    assert Settings.from_environment().serve_frontend is False
