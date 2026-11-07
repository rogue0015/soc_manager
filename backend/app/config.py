"""App settings.

Everything configurable lives here so the rest of the code never reads
environment variables directly. Each setting can be overridden with an
environment variable (see README "Configuration").
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a dotenv-style file. Missing file -> {}."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    db_path: Path
    env_file: Path
    frontend_dir: Path
    blog_base_url: str
    scheduler_enabled: bool
    scheduler_interval_seconds: int
    # Secrets from the env file, with real environment variables taking priority.
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_environment(cls) -> "Settings":
        env_file = Path(os.environ.get("SM_ENV_FILE", PROJECT_ROOT / "env"))
        secrets = load_env_file(env_file)
        secrets.update({k: v for k, v in os.environ.items() if k.startswith("X_")})
        return cls(
            db_path=Path(os.environ.get("SM_DB_PATH", PROJECT_ROOT / "data" / "social_manager.db")),
            env_file=env_file,
            frontend_dir=PROJECT_ROOT / "frontend",
            blog_base_url=os.environ.get("SM_BLOG_BASE_URL", "https://rogue0015.github.io").rstrip("/"),
            scheduler_enabled=os.environ.get("SM_SCHEDULER", "on").lower() not in ("0", "off", "false"),
            scheduler_interval_seconds=int(os.environ.get("SM_SCHEDULER_INTERVAL", "30")),
            secrets=secrets,
        )
