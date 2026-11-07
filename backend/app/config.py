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


def env_flag(name: str, default: str = "on") -> bool:
    """True unless the variable is set to 0 / off / false."""
    return os.environ.get(name, default).strip().lower() not in ("0", "off", "false")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    env_file: Path
    frontend_dir: Path
    blog_base_url: str
    scheduler_enabled: bool
    scheduler_interval_seconds: int
    # Local dev serves the UI from this process. In Docker the frontend has its
    # own container and the backend is API-only (docs/decisions/0002-docker.md).
    serve_frontend: bool = True
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
            scheduler_enabled=env_flag("SM_SCHEDULER"),
            scheduler_interval_seconds=int(os.environ.get("SM_SCHEDULER_INTERVAL", "30")),
            serve_frontend=env_flag("SM_SERVE_FRONTEND"),
            secrets=secrets,
        )
