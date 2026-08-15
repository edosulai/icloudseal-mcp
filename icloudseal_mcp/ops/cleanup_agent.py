"""Optional LaunchAgent *template* for recurring mail cleanup.

Generate a plist. Write it only with --apply. Never launchctl load unless the
user asked. The committed template uses placeholders, not live home paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

LABEL = "dev.icloudseal.mail-cleanup"
INTERVAL_SECONDS = 86_400


def generate_mail_cleanup_plist(
    *,
    python_exe: str | None = None,
    interval: int = INTERVAL_SECONDS,
) -> str:
    if interval < 3600:
        raise ValueError("Cleanup interval must be at least 3600 seconds.")
    exe = python_exe or sys.executable
    # Keep the generated file machine-local. Committed docs use placeholders.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>-m</string>
        <string>icloudseal_mcp.cli</string>
        <string>mail</string>
        <string>cleanup</string>
        <string>strict</string>
        <string>--apply</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def write_mail_cleanup_plist(
    dest: Path, *, python_exe: str | None = None, interval: int = INTERVAL_SECONDS
) -> Path:
    dest = dest.expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        generate_mail_cleanup_plist(python_exe=python_exe, interval=interval)
    )
    return dest
