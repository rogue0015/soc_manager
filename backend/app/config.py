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

# Environment variables with these prefixes are platform keys. Docker passes
# keys as environment variables, so a new platform's prefix must be added here.
SECRET_PREFIXES = ("X_", "LINKEDIN_", "META_", "THREADS_", "DEVTO_", "BLUESKY_", "YOUTUBE_", "TIKTOK_",
                   "INSTAGRAM_", "GEMINI_")


def _env_key(line: str) -> str | None:
    """The KEY of a KEY=VALUE line, or None for blanks and comments."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    return line.split("=", 1)[0].strip()


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a dotenv-style file. Missing file -> {}."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        if key := _env_key(raw):
            value = raw.split("=", 1)[1]
            values[key] = value.strip().strip('"').strip("'")
    return values


def set_env_values(path: Path, values: dict[str, str]) -> None:
    """Set keys in a dotenv-style file, keeping every other line as it is.

    Used by sign-in scripts that receive a new token. Every existing line
    for a key is replaced (the loader keeps the last one); new keys are
    appended. A new file is created readable by its owner only.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    replaced: set[str] = set()
    for i, raw in enumerate(lines):
        key = _env_key(raw)
        if key in values:
            lines[i] = f"{key}={values[key]}"
            replaced.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in replaced]
    if not path.exists():
        path.touch(mode=0o600)
    path.write_text("\n".join(lines) + "\n")


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
    # Where the browser reaches the app. Sign-in redirects (OAuth) come back here, so
    # this exact address must be registered with the platform. See ADR 0005.
    public_url: str = "http://localhost:8000"
    # How often the background job fetches post statistics from platforms that share them
    # (dev.to, Bluesky, YouTube, ...), so Analytics stays current without pressing Refresh.
    stats_interval_seconds: int = 1800
    # Secrets from the env file, with real environment variables taking priority.
    # One shared dict: a sign-in updates it in place (services/signin.py).
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def media_dir(self) -> Path:
        """Uploaded images live next to the database (Docker: in the data volume)."""
        return self.db_path.parent / "media"

    @classmethod
    def from_environment(cls) -> "Settings":
        env_file = Path(os.environ.get("SM_ENV_FILE", PROJECT_ROOT / "env"))
        secrets = load_env_file(env_file)
        # A blank variable must not hide a real value in the file.
        secrets.update({k: v for k, v in os.environ.items() if k.startswith(SECRET_PREFIXES) and v})
        default_url = f"http://localhost:{os.environ.get('PORT', '8000')}"
        return cls(
            db_path=Path(os.environ.get("SM_DB_PATH", PROJECT_ROOT / "data" / "social_manager.db")),
            env_file=env_file,
            frontend_dir=PROJECT_ROOT / "frontend",
            blog_base_url=os.environ.get("SM_BLOG_BASE_URL", "https://rogue0015.github.io").rstrip("/"),
            scheduler_enabled=env_flag("SM_SCHEDULER"),
            scheduler_interval_seconds=int(os.environ.get("SM_SCHEDULER_INTERVAL", "30")),
            serve_frontend=env_flag("SM_SERVE_FRONTEND"),
            public_url=os.environ.get("SM_PUBLIC_URL", default_url).rstrip("/"),
            stats_interval_seconds=int(os.environ.get("SM_STATS_INTERVAL", "1800")),
            secrets=secrets,
        )
