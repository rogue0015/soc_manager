from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app
from backend.app.platforms import AccountStatus, PublishError, PublishResult, Publisher


class FakePublisher(Publisher):
    """Records posts instead of sending them. Set `fail_with` to simulate an error."""

    def __init__(self):
        self.sent: list[str] = []
        self.fail_with: str | None = None

    def publish(self, text: str) -> PublishResult:
        if self.fail_with:
            raise PublishError(self.fail_with)
        self.sent.append(text)
        return PublishResult(external_id=str(len(self.sent)), external_url=f"https://x.test/{len(self.sent)}")

    def check(self) -> AccountStatus:
        return AccountStatus(True, True, "@tester", "ok")


@pytest.fixture
def fake_x() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def app(tmp_path: Path, fake_x: FakePublisher):
    settings = Settings(
        db_path=tmp_path / "test.db",
        env_file=tmp_path / "env",
        frontend_dir=PROJECT_ROOT / "frontend",
        blog_base_url="https://blog.test",
        scheduler_enabled=False,
        scheduler_interval_seconds=1,
    )
    return create_app(settings, publishers={"x": fake_x})


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c
