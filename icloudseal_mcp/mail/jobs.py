"""Job-alert extraction and scoring.

This module keeps job automation in a review-first shape: extract leads from
mail alerts, score them locally, and write a plan that a human/agent can review
before any apply/archive/delete workflow is considered.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from email.message import Message
from urllib.parse import unquote

JOB_SENDER_HINTS = (
    "jobstreet",
    "linkedin",
    "indeed",
)

POSITIVE_KEYWORDS = {
    "remote": 14,
    "work from home": 14,
    "wfh": 14,
    "hybrid": 6,
    "frontend": 12,
    "front end": 12,
    "full stack": 12,
    "fullstack": 12,
    "software engineer": 12,
    "developer": 10,
    "engineer": 8,
    "javascript": 8,
    "typescript": 8,
    "react": 8,
    "next.js": 7,
    "node": 7,
    "ai": 6,
    "genai": 8,
    "llm": 8,
    "senior": 5,
    "lead": 6,
}

NEGATIVE_KEYWORDS = {
    "telemarketing": -16,
    "sales": -12,
    "admin": -10,
    "administrasi": -10,
    "customer service": -10,
    "intern": -8,
    "magang": -8,
    "driver": -14,
    "warehouse": -10,
    "retail": -8,
}


@dataclass(frozen=True)
class JobLead:
    uid: int
    provider: str
    sender: str
    subject: str
    title: str
    company: str
    location: str
    apply_url: str
    score: int
    reasons: list[str]
    source_excerpt: str

    def to_dict(self) -> dict:
        return asdict(self)


def is_job_alert(sender_email: str, sender_name: str, subject: str) -> bool:
    haystack = " ".join([sender_email, sender_name, subject]).lower()
    return any(hint in haystack for hint in JOB_SENDER_HINTS) or any(
        term in haystack
        for term in ("job alert", "hiring", "lowongan", "pekerjaan", "developer")
    )


def provider_for(sender_email: str, sender_name: str) -> str:
    haystack = f"{sender_email} {sender_name}".lower()
    if "jobstreet" in haystack:
        return "jobstreet"
    if "linkedin" in haystack:
        return "linkedin"
    if "indeed" in haystack:
        return "indeed"
    return "unknown"


def message_text(msg: Message) -> str:
    """Extract readable text, falling back to stripped HTML."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain":
            plain_parts.append(text)
        else:
            html_parts.append(strip_html(text))

    if plain_parts:
        return normalize_text("\n".join(plain_parts))
    return normalize_text("\n".join(html_parts))


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value)


def normalize_text(value: str) -> str:
    lines = []
    for line in value.replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>\]\)\"']+", text):
        url = unquote(match.group(0).rstrip(".,;"))
        if url not in urls:
            urls.append(url)
    return urls


def extract_job_leads(
    *,
    uid: int,
    sender_email: str,
    sender_name: str,
    subject: str,
    msg: Message,
) -> list[JobLead]:
    provider = provider_for(sender_email, sender_name)
    text = message_text(msg)
    urls = extract_urls(text)

    leads = _extract_structured_leads(provider, subject, text, urls)
    if not leads:
        leads = [_fallback_lead(subject, text, urls)]

    out: list[JobLead] = []
    sender = sender_name or sender_email
    for lead in leads[:5]:
        title, company, location, apply_url, excerpt = lead
        score, reasons = score_lead(
            title=title,
            company=company,
            location=location,
            subject=subject,
            text=excerpt,
        )
        out.append(
            JobLead(
                uid=uid,
                provider=provider,
                sender=sender,
                subject=subject,
                title=title,
                company=company,
                location=location,
                apply_url=apply_url,
                score=score,
                reasons=reasons,
                source_excerpt=excerpt[:500],
            )
        )
    return out


