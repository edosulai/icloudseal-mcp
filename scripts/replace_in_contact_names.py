#!/usr/bin/env python3
"""Replace a substring inside iCloud contact display names.

Only the ``FN`` (formatted name) and ``N`` (structured name) vCard properties
are rewritten. Every other line — photos, addresses, notes, birthdays, emails,
phones, X-* extensions — is left byte-for-byte untouched.

The match is a literal substring (not a regex). By default it is
case-insensitive, so ``(steradian)``, ``(Steradian)`` and ``(STERADIAN)`` all
match. The replacement text is inserted verbatim.

Safety model (mirrors the rest of icloudseal-mcp):
  * dry-run by default; ``--apply`` is required to push changes to iCloud
  * every contact that would change has its ORIGINAL raw vCard backed up first
  * verbose 0-100%% progress line per contact

Examples:
    # dry-run: (steradian) -> (SDO)
    python scripts/replace_in_contact_names.py "(steradian)" "(SDO)"

    # actually apply
    python scripts/replace_in_contact_names.py "(steradian)" "(SDO)" --apply

    # case-sensitive match
    python scripts/replace_in_contact_names.py "sapo" "SAPO" --case-sensitive
"""

from __future__ import annotations

import argparse
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
# Substring replacement (case-insensitive by default, literal not regex)
# ---------------------------------------------------------------------------


def _replace_ci(text: str, needle: str, replacement: str, *, case_sensitive: bool) -> str:
    if case_sensitive:
        return text.replace(needle, replacement)
    # Case-insensitive literal replace, preserving surrounding text exactly.
    if not needle:
        return text
    out: list[str] = []
    low_text = text.lower()
    low_needle = needle.lower()
    i = 0
    n = len(needle)
    while True:
        j = low_text.find(low_needle, i)
        if j == -1:
            out.append(text[i:])
            break
        out.append(text[i:j])
        out.append(replacement)
        i = j + n
    return "".join(out)


def _prop_name(line: str) -> str:
    head = line.split(":", 1)[0]
    head = head.split(".", 1)[-1]  # drop group prefix like item1.FN
    return head.split(";", 1)[0].upper()


