"""Shortcuts.app access via the ``shortcuts`` CLI.

List is a read. Run is gated by exact name through argv. Arbitrary shortcut
input is refused so prepare can freeze a single identity.
"""

from __future__ import annotations

import shutil
import subprocess

MAX_NAME_LEN = 200
MAX_LIST = 200


class ShortcutsError(RuntimeError):
    pass


def _binary() -> str:
    path = shutil.which("shortcuts")
    if not path:
        raise ShortcutsError("The shortcuts CLI is not available on this Mac.")
    return path


def _run(*args: str) -> str:
    result = subprocess.run(
        [_binary(), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ShortcutsError(result.stderr.strip() or "shortcuts failed")
    return result.stdout


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ShortcutsError("shortcut name is required.")
    if any(ch in name for ch in "\r\n\x00"):
        raise ShortcutsError("shortcut name must not contain control characters.")
    if len(name) > MAX_NAME_LEN:
        raise ShortcutsError(f"shortcut name is limited to {MAX_NAME_LEN} characters.")
    return name


def list_shortcuts(*, limit: int = 100) -> list[str]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST:
        raise ShortcutsError(f"limit must be an integer from 1 to {MAX_LIST}.")
    raw = _run("list")
    names = [line.strip() for line in raw.splitlines() if line.strip()]
    return names[:limit]


def require_named(name: str) -> str:
    """Return the exact installed shortcut name, or fail closed."""
    target = _clean_name(name)
    names = list_shortcuts(limit=MAX_LIST)
    if target not in names:
        raise ShortcutsError(f"No shortcut named {target!r}.")
    return target


def run_shortcut(name: str) -> None:
    """Run one installed shortcut by exact name. No user input is passed."""
    target = require_named(name)
    _run("run", target)


__all__ = [
    "MAX_LIST",
    "MAX_NAME_LEN",
    "ShortcutsError",
    "list_shortcuts",
    "require_named",
    "run_shortcut",
]