def _extract_structured_leads(
    provider: str,
    subject: str,
    text: str,
    urls: list[str],
) -> list[tuple[str, str, str, str, str]]:
    lines = [line for line in text.split("\n") if _is_signal_line(line)]
    leads: list[tuple[str, str, str, str, str]] = []

    if provider == "linkedin":
        for i, line in enumerate(lines):
            if not _looks_like_title(line):
                continue
            company = lines[i + 1] if i + 1 < len(lines) else ""
            location = lines[i + 2] if i + 2 < len(lines) else ""
            if _looks_like_company(company):
                leads.append(
                    (line, company, location, _best_url(urls, "linkedin"), _excerpt(lines, i))
                )

    if provider == "jobstreet":
        for i, line in enumerate(lines):
            if not _looks_like_title(line):
                continue
            company = lines[i + 1] if i + 1 < len(lines) else ""
            location = lines[i + 2] if i + 2 < len(lines) else ""
            if _looks_like_company(company) and _looks_like_location(location):
                leads.append(
                    (line, company, location, _best_url(urls, "jobstreet"), _excerpt(lines, i))
                )

    if provider == "indeed":
        title = subject.split(" at ", 1)[0].strip()
        company = ""
        if " at " in subject:
            company = subject.split(" at ", 1)[1].split(" and ", 1)[0].strip()
        if title:
            leads.append((title, company, "", _best_url(urls, "indeed"), text[:500]))

    # De-duplicate by title/company/location.
    unique: list[tuple[str, str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for lead in leads:
        key = (lead[0].lower(), lead[1].lower(), lead[2].lower())
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


def _fallback_lead(
    subject: str,
    text: str,
    urls: list[str],
) -> tuple[str, str, str, str, str]:
    title = subject
    company = ""
    location = ""

    m = re.search(r"(?P<title>.+?) at (?P<company>[^|,\n]+)", subject, flags=re.I)
    if m:
        title = m.group("title").strip()
        company = m.group("company").strip()

    return (title, company, location, _best_url(urls, ""), text[:500])


def _best_url(urls: list[str], provider: str) -> str:
    if provider:
        for url in urls:
            if provider in url.lower() and any(token in url.lower() for token in ("job", "view")):
                return url
    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in ("job", "career", "apply", "view")):
            return url
    return urls[0] if urls else ""


def score_lead(
    *,
    title: str,
    company: str,
    location: str,
    subject: str,
    text: str,
) -> tuple[int, list[str]]:
    haystack = " ".join([title, company, location, subject, text]).lower()
    score = 20
    reasons: list[str] = []

    for keyword, value in POSITIVE_KEYWORDS.items():
        if keyword in haystack:
            score += value
            reasons.append(f"+{value} {keyword}")
    for keyword, value in NEGATIVE_KEYWORDS.items():
        if keyword in haystack:
            score += value
            reasons.append(f"{value} {keyword}")

    if "jakarta" in haystack:
        score += 4
        reasons.append("+4 jakarta")
    if "apply" in haystack or "lamar" in haystack:
        score += 3
        reasons.append("+3 apply link")

    score = max(0, min(100, score))
    return score, reasons


def _is_signal_line(line: str) -> bool:
    lowered = line.lower()
    if len(line) < 4 or len(line) > 160:
        return False
    noisy = (
        "http",
        "manage alert",
        "unsubscribe",
        "privacy",
        "terms",
        "view job:",
        "logo",
        "jobstreet",
        "linkedin",
        "indeed",
        "hai edo",
    )
    return not any(token in lowered for token in noisy)


def _looks_like_title(line: str) -> bool:
    lowered = line.lower()
    return any(
        token in lowered
        for token in (
            "developer",
            "engineer",
            "frontend",
            "front end",
            "full stack",
            "fullstack",
            "software",
            "javascript",
            "typescript",
            "genai",
            "ai ",
            "architect",
            "analyst",
        )
    )


def _looks_like_company(line: str) -> bool:
    lowered = line.lower()
    blocked = ("remote", "jakarta", "apply", "view", "profile", "resume")
    return bool(line) and not any(token in lowered for token in blocked)


def _looks_like_location(line: str) -> bool:
    lowered = line.lower()
    return any(
        token in lowered
        for token in (
            "jakarta",
            "remote",
            "hybrid",
            "indonesia",
            "gambir",
            "bandung",
            "surabaya",
            "tangerang",
            "bekasi",
            "yogyakarta",
        )
    )


def _excerpt(lines: list[str], index: int) -> str:
    return "\n".join(lines[index : index + 5])
