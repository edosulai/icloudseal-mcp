"""Shared on-disk locations for icloud-agent.

NOTE: the storage directory and Keychain service name intentionally remain
``icloud-mail-agent`` even though the project was renamed to ``icloud-agent``.
The iCloud app-specific password, the metadata cache, and historical backups
were created under that identifier; keeping it avoids losing credentials and
history during the rename. New domains (contacts, calendar, ...) share the same
directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

# Kept as "icloud-mail-agent" for backward compatibility (see module docstring).
APP_DIR = Path.home() / "Library" / "Application Support" / "icloud-mail-agent"
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
