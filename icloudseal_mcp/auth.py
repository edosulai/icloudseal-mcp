"""Credential storage via macOS Keychain.

Uses the `security` CLI shipped with macOS. No third-party dependency, no
plaintext on disk. The app-specific password is stored as an
internet-password keychain item with service `icloud-mail-agent`.
"""

from __future__ import annotations

import getpass
import subprocess
from dataclasses import dataclass

from .paths import APP_DIR

# Keychain service stays "icloud-mail-agent" so the existing app-specific
# password keeps working after renames (mail-agent → icloud-agent → icloudseal-mcp).
# The same credential is used for IMAP/SMTP (mail) and CardDAV/CalDAV (contacts/calendar).
SERVICE_NAME = "icloud-mail-agent"
CONFIG_DIR = APP_DIR
EMAIL_FILE = CONFIG_DIR / "email"


class AuthError(RuntimeError):
    """Raised when credentials cannot be stored or retrieved."""


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str


def _run_security(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the macOS `security` CLI."""
    return subprocess.run(
        ["security", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def store_credentials(email: str, password: str) -> None:
    """Persist credentials in the macOS Keychain (idempotent: overwrites if exists)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # -U updates the item if it already exists; otherwise creates it.
    # -s service, -a account, -w password, -T "" disables app-access prompts (we control it)
    result = _run_security(
        [
            "add-generic-password",
            "-U",
            "-s", SERVICE_NAME,
            "-a", email,
            "-w", password,
            "-l", f"{SERVICE_NAME} ({email})",
        ]
    )
    if result.returncode != 0:
        raise AuthError(f"Failed to store credentials: {result.stderr.strip()}")

    EMAIL_FILE.write_text(email + "\n")


def load_credentials(email: str | None = None) -> Credentials:
    """Retrieve credentials from the Keychain.

    If `email` is None, reads the last-used email from the config dir.
    """
    if email is None:
        if not EMAIL_FILE.exists():
            raise AuthError(
                "No email registered yet. Run: icloudseal-mcp mail setup --email <you@icloud.com>"
            )
        email = EMAIL_FILE.read_text().strip()

    result = _run_security(
        [
            "find-generic-password",
            "-s", SERVICE_NAME,
            "-a", email,
            "-w",
        ]
    )
    if result.returncode != 0:
        raise AuthError(
            f"No keychain item for {email!r}. "
            f"Run: icloudseal-mcp mail setup --email {email}"
        )
    password = result.stdout.strip()
    if not password:
        raise AuthError(f"Empty password retrieved for {email!r}.")

    return Credentials(email=email, password=password)


def delete_credentials(email: str) -> None:
    """Remove credentials from the Keychain."""
    result = _run_security(
        [
            "delete-generic-password",
            "-s", SERVICE_NAME,
            "-a", email,
        ]
    )
    if result.returncode != 0:
        raise AuthError(f"Failed to delete credentials: {result.stderr.strip()}")


def prompt_and_store(email: str) -> None:
    """Interactive setup: prompt for password (hidden input) and store it."""
    print(f"Storing iCloud app-specific password for: {email}")
    print("(Generate one at https://appleid.apple.com -> App-Specific Passwords)")
    print()
    password = getpass.getpass("App-specific password: ").strip()
    if not password:
        raise AuthError("Empty password — aborting.")
    # Normalize: Apple displays passwords with hyphens; IMAP accepts either.
    password = password.replace(" ", "")
    store_credentials(email, password)
    print(f"Stored in Keychain (service={SERVICE_NAME}, account={email}).")
