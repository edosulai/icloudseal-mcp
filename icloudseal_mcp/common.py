"""Shared CLI helpers used across icloudseal-mcp domains.

Domain-agnostic utilities only: time parsing, human formatting, and JSON plan
IO. Domain-specific apply/backup logic stays in each domain's module.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console

console = Console()


def parse_since(value: str | None) -> str | None:
    """Convert "7d" / "24h" / "2025-01-01" to IMAP date format (DD-Mon-YYYY)."""
    if not value:
        return None
    dt = _relative_or_iso(value, label="--since")
    return dt.strftime("%d-%b-%Y")


def parse_age_cutoff(value: str | None) -> str | None:
    """Convert "30d" / "12h" / ISO date into an ISO cutoff for cache queries."""
    if value is None:
        return None
    return _relative_or_iso(value, label="--older-than").isoformat()


def _relative_or_iso(value: str, *, label: str) -> datetime:
    m = re.fullmatch(r"(\d+)([dh])", value)
    if m:
        n = int(m.group(1))
        delta = timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n)
        return datetime.now(UTC) - delta
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError as exc:
        raise SystemExit(f"Invalid {label} value: {value!r}") from exc


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}TB"


def write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def load_json_plan(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read plan file {path}: {exc}") from exc
