"""Safari access via AppleScript.

Reads never launch Safari. Opening a URL is argv-only: the URL is never
interpolated into AppleScript source. Only http/https are accepted.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

FIELD = "\x1f"
RECORD = "\x1e"

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_TARGETS = frozenset({"new_tab", "new_window"})


class SafariError(RuntimeError):
    pass


@dataclass(frozen=True)
class SafariTab:
    window_index: int
    tab_index: int
    name: str
    url: str
    is_current: bool


def _run(script: str, *args: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script, "--", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SafariError(result.stderr.strip() or "osascript failed")
    return result.stdout


def safari_is_running() -> bool:
    script = (
        'tell application "System Events" to '
        '(name of processes) contains "Safari"'
    )
    return _run(script).strip().lower() == "true"


def validate_url(url: str) -> str:
    """Accept only http/https. Never prepend a scheme."""
    candidate = (url or "").strip()
    if not candidate:
        raise SafariError("URL is required.")
    if any(ch in candidate for ch in "\r\n\x00"):
        raise SafariError("URL must not contain control characters.")
    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SafariError("URL must use http or https (no implicit scheme prefix).")
    if not parsed.netloc:
        raise SafariError("URL must include a host.")
    return candidate


def list_tabs() -> list[SafariTab]:
    """Return open Safari tabs. Empty list if Safari is not running."""
    if not safari_is_running():
        return []
    script = (
        'tell application "Safari"\n'
        "  set output to \"\"\n"
        "  set windowCount to count of windows\n"
        "  repeat with w from 1 to windowCount\n"
        "    set tabCount to count of tabs of window w\n"
        "    set currentTabIndex to index of current tab of window w\n"
        "    repeat with t from 1 to tabCount\n"
        "      set theTab to tab t of window w\n"
        "      set isCurrent to \"0\"\n"
        "      if t is currentTabIndex then set isCurrent to \"1\"\n"
        f'      set output to output & w & "{FIELD}" & t & "{FIELD}" '
        f'& (name of theTab) & "{FIELD}" & (URL of theTab) & "{FIELD}" '
        f'& isCurrent & "{RECORD}"\n'
        "    end repeat\n"
        "  end repeat\n"
        "  return output\n"
        "end tell"
    )
    raw = _run(script)
    tabs: list[SafariTab] = []
    for rec in raw.split(RECORD):
        if not rec.strip():
            continue
        parts = rec.split(FIELD)
        if len(parts) < 5:
            continue
        try:
            window_index = int(parts[0])
            tab_index = int(parts[1])
        except ValueError:
            continue
        tabs.append(
            SafariTab(
                window_index=window_index,
                tab_index=tab_index,
                name=parts[2],
                url=parts[3],
                is_current=parts[4] == "1",
            )
        )
    return tabs


def current_tab() -> SafariTab | None:
    for tab in list_tabs():
        if tab.is_current:
            return tab
    return None


def open_url(url: str, *, target: str = "new_tab") -> None:
    canonical = validate_url(url)
    if target not in ALLOWED_TARGETS:
        raise SafariError("target must be new_tab or new_window.")
    kind = "tab" if target == "new_tab" else "window"
    script = """on run argv
    set theURL to item 1 of argv
    set theKind to item 2 of argv
    tell application "Safari"
        activate
        if theKind is "window" then
            make new document with properties {URL:theURL}
        else
            if (count of windows) is 0 then
                make new document with properties {URL:theURL}
            else
                tell window 1
                    make new tab with properties {URL:theURL}
                end tell
            end if
        end if
    end tell
end run"""
    _run(script, canonical, kind)
