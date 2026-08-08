"""Shared on-disk locations for icloudseal-mcp."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "icloudseal-mcp"
DB_PATH = APP_DIR / "cache.db"
BACKUP_DIR = APP_DIR / "backups"
PLANS_DIR = APP_DIR / "plans"


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def default_plan_path(prefix: str) -> Path:
    return PLANS_DIR / f"{prefix}-{timestamp_slug()}.json"
