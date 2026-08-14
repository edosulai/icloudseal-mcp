#!/usr/bin/env python3
"""Lowercase iCloud contact display names, preserving ALL-CAPS abbreviations.

Only the ``FN`` (formatted name) and ``N`` (structured name) vCard properties are
rewritten. Every other line of the raw vCard — photos, addresses, notes,
birthdays, X-* extensions, group prefixes, item labels — is left byte-for-byte
untouched, so nothing but the name casing changes.

A "word" is lowercased unless it is an abbreviation: an all-uppercase token of
two or more letters (e.g. BRI, IGLO, IF, SDO, UPI, KP, SI, AC, SD, DBS, BKS,
BOT). Tokens inside parentheses are treated the same way, so ``(BRI)`` stays
``(BRI)`` while ``(sapo)`` stays lowercase. Single letters, digits, emoji, and
punctuation pass through unchanged.

Safety model (mirrors the rest of icloudseal-mcp):
  * dry-run by default; ``--apply`` is required to push changes to iCloud
  * every contact that would change has its ORIGINAL raw vCard backed up first
  * verbose logging with a 0-100%% progress line per contact

Usage:
    python scripts/lowercase_contacts.py            # dry-run preview
    python scripts/lowercase_contacts.py --apply    # actually update iCloud
    python scripts/lowercase_contacts.py --verbose  # full per-token trace
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icloudseal_mcp.common import console  # noqa: E402
from icloudseal_mcp.contacts import carddav  # noqa: E402
from icloudseal_mcp.contacts.carddav import Contact, ContactsSession  # noqa: E402
from icloudseal_mcp.paths import BACKUP_DIR, timestamp_slug  # noqa: E402

# ---------------------------------------------------------------------------
# Name-casing logic
# ---------------------------------------------------------------------------

# A token is "abbreviation-like" if, ignoring surrounding punctuation, its
# letters are all uppercase and there are at least two of them.
_LETTERS = re.compile(r"[A-Za-z]")


def _is_abbreviation(token: str) -> bool:
    """True if the alphabetic part of *token* is all-caps with >= 2 letters."""
    letters = _LETTERS.findall(token)
    if len(letters) < 2:
        return False
    return all(ch.isupper() for ch in letters)


# Split on whitespace but keep the whitespace runs so we can rebuild exactly.
_WS_SPLIT = re.compile(r"(\s+)")


def lowercase_name(value: str, *, trace: list[str] | None = None) -> str:
    """Lowercase *value* word-by-word, keeping ALL-CAPS abbreviations intact."""
    out_parts: list[str] = []
    for chunk in _WS_SPLIT.split(value):
        if not chunk or chunk.isspace():
            out_parts.append(chunk)
            continue
        if _is_abbreviation(chunk):
            out_parts.append(chunk)  # keep abbreviation as-is
            if trace is not None:
                trace.append(f"keep {chunk!r}")
        else:
            lowered = chunk.lower()
            out_parts.append(lowered)
            if trace is not None and lowered != chunk:
                trace.append(f"{chunk!r} -> {lowered!r}")
    return "".join(out_parts)


# ---------------------------------------------------------------------------
# vCard line rewriting (surgical: only FN and N)
# ---------------------------------------------------------------------------


def _prop_name(line: str) -> str:
    """Uppercase property name of a vCard line, ignoring params and group."""
    head = line.split(":", 1)[0]
    # Strip a group prefix like "item1.FN" or "ITEM1.N".
    head = head.split(".", 1)[-1]
    return head.split(";", 1)[0].upper()


def rewrite_raw_vcard(
    raw: str, *, verbose: bool
) -> tuple[str, bool, list[str]]:
    """Return (new_raw, changed, trace). Only FN and N value casing changes.

    Handles RFC 6350 line folding: a line that has been split across physical
    lines (continuation starts with space/tab) is joined, rewritten, and left
    unfolded (iCloud re-folds on its own; short names never need folding).
    """
    # vCard uses CRLF; normalise for processing then restore CRLF on output.
    physical = raw.replace("\r\n", "\n").split("\n")

    # Unfold: merge continuation lines into their logical line.
    logical: list[str] = []
    for line in physical:
        if line[:1] in (" ", "\t") and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)

    changed = False
    trace: list[str] = []
    for i, line in enumerate(logical):
        if ":" not in line:
            continue
        name = _prop_name(line)
        if name not in ("FN", "N"):
            continue
        head, value = line.split(":", 1)
        if name == "FN":
            new_value = lowercase_name(value, trace=trace if verbose else None)
        else:  # N -> LAST;FIRST;MIDDLE;PREFIX;SUFFIX (each component independently)
            components = value.split(";")
            new_components = [
                lowercase_name(c, trace=trace if verbose else None) for c in components
            ]
            new_value = ";".join(new_components)
        if new_value != value:
            logical[i] = f"{head}:{new_value}"
            changed = True

    new_raw = "\r\n".join(logical)
    if not new_raw.endswith("\r\n"):
        new_raw += "\r\n"
    return new_raw, changed, trace


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_originals(contacts: list[Contact]) -> Path:
    root = BACKUP_DIR / f"contacts-lowercase-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    for c in contacts:
        raw = c.raw or carddav.build_vcard(
            uid=c.uid,
            full_name=c.full_name,
            first=c.first,
            last=c.last,
            org=c.org,
            emails=c.emails,
            phones=c.phones,
        )
        (root / f"{c.uid}.vcf").write_text(raw)
    return root


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually push updates to iCloud (default: dry-run preview).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print a full per-token trace for every changed contact.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N contacts (for testing).",
    )
    args = parser.parse_args()

    started = time.monotonic()
    console.print("[cyan][start][/cyan] connecting to iCloud CardDAV…")
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    if args.limit:
        contacts = contacts[: args.limit]
    total = len(contacts)
    console.print(f"[cyan][info][/cyan] fetched {total} contact(s)")

    planned: list[tuple[Contact, str, str]] = []  # (contact, old_fn, new_fn)
    for idx, c in enumerate(contacts, 1):
        raw = c.raw or ""
        new_raw, changed, trace = rewrite_raw_vcard(raw, verbose=args.verbose)
        pct = int(idx / total * 100) if total else 100
        if changed:
            # Derive the new display name for reporting.
            new_fn = lowercase_name(c.full_name)
            planned.append((c, c.full_name, new_fn))
            console.print(
                f"[{idx}/{total}] {pct}% — change [yellow]{c.full_name!r}[/yellow]"
                f" -> [green]{new_fn!r}[/green]"
            )
            if args.verbose and trace:
                for step in trace:
                    console.print(f"          · {step}")
            # Stash the rewritten raw on the contact for the apply phase.
            c.raw = new_raw
        elif args.verbose:
            console.print(f"[{idx}/{total}] {pct}% — skip {c.full_name!r} (no change)")

    console.rule(f"{len(planned)} of {total} contact(s) would change")

    if not planned:
        console.print("[green]Nothing to do — all names already lowercase.[/green]")
        return 0

    if not args.apply:
        console.print(
            "[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to push "
            "these changes (originals are backed up first)."
        )
        return 0

    # --- apply ---
    # Backup uses the CURRENT (already-mutated) raw only if we didn't keep the
    # original; to be safe we re-list to grab untouched vCards for the backup.
    console.print("[cyan][backup][/cyan] saving original vCards…")
    pristine = {c.uid: c for c in session.list_contacts()}
    backup_list = [pristine[c.uid] for c, _, _ in planned if c.uid in pristine]
    backup_dir = backup_originals(backup_list)
    console.print(f"[cyan][backup][/cyan] {len(backup_list)} vCard(s) -> {backup_dir}")

    ok = 0
    fail = 0
    for i, (c, old_fn, new_fn) in enumerate(planned, 1):
        pct = int(i / len(planned) * 100)
        try:
            # c.href / c.etag came from the live list; c.raw holds rewritten card.
            if not c.href:
                raise RuntimeError("missing href")
            session._client.put(  # noqa: SLF001 — internal, intentional
                c.href,
                c.raw,
                content_type="text/vcard; charset=utf-8",
                etag=c.etag,
            )
            ok += 1
            console.print(
                f"[{i}/{len(planned)}] {pct}% — [green]updated[/green] {new_fn!r}"
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            fail += 1
            console.print(
                f"[{i}/{len(planned)}] {pct}% — [red]FAILED[/red] {old_fn!r}: {exc}"
            )

    elapsed = time.monotonic() - started
    console.print(
        f"[cyan][done][/cyan] updated {ok}, failed {fail}, "
        f"backup at {backup_dir} ({elapsed:.1f}s)"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
