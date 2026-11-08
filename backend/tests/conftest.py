from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app
from backend.app.platforms import AccountProfile, AccountStatus, PostExtras, PublishError, PublishResult, Publisher


class FakePublisher(Publisher):
    """Records posts instead of sending them. Set `fail_with` to simulate an error,
    `warning` to simulate a post that went out with a problem, `stats` to report statistics."""

    def __init__(self):
        self.sent: list[str] = []
        self.extras: list[PostExtras | None] = []
        self.deleted: list[str] = []
        self.edited: list[tuple[str, str]] = []
        self.fail_with: str | None = None
        self.warning: str | None = None
        self.stats: dict[str, int] | None = None

    def publish(self, text: str, extras: PostExtras | None = None) -> PublishResult:
        if self.fail_with:
            raise PublishError(self.fail_with)
        self.sent.append(text)
        self.extras.append(extras)
        n = len(self.sent)
        return PublishResult(external_id=f"urn:li:share:70000000000000000{n:02d}",
                             external_url=f"https://x.test/{n}", warning=self.warning)

    def delete(self, external_id: str) -> None:
        if self.fail_with:
            raise PublishError(self.fail_with)
        self.deleted.append(external_id)

    def edit(self, external_id: str, text: str) -> None:
        if self.fail_with:
            raise PublishError(self.fail_with)
        self.edited.append((external_id, text))

    def stats_enabled(self) -> bool:
        return self.stats is not None

    def metrics(self, external_id: str) -> dict[str, int]:
        if self.fail_with:
            raise PublishError(self.fail_with)
        return dict(self.stats or {})

    def check(self) -> AccountStatus:
        return AccountStatus(True, True, "@tester", "ok")

    profile_calls = 0

    def profile(self) -> AccountProfile:
        self.profile_calls += 1
        if self.fail_with:
            raise PublishError(self.fail_with)
        return AccountProfile(name="Tester", handle="@tester", url="https://x.test/tester",
                              avatar="https://x.test/tester.png", followers=12, following=3, posts=40)


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
