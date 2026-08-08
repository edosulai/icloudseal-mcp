#!/usr/bin/env python3
"""Normalize iCloud contact names for consistency (safe version).

What this DOES change:
1. Affiliation currently stored in N-last (e.g. ``N:(BRI);bu;tania;;``)
   → move out of last name, rebuild FN as ``given (AFFIL)``, set ORG.
2. Outside-paren known acronyms used as company tags
   (``edi SD``, ``isan SD``, ``toa UPI``, ``DBS laundry``, ``customer care DANA ✅``)
   → rewrite to ``name (ACRONYM)`` form + ORG.
3. Broken parentheses ``sekar madura)`` → ``sekar (warung madura)``.
4. Special rename ``a5`` → ``afif (UPI)``.
5. When FN already has ``name (tag)`` but the whole string is stuffed into
   N-first (``N:;andre (SDO);;;``), split given into First and put tag into ORG
   (display name stays the same).

What this does NOT change:
  * Multi-word real names (``edo sulai``, ``yakjuj makjuj``, ``bg dayat``, …)
  * Protected: ``my life 🤬🤬``, ``fake``
  * False-positive acronyms that are ordinary Indonesian words in context
    (``si paling`` is NOT rewritten to ``paling (SI)``)

Also always writes two reports (read-only):
  * empty contacts (no phone + no email)
  * duplicate report (same name / same phone / same email)

Safety: dry-run by default; ``--apply`` required. Originals backed up first.
Only FN / N / ORG lines of the raw vCard are rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icloudseal_mcp.common import console  # noqa: E402
from icloudseal_mcp.contacts.carddav import Contact, ContactsSession  # noqa: E402
from icloudseal_mcp.paths import APP_DIR, BACKUP_DIR, timestamp_slug  # noqa: E402

SKIP_NAMES = frozenset({"my life 🤬🤬", "fake"})

# Company / affiliation acronyms. Matched as whole words.
ACRONYMS = frozenset(
    {
        "BRI",
        "SDO",
        "IGLO",
        "IF",
        "UPI",
        "IIS",
        "KP",
        "SD",
        "SI",
        "MP",
        "KSPS",
        "UPIYPTK",
        "SIGMATECH",
        "FTL",
        "CISO",
        "DBS",
        "DANA",
    }
)

# Only these may be auto-wrapped when bare outside parentheses.
# SI is intentionally EXCLUDED here — "si paling" is Indonesian, not SI.
OUTSIDE_WRAP = frozenset(
    {
        "BRI",
        "SDO",
        "IGLO",
        "IF",
        "UPI",
        "IIS",
        "KP",
        "SD",
        "MP",
        "KSPS",
        "UPIYPTK",
        "SIGMATECH",
        "FTL",
        "CISO",
        "DBS",
        "DANA",
    }
)

# Non-acronym affiliation tags we still treat as company context when already
# parenthesized in FN (do NOT invent these; only extract if already present).
KNOWN_TAGS = frozenset(
    {
        "willman",
        "ordent",
        "sapo",
        "entrust",
        "ellenzia",
        "briguna",
        "macbook",
        "ragunan",
        "ambon parkir",
        "dana syariah",
        "bonalti kost",
        "kost nada",
        "kost mili",
        "kost andreas",
        "service ac",
        "service AC",
        "arumi motor",
        "alex",
        "meng",
        "imam",
        "warung madura",
    }
)

REPORT_DIR = APP_DIR / "reports"


# ---------------------------------------------------------------------------
# vCard helpers
# ---------------------------------------------------------------------------


def _unfold(raw: str) -> list[str]:
    physical = raw.replace("\r\n", "\n").split("\n")
    logical: list[str] = []
    for line in physical:
        if line[:1] in (" ", "\t") and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return logical


def _prop_name(line: str) -> str:
    if ":" not in line:
        return ""
    head = line.split(":", 1)[0]
    head = head.split(".", 1)[-1]
    return head.split(";", 1)[0].upper()


def _get_prop(logical: list[str], name: str) -> str | None:
    for line in logical:
        if _prop_name(line) == name and ":" in line:
            return line.split(":", 1)[1]
    return None


def _set_or_add_prop(logical: list[str], name: str, value: str) -> None:
    for i, line in enumerate(logical):
        if _prop_name(line) == name:
            head = line.split(":", 1)[0]
            logical[i] = f"{head}:{value}"
            return
    for i, line in enumerate(logical):
        if line.upper().startswith("END:VCARD"):
            logical.insert(i, f"{name}:{value}")
            return
    logical.append(f"{name}:{value}")


def _parse_n(value: str) -> list[str]:
    parts = value.split(";")
    while len(parts) < 5:
        parts.append("")
    return parts[:5]


def _is_abbr_token(token: str) -> bool:
    """True if alphabetic part is ALL-CAPS with >= 2 letters (e.g. BRI, AC)."""
    letters = re.findall(r"[A-Za-z]", token)
    return len(letters) >= 2 and all(ch.isupper() for ch in letters)


def _lower_keep_abbr(value: str) -> str:
    parts = re.split(r"(\s+)", value)
    out: list[str] = []
    for chunk in parts:
        if not chunk or chunk.isspace():
            out.append(chunk)
        elif _is_abbr_token(chunk):
            out.append(chunk)
        else:
            out.append(chunk.lower())
    return "".join(out)


def _norm_affil(inner: str) -> str:
    """Normalize affiliation text: known acronyms → UPPER, else lower-keep-abbr."""
    inner = inner.strip()
    if inner.upper() in ACRONYMS:
        return inner.upper()
    return _lower_keep_abbr(inner)


def _is_paren_affiliation(token: str) -> bool:
    t = token.strip()
    return bool(t.startswith("(") and t.endswith(")") and len(t) > 2)


def _is_bare_acronym(token: str) -> bool:
    t = token.strip()
    return t.isalpha() and t.upper() in ACRONYMS


def _join_given(first: str, middle: str) -> str:
    return " ".join(p for p in (first.strip(), middle.strip()) if p)


def _build_fn(given: str, affil: str | None, emoji: str = "") -> str:
    given = given.strip()
    if affil:
        base = f"{given} ({affil})" if given else f"({affil})"
    else:
        base = given
    if emoji and emoji not in base:
        return f"{base} {emoji}".strip()
    return base


def _strip_trailing_paren(s: str) -> tuple[str, str | None]:
    """Split ``foo (BAR)`` → (``foo``, ``BAR``). No match → (s, None)."""
    m = re.search(r"^(.*)\s*\(([^)]+)\)\s*([✅🤬]*)\s*$", s.strip())
    if not m:
        return s.strip(), None
    given = m.group(1).strip()
    inner = m.group(2).strip()
    return given, inner


def _extract_emoji(s: str) -> str:
    return "".join(re.findall(r"[✅🤬]+", s))


# ---------------------------------------------------------------------------
# Plan model
# ---------------------------------------------------------------------------


@dataclass
class NamePlan:
    contact: Contact
    old_fn: str
    new_fn: str
    old_n: str
    new_n: str
    old_org: str
    new_org: str
    reasons: list[str] = field(default_factory=list)
    new_raw: str = ""

    def to_dict(self) -> dict:
        return {
            "uid": self.contact.uid,
            "old_fn": self.old_fn,
            "new_fn": self.new_fn,
            "old_n": self.old_n,
            "new_n": self.new_n,
            "old_org": self.old_org,
            "new_org": self.new_org,
            "reasons": self.reasons,
        }


# ---------------------------------------------------------------------------
# Per-contact planning (safe)
# ---------------------------------------------------------------------------


def plan_contact(c: Contact) -> NamePlan | None:
    if c.full_name in SKIP_NAMES:
        return None
    raw = c.raw or ""
    if not raw.strip():
        return None

    logical = _unfold(raw)
    old_fn_raw = _get_prop(logical, "FN")
    old_n = _get_prop(logical, "N") or ""
    old_org = _get_prop(logical, "ORG") or ""

    last, first, middle, prefix, suffix = (
        _parse_n(old_n) if old_n else ["", "", "", "", ""]
    )

    # Display: prefer non-empty FN; else reconstruct from N.
    reconstructed = " ".join(p for p in (first, middle, last) if p).strip()
    # Special: when last is affiliation like (BRI), reconstructed display is weird;
    # prefer first+middle + (last) form.
    if _is_paren_affiliation(last):
        reconstructed = _build_fn(_join_given(first, middle), last.strip()[1:-1])
    elif _is_bare_acronym(last):
        reconstructed = _build_fn(_join_given(first, middle), last.upper())

    display = (old_fn_raw or "").strip() or reconstructed or c.full_name
    old_fn_for_report = (old_fn_raw or "").strip() or display

    reasons: list[str] = []
    affil: str | None = None
    emoji = _extract_emoji(display)

    # Working name parts
    w_last, w_first, w_middle = last, first, middle

    # ------------------------------------------------------------------
    # 0) Special rename: a5 → afif (UPI)
    # ------------------------------------------------------------------
    if display.casefold() == "a5" or (
        w_first.casefold() == "a5" and not w_last and not w_middle
    ):
        w_first, w_middle, w_last = "afif", "", ""
        affil = "UPI"
        reasons.append("rename a5 → afif (UPI)")

    # ------------------------------------------------------------------
    # 1) Broken sekar madura)
    #    Raw N: madura);sekar;(warung
    # ------------------------------------------------------------------
    if affil is None and (
        "madura)" in w_last
        or "(warung" in w_middle
        or display in ("sekar madura)", "sekar madura")
    ):
        w_first, w_middle, w_last = "sekar", "", ""
        affil = "warung madura"
        reasons.append("fix broken parentheses sekar madura)")

    # ------------------------------------------------------------------
    # 2) Affiliation sitting in N-last: (BRI) / (SDO) / SD / BRI
    # ------------------------------------------------------------------
    if affil is None and _is_paren_affiliation(w_last):
        affil = _norm_affil(w_last[1:-1])
        w_last = ""
        reasons.append(f"move affiliation from N-last → ({affil})")
    elif affil is None and _is_bare_acronym(w_last):
        # Bare SD/BRI in last IS the affiliation pattern (edi SD, isan SD).
        # Real surnames are not in ACRONYMS.
        affil = w_last.upper()
        w_last = ""
        reasons.append(f"move bare-acronym N-last → ({affil})")

    # ------------------------------------------------------------------
    # 3) FN already ends with (tag) — extract org, clean N-first if stuffed
    # ------------------------------------------------------------------
    if affil is None:
        core_disp = re.sub(r"[✅🤬]+", "", display).strip()
        given_from_fn, inner = _strip_trailing_paren(core_disp)
        if inner is not None:
            inner_norm = _norm_affil(inner)
            is_known = (
                inner_norm.upper() in ACRONYMS
                or inner.casefold() in {t.casefold() for t in KNOWN_TAGS}
                or inner_norm.casefold() in {t.casefold() for t in KNOWN_TAGS}
            )
            if is_known:
                affil = inner_norm
                # If N-first contains the whole "name (tag)", split it.
                if "(" in w_first and ")" in w_first:
                    g, _ = _strip_trailing_paren(w_first)
                    parts = g.split()
                    w_first = parts[0] if parts else g
                    w_middle = " ".join(parts[1:]) if len(parts) > 1 else ""
                    reasons.append(
                        f"split stuffed N-first; extract FN affiliation → ({affil})"
                    )
                else:
                    reasons.append(f"extract existing FN affiliation → ({affil})")

                # Prefer multi-word given from FN when N only has first token.
                if given_from_fn:
                    gparts = given_from_fn.split()
                    if gparts:
                        if (
                            not w_first
                            or w_first.casefold() == gparts[0].casefold()
                            or "(" in first
                        ):
                            w_first = gparts[0]
                            w_middle = " ".join(gparts[1:]) if len(gparts) > 1 else w_middle

    # ------------------------------------------------------------------
    # 4) Outside-paren acronym wrap (strict)
    # ------------------------------------------------------------------
    if affil is None:
        core = re.sub(r"[✅🤬]+", "", display).strip()
        if not re.search(r"\([^)]+\)", core):  # no existing parens
            tokens = core.split()
            # Trailing acronym: edi SD, toa UPI, customer care DANA
            if len(tokens) >= 2 and tokens[-1].upper() in OUTSIDE_WRAP:
                affil = tokens[-1].upper()
                given = " ".join(tokens[:-1])
                gparts = given.split()
                w_first = gparts[0] if gparts else ""
                w_middle = " ".join(gparts[1:]) if len(gparts) > 1 else ""
                w_last = ""
                reasons.append(f"wrap trailing acronym → ({affil})")
            # Leading acronym: DBS laundry
            elif len(tokens) >= 2 and tokens[0].upper() in OUTSIDE_WRAP:
                affil = tokens[0].upper()
                given = " ".join(tokens[1:])
                gparts = given.split()
                w_first = gparts[0] if gparts else ""
                w_middle = " ".join(gparts[1:]) if len(gparts) > 1 else ""
                w_last = ""
                reasons.append(f"wrap leading acronym → ({affil})")

    # ------------------------------------------------------------------
    # If still no structural reason: optionally fill empty FN only.
    # ------------------------------------------------------------------
    if not reasons:
        if not (old_fn_raw or "").strip() and reconstructed:
            new_fn_only = _lower_keep_abbr(reconstructed)
            # Avoid inventing affiliation-looking FN from bare N last acronym
            # (those should have been caught above). For normal multi-word names
            # just fill FN.
            new_logical = list(logical)
            _set_or_add_prop(new_logical, "FN", new_fn_only)
            new_raw = "\r\n".join(new_logical)
            if not new_raw.endswith("\r\n"):
                new_raw += "\r\n"
            if new_fn_only != old_fn_for_report:
                return NamePlan(
                    contact=c,
                    old_fn=old_fn_for_report or "(empty FN)",
                    new_fn=new_fn_only,
                    old_n=old_n,
                    new_n=old_n,
                    old_org=old_org,
                    new_org=old_org,
                    reasons=["fill empty FN from N"],
                    new_raw=new_raw,
                )
        return None

    # ------------------------------------------------------------------
    # Build final FN / N / ORG from working parts
    # ------------------------------------------------------------------
    w_first = _lower_keep_abbr(w_first)
    w_middle = _lower_keep_abbr(w_middle)
    if w_last and not _is_paren_affiliation(w_last) and not _is_bare_acronym(w_last):
        w_last = _lower_keep_abbr(w_last)
    else:
        w_last = ""

    given_final = _join_given(w_first, w_middle)
    if not given_final:
        given_final = re.sub(
            r"\s*\([^)]*\)\s*$", "", re.sub(r"[✅🤬]+", "", display)
        ).strip()
        given_final = _lower_keep_abbr(given_final)
        parts = given_final.split()
        w_first = parts[0] if parts else ""
        w_middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        given_final = _join_given(w_first, w_middle)

    new_fn = _build_fn(given_final, affil, emoji)
    new_n = ";".join([w_last, w_first, w_middle, prefix, suffix])

    new_org = old_org
    if affil:
        clean_org = old_org.strip().strip(";")
        if not clean_org:
            new_org = affil
            reasons.append(f"set ORG={affil}")
        elif affil.casefold() not in clean_org.casefold():
            new_org = f"{affil};{clean_org}"
            reasons.append(f"prepend ORG={affil}")

    if new_fn == old_fn_for_report and new_n == old_n and new_org == old_org:
        return None

    new_logical = list(logical)
    _set_or_add_prop(new_logical, "FN", new_fn)
    _set_or_add_prop(new_logical, "N", new_n)
    if new_org != old_org:
        _set_or_add_prop(new_logical, "ORG", new_org)

    new_raw = "\r\n".join(new_logical)
    if not new_raw.endswith("\r\n"):
        new_raw += "\r\n"

    return NamePlan(
        contact=c,
        old_fn=old_fn_for_report,
        new_fn=new_fn,
        old_n=old_n,
        new_n=new_n,
        old_org=old_org,
        new_org=new_org,
        reasons=reasons,
        new_raw=new_raw,
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _norm_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p)
    if digits.startswith("62"):
        digits = "0" + digits[2:]
    if digits.startswith("8") and len(digits) >= 9:
        digits = "0" + digits
    return digits


def _extract_from_raw(raw: str, prop: str) -> list[str]:
    out: list[str] = []
    for line in _unfold(raw or ""):
        if _prop_name(line) == prop and ":" in line:
            out.append(line.split(":", 1)[1].strip())
    return out


def build_reports(contacts: list[Contact], report_dir: Path) -> dict:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp_slug()

    empty = []
    for c in contacts:
        phones = _extract_from_raw(c.raw, "TEL") or c.phones
        emails = _extract_from_raw(c.raw, "EMAIL") or c.emails
        if not phones and not emails:
            note = ""
            if c.full_name in SKIP_NAMES:
                note = "KEEP — user requested"
            elif c.full_name == "a5":
                note = "rename target a5 → afif (UPI)"
            empty.append(
                {
                    "uid": c.uid,
                    "full_name": c.full_name,
                    "first": c.first,
                    "last": c.last,
                    "org": c.org,
                    "protected": c.full_name in SKIP_NAMES,
                    "note": note,
                }
            )
    empty_path = report_dir / f"empty-contacts-{stamp}.json"
    empty_path.write_text(json.dumps(empty, indent=2, ensure_ascii=False) + "\n")
    empty_md = report_dir / f"empty-contacts-{stamp}.md"
    lines = [
        f"# Empty contacts (no phone, no email) — {stamp}",
        "",
        f"Total: **{len(empty)}**",
        "",
        "Protected / special:",
        "- `my life 🤬🤬` — KEEP (user request)",
        "- `fake` — KEEP (user request)",
        "- `a5` — rename to `afif (UPI)` (user request; not deleted)",
        "",
        "| # | Name | UID | Note |",
        "|---|------|-----|------|",
    ]
    for i, e in enumerate(empty, 1):
        lines.append(
            f"| {i} | `{e['full_name']}` | `{e['uid'][:8]}…` | {e['note']} |"
        )
    empty_md.write_text("\n".join(lines) + "\n")

    by_name: dict[str, list[Contact]] = defaultdict(list)
    by_phone: dict[str, list[Contact]] = defaultdict(list)
    by_email: dict[str, list[Contact]] = defaultdict(list)
    for c in contacts:
        by_name[c.full_name.strip().casefold()].append(c)
        for p in _extract_from_raw(c.raw, "TEL") or c.phones:
            np = _norm_phone(p)
            if len(np) >= 8:
                by_phone[np].append(c)
        for e in _extract_from_raw(c.raw, "EMAIL") or c.emails:
            by_email[e.lower()].append(c)

    name_dups = {
        k: [{"uid": c.uid, "full_name": c.full_name} for c in v]
        for k, v in by_name.items()
        if len({c.uid for c in v}) > 1
    }
    phone_dups = {
        k: [{"uid": c.uid, "full_name": c.full_name} for c in v]
        for k, v in by_phone.items()
        if len({c.uid for c in v}) > 1
    }
    email_dups = {
        k: [{"uid": c.uid, "full_name": c.full_name} for c in v]
        for k, v in by_email.items()
        if len({c.uid for c in v}) > 1
    }

    dup_report = {
        "generated": stamp,
        "same_name": name_dups,
        "same_phone": phone_dups,
        "same_email": email_dups,
    }
    dup_path = report_dir / f"duplicate-contacts-{stamp}.json"
    dup_path.write_text(json.dumps(dup_report, indent=2, ensure_ascii=False) + "\n")
    dup_md = report_dir / f"duplicate-contacts-{stamp}.md"
    md = [f"# Duplicate contacts report — {stamp}", ""]
    md.append(f"## Same display name ({len(name_dups)} groups)")
    md.append("")
    if not name_dups:
        md.append("_None_")
    for _, items in sorted(name_dups.items(), key=lambda kv: kv[1][0]["full_name"]):
        md.append(f"- **{items[0]['full_name']}** ×{len(items)}")
        for it in items:
            md.append(f"  - `{it['uid']}`")
    md.append("")
    md.append(f"## Same phone number ({len(phone_dups)} groups)")
    md.append("")
    if not phone_dups:
        md.append("_None_")
    for k, items in sorted(phone_dups.items()):
        names = ", ".join(f"`{it['full_name']}`" for it in items)
        md.append(f"- **{k}**: {names}")
        for it in items:
            md.append(f"  - `{it['uid']}`")
    md.append("")
    md.append(f"## Same email ({len(email_dups)} groups)")
    md.append("")
    if not email_dups:
        md.append("_None_")
    for k, items in sorted(email_dups.items()):
        names = ", ".join(f"`{it['full_name']}`" for it in items)
        md.append(f"- **{k}**: {names}")
        for it in items:
            md.append(f"  - `{it['uid']}`")
    md.append("")
    md.append("## Recommended merges (manual)")
    md.append("")
    md.append(
        "- Phone `089654573424` shared by `desi yo nee` and `eci` — likely same person."
    )
    md.append(
        "- Same-name groups (`erika`, `sonia (UPI)`, `pak (SDO)`) — verify before merge."
    )
    dup_md.write_text("\n".join(md) + "\n")

    return {
        "empty_json": str(empty_path),
        "empty_md": str(empty_md),
        "empty_count": len(empty),
        "dup_json": str(dup_path),
        "dup_md": str(dup_md),
        "name_dup_groups": len(name_dups),
        "phone_dup_groups": len(phone_dups),
        "email_dup_groups": len(email_dups),
    }


def backup_originals(contacts: list[Contact], label: str) -> Path:
    root = BACKUP_DIR / f"contacts-{label}-{timestamp_slug()}"
    root.mkdir(parents=True, exist_ok=True)
    for c in contacts:
        (root / f"{c.uid}.vcf").write_text(c.raw or "")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Push name/structure fixes to iCloud (default: dry-run).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--reports-only",
        action="store_true",
        help="Only write empty/duplicate reports; skip name planning.",
    )
    args = parser.parse_args()

    started = time.monotonic()
    console.print("[cyan][start][/cyan] connecting to iCloud CardDAV…")
    session = ContactsSession.connect()
    contacts = session.list_contacts()
    total = len(contacts)
    console.print(f"[cyan][info][/cyan] fetched {total} contact(s)")

    console.print("[cyan][reports][/cyan] building empty + duplicate reports…")
    report_info = build_reports(contacts, REPORT_DIR)
    console.print(
        f"[cyan][reports][/cyan] empty={report_info['empty_count']} → "
        f"{report_info['empty_md']}"
    )
    console.print(
        f"[cyan][reports][/cyan] dups name={report_info['name_dup_groups']} "
        f"phone={report_info['phone_dup_groups']} "
        f"email={report_info['email_dup_groups']} → {report_info['dup_md']}"
    )

    if args.reports_only:
        console.print("[green]Reports only — done.[/green]")
        return 0

    planned: list[NamePlan] = []
    skipped_protected = 0
    for c in contacts:
        if c.full_name in SKIP_NAMES:
            skipped_protected += 1
            if args.verbose:
                console.print(f"  skip protected {c.full_name!r}")
            continue
        plan = plan_contact(c)
        if plan is None:
            continue
        planned.append(plan)
        visible = plan.old_fn != plan.new_fn
        arrow = "→" if visible else "·"
        style_old = "yellow" if visible else "dim"
        style_new = "green" if visible else "dim"
        console.print(
            f"  change [{style_old}]{plan.old_fn!r}[/{style_old}] "
            f"{arrow} [{style_new}]{plan.new_fn!r}[/{style_new}]"
        )
        if args.verbose:
            console.print(f"         N: {plan.old_n!r} → {plan.new_n!r}")
            console.print(f"         ORG: {plan.old_org!r} → {plan.new_org!r}")
            console.print(f"         reasons: {', '.join(plan.reasons)}")

    visible_n = sum(1 for p in planned if p.old_fn != p.new_fn)
    console.rule(
        f"{len(planned)} contact(s) would change "
        f"({visible_n} visible FN, protected skips: {skipped_protected})"
    )

    plan_path = REPORT_DIR / f"name-cleanup-plan-{timestamp_slug()}.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps([p.to_dict() for p in planned], indent=2, ensure_ascii=False) + "\n"
    )
    console.print(f"[cyan][plan][/cyan] {plan_path}")

    console.print("[cyan][safety][/cyan] visible FN changes:")
    for p in planned:
        if p.old_fn != p.new_fn:
            console.print(f"  • {p.old_fn!r} → {p.new_fn!r}")

    if not planned:
        console.print("[green]Nothing to change.[/green]")
        return 0

    if not args.apply:
        console.print(
            "[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to push "
            "(originals backed up first)."
        )
        return 0

    console.print("[cyan][backup][/cyan] saving original vCards…")
    pristine = {c.uid: c for c in session.list_contacts()}
    backup_list = [
        pristine[p.contact.uid] for p in planned if p.contact.uid in pristine
    ]
    backup_dir = backup_originals(backup_list, "name-cleanup")
    console.print(f"[cyan][backup][/cyan] {len(backup_list)} vCard(s) → {backup_dir}")

    ok = 0
    fail = 0
    n = len(planned)
    for i, p in enumerate(planned, 1):
        pct = int(i / n * 100)
        live = pristine.get(p.contact.uid, p.contact)
        try:
            if not live.href:
                raise RuntimeError("missing href")
            session._client.put(  # noqa: SLF001
                live.href,
                p.new_raw,
                content_type="text/vcard; charset=utf-8",
                etag=live.etag,
            )
            ok += 1
            console.print(f"[{i}/{n}] {pct}% — [green]updated[/green] {p.new_fn!r}")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            console.print(f"[{i}/{n}] {pct}% — [red]FAILED[/red] {p.old_fn!r}: {exc}")

    elapsed = time.monotonic() - started
    console.print(
        f"[cyan][done][/cyan] updated {ok}, failed {fail}, "
        f"backup at {backup_dir} ({elapsed:.1f}s)"
    )
    console.print(
        f"[cyan][reports][/cyan] empty={report_info['empty_md']} | "
        f"dups={report_info['dup_md']}"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
