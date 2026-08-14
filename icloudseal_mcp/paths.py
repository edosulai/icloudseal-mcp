"""Shared on-disk locations for icloudseal-mcp."""

from __future__ import annotations

import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "icloudseal-mcp"
DB_PATH = APP_DIR / "cache.db"
BACKUP_DIR = APP_DIR / "backups"
PLANS_DIR = APP_DIR / "plans"
EXPORTS_DIR = APP_DIR / "exports"


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"State path must be a real directory, not a symlink: {path}")
    if metadata.st_uid != os.geteuid():
        raise ValueError(f"State directory must be owned by the current user: {path}")
    path.chmod(0o700)
    return path


def ensure_app_dir() -> None:
    _private_directory(APP_DIR)


def managed_path(
    root: Path,
    value: str | Path,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a user path inside an owner-only application directory."""
    ensure_app_dir()
    _private_directory(root)
    root_resolved = root.resolve()
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else root_resolved / raw
    resolved = candidate.resolve(strict=must_exist)
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path must be inside {root_resolved}.") from exc
    if not relative.parts:
        raise ValueError(f"Path must name a file inside {root_resolved}.")
    return resolved


def plan_path(value: str | Path, *, must_exist: bool = False) -> Path:
    return managed_path(PLANS_DIR, value, must_exist=must_exist)


def export_path(value: str | Path, *, must_exist: bool = False) -> Path:
    return managed_path(EXPORTS_DIR, value, must_exist=must_exist)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def default_plan_path(prefix: str) -> Path:
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", prefix).strip("-.") or "plan"
    return plan_path(f"{safe_prefix}-{timestamp_slug()}.json")
