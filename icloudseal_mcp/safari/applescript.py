"""Safari access via AppleScript.

Reads never launch Safari. Opening a URL is argv-only: the URL is never
interpolated into AppleScript source. Only http/https are accepted.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

FIELD = "\x1f"
RECORD = "\x1e"

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_TARGETS = frozenset({"new_tab", "new_window"})
MAX_SEARCH_LEN = 200
MAX_PAGE_CHARS = 8_000
MAX_BOOKMARK_TITLE = 200


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


def search_url(query: str) -> str:
    """Build an https Google search URL. Never prepends a scheme to bare words."""
    candidate = (query or "").strip()
    if not candidate:
        raise SafariError("Search query is required.")
    if any(ch in candidate for ch in "\r\n\x00"):
        raise SafariError("Search query must not contain control characters.")
    if len(candidate) > MAX_SEARCH_LEN:
        raise SafariError(f"Search query is limited to {MAX_SEARCH_LEN} characters.")
    return validate_url(f"https://www.google.com/search?q={quote_plus(candidate)}")


def close_tab(
    *,
    window_index: int,
    tab_index: int,
    name: str,
    url: str,
) -> None:
    """Close a tab only if its frozen name/url snapshot still matches."""
    if not isinstance(window_index, int) or isinstance(window_index, bool) or window_index < 1:
        raise SafariError("window_index must be a positive integer.")
    if not isinstance(tab_index, int) or isinstance(tab_index, bool) or tab_index < 1:
        raise SafariError("tab_index must be a positive integer.")
    if any(ch in (name or "") for ch in "\r\n\x00"):
        raise SafariError("tab name must not contain control characters.")
    canonical = validate_url(url)
    script = """on run argv
    set wIdx to item 1 of argv as integer
    set tIdx to item 2 of argv as integer
    set expectedName to item 3 of argv
    set expectedURL to item 4 of argv
    tell application "Safari"
        set theTab to tab tIdx of window wIdx
        if (name of theTab) is not expectedName then error "Safari tab name changed"
        if (URL of theTab) is not expectedURL then error "Safari tab URL changed"
        close theTab
    end tell
end run"""
    _run(script, str(window_index), str(tab_index), name, canonical)


def validate_bookmark_title(title: str) -> str:
    name = (title or "").strip()
    if not name:
        raise SafariError("bookmark title is required.")
    if any(ch in name for ch in "\r\n\x00"):
        raise SafariError("bookmark title must not contain control characters.")
    if len(name) > MAX_BOOKMARK_TITLE:
        raise SafariError(f"bookmark title is limited to {MAX_BOOKMARK_TITLE} characters.")
    return name


def add_bookmark(title: str, url: str) -> None:
    """Add one bookmarks-bar item. Title and URL go through argv only."""
    name = validate_bookmark_title(title)
    canonical = validate_url(url)
    script = """on run argv
    set theName to item 1 of argv
    set theURL to item 2 of argv
    tell application "Safari"
        if (count of windows) is 0 then activate
        make new bookmark item at end of bookmarks bar with properties {name:theName, URL:theURL}
    end tell
end run"""
    _run(script, name, canonical)


def remove_bookmark(title: str, url: str) -> None:
    """Delete the first bookmarks-bar item whose frozen title+URL still match."""
    name = validate_bookmark_title(title)
    canonical = validate_url(url)
    script = """on run argv
    set theName to item 1 of argv
    set theURL to item 2 of argv
    tell application "Safari"
        set theItems to (every bookmark item of bookmarks bar whose name is theName and URL is theURL)
        if (count of theItems) is 0 then error "No Safari bookmark with that title and URL"
        delete item 1 of theItems
    end tell
end run"""
    _run(script, name, canonical)


def page_text(
    *,
    window_index: int,
    tab_index: int,
    name: str,
    url: str,
    max_chars: int = MAX_PAGE_CHARS,
) -> str:
    """Return Safari tab source text. Never executes JavaScript."""
    if not isinstance(window_index, int) or isinstance(window_index, bool) or window_index < 1:
        raise SafariError("window_index must be a positive integer.")
    if not isinstance(tab_index, int) or isinstance(tab_index, bool) or tab_index < 1:
        raise SafariError("tab_index must be a positive integer.")
    if not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise SafariError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}.")
    if any(ch in (name or "") for ch in "\r\n\x00"):
        raise SafariError("tab name must not contain control characters.")
    canonical = validate_url(url)
    script = """on run argv
    set wIdx to item 1 of argv as integer
    set tIdx to item 2 of argv as integer
    set expectedName to item 3 of argv
    set expectedURL to item 4 of argv
    tell application "Safari"
        set theTab to tab tIdx of window wIdx
        if (name of theTab) is not expectedName then error "Safari tab name changed"
        if (URL of theTab) is not expectedURL then error "Safari tab URL changed"
        return source of theTab
    end tell
end run"""
    raw = _run(script, str(window_index), str(tab_index), name, canonical)
    return raw[:max_chars]


ALLOWED_EXTRACTS = frozenset({"title_text"})

def page_extract(
    *,
    window_index: int,
    tab_index: int,
    name: str,
    url: str,
    extract: str = "title_text",
    max_chars: int = MAX_PAGE_CHARS,
) -> str:
    """Return allowlisted title+innerText. Refuses arbitrary JavaScript."""
    if extract not in ALLOWED_EXTRACTS:
        raise SafariError("extract must be title_text (arbitrary JavaScript is refused).")
    if not isinstance(window_index, int) or isinstance(window_index, bool) or window_index < 1:
        raise SafariError("window_index must be a positive integer.")
    if not isinstance(tab_index, int) or isinstance(tab_index, bool) or tab_index < 1:
        raise SafariError("tab_index must be a positive integer.")
    if not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise SafariError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}.")
    if any(ch in (name or "") for ch in "\r\n\x00"):
        raise SafariError("tab name must not contain control characters.")
    canonical = validate_url(url)
    # JS is a constant in this script. User input never reaches do JavaScript.
    script = """on run argv
    set wIdx to item 1 of argv as integer
    set tIdx to item 2 of argv as integer
    set expectedName to item 3 of argv
    set expectedURL to item 4 of argv
    tell application "Safari"
        set theTab to tab tIdx of window wIdx
        if (name of theTab) is not expectedName then error "Safari tab name changed"
        if (URL of theTab) is not expectedURL then error "Safari tab URL changed"
        return do JavaScript "(function(){var t=document.title||'';var b=(document.body&&document.body.innerText)?document.body.innerText:'';return t+'\\n'+b;})()" in theTab
    end tell
end run"""
    raw = _run(script, str(window_index), str(tab_index), name, canonical)
    return raw[:max_chars]