def rewrite_raw_vcard(
    raw: str,
    needle: str,
    replacement: str,
    *,
    case_sensitive: bool,
    org_needle: str | None = None,
    org_replacement: str | None = None,
) -> tuple[str, bool]:
    """Return (new_raw, changed). Only FN/N (and optionally ORG) value text is
    touched. Pass *org_needle*/*org_replacement* to also keep the Company
    field in sync with the display-name substring rename (e.g. renaming the
    ``(IF)`` affiliation tag should also rename ``ORG:IF`` -> ``ORG:UPI``)."""
    physical = raw.replace("\r\n", "\n").split("\n")

    # Unfold RFC 6350 continuation lines.
    logical: list[str] = []
    for line in physical:
        if line[:1] in (" ", "\t") and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)

    changed = False
    for idx, line in enumerate(logical):
        if ":" not in line:
            continue
        prop = _prop_name(line)
        if prop in ("FN", "N"):
            head, value = line.split(":", 1)
            new_value = _replace_ci(
                value, needle, replacement, case_sensitive=case_sensitive
            )
            if new_value != value:
                logical[idx] = f"{head}:{new_value}"
                changed = True
        elif prop == "ORG" and org_needle is not None:
            head, value = line.split(":", 1)
            # ORG is structured: Company;Unit;… — only replace a WHOLE
            # component that exactly equals org_needle. This avoids clobbering
            # substrings inside real words (e.g. the "if" in "Ariffullah").
            comps = value.split(";")
            hit = False
            for j, comp in enumerate(comps):
                cmp_a = comp.strip()
                cmp_b = org_needle.strip()
                same = cmp_a == cmp_b if case_sensitive else cmp_a.casefold() == cmp_b.casefold()
                if same:
                    # Preserve surrounding whitespace of the original component.
                    lead = comp[: len(comp) - len(comp.lstrip())]
                    trail = comp[len(comp.rstrip()) :]
                    comps[j] = f"{lead}{org_replacement or ''}{trail}"
                    hit = True
            if hit:
                new_value = ";".join(comps)
                if new_value != value:
                    logical[idx] = f"{head}:{new_value}"
                    changed = True

    new_raw = "\r\n".join(logical)
    if not new_raw.endswith("\r\n"):
        new_raw += "\r\n"
    return new_raw, changed


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_originals(contacts: list[Contact], label: str) -> Path:
    root = BACKUP_DIR / f"contacts-{label}-{timestamp_slug()}"
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
    parser.add_argument("needle", help="Literal substring to find in names.")
    parser.add_argument("replacement", help="Text to replace it with.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually push updates to iCloud (default: dry-run).",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match case-sensitively (default: case-insensitive).",
    )
    parser.add_argument(
        "--sync-org",
        action="store_true",
        help=(
            "Also rename the Company/ORG field: strips surrounding parentheses "
            "from needle/replacement (e.g. '(IF)'→'(UPI)' also does ORG IF→UPI)."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose per-contact logging."
    )
    args = parser.parse_args()

    def _strip_parens(s: str) -> str:
        t = s.strip()
        if t.startswith("(") and t.endswith(")"):
            return t[1:-1].strip()
        return t

    org_needle = _strip_parens(args.needle) if args.sync_org else None
    org_replacement = _strip_parens(args.replacement) if args.sync_org else None

    started = time.monotonic()
    console.print(
        f"[cyan][start][/cyan] replace [yellow]{args.needle!r}[/yellow] -> "
        f"[green]{args.replacement!r}[/green] "
        f"({'case-sensitive' if args.case_sensitive else 'case-insensitive'})"
    )
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    total = len(contacts)
    console.print(f"[cyan][info][/cyan] fetched {total} contact(s)")

    if args.sync_org:
        console.print(
            f"[cyan][info][/cyan] also syncing ORG "
            f"[yellow]{org_needle!r}[/yellow] -> [green]{org_replacement!r}[/green]"
        )

    planned: list[tuple[Contact, str, str]] = []  # (contact, old_fn, new_fn)
    for idx, c in enumerate(contacts, 1):
        raw = c.raw or ""
        new_raw, changed = rewrite_raw_vcard(
            raw,
            args.needle,
            args.replacement,
            case_sensitive=args.case_sensitive,
            org_needle=org_needle,
            org_replacement=org_replacement,
        )
        if changed:
            new_fn = _replace_ci(
                c.full_name,
                args.needle,
                args.replacement,
                case_sensitive=args.case_sensitive,
            )
            planned.append((c, c.full_name, new_fn))
            console.print(
                f"  change [yellow]{c.full_name!r}[/yellow] -> "
                f"[green]{new_fn!r}[/green]"
            )
            c.raw = new_raw  # stash rewritten card for apply phase
        elif args.verbose:
            console.print(f"  skip {c.full_name!r}")

    console.rule(f"{len(planned)} contact(s) would change")

    if not planned:
        console.print("[green]Nothing matched — no changes.[/green]")
        return 0

    if not args.apply:
        console.print(
            "[yellow]Dry-run.[/yellow] Add [bold]--apply[/bold] to push "
            "(originals backed up first)."
        )
        return 0

    # --- apply ---
    console.print("[cyan][backup][/cyan] saving original vCards…")
    pristine = {c.uid: c for c in session.list_contacts()}
    backup_list = [pristine[c.uid] for c, _, _ in planned if c.uid in pristine]
    backup_dir = backup_originals(backup_list, "rename")
    console.print(f"[cyan][backup][/cyan] {len(backup_list)} vCard(s) -> {backup_dir}")

    ok = 0
    fail = 0
    n = len(planned)
    for i, (c, old_fn, new_fn) in enumerate(planned, 1):
        pct = int(i / n * 100)
        try:
            if not c.href:
                raise RuntimeError("missing href")
            session._client.put(  # noqa: SLF001 — internal, intentional
                c.href,
                c.raw,
                content_type="text/vcard; charset=utf-8",
                etag=c.etag,
            )
            ok += 1
            console.print(f"[{i}/{n}] {pct}% — [green]updated[/green] {new_fn!r}")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            console.print(f"[{i}/{n}] {pct}% — [red]FAILED[/red] {old_fn!r}: {exc}")

    elapsed = time.monotonic() - started
    console.print(
        f"[cyan][done][/cyan] updated {ok}, failed {fail}, "
        f"backup at {backup_dir} ({elapsed:.1f}s)"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
