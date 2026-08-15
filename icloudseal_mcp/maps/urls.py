"""Documented Apple Maps URLs.

Reads only construct ``https://maps.apple.com/?…``. Opening Maps.app is a
separate gated action that calls ``/usr/bin/open`` on a frozen https URL.
``maps:`` / ``javascript:`` / ``file:`` / ``data:`` are rejected.
"""

from __future__ import annotations

import math
import subprocess
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

MAPS_HOST = "maps.apple.com"
ALLOWED_DIRFLG = frozenset({"d", "w", "r"})
MAX_TEXT_LEN = 200


class MapsError(RuntimeError):
    pass


def _reject_controls(value: str, name: str) -> str:
    if any(ch in value for ch in "\r\n\x00"):
        raise MapsError(f"{name} must not contain control characters.")
    return value


def _clean_text(value: str | None, name: str) -> str:
    candidate = _reject_controls((value or "").strip(), name)
    if not candidate:
        raise MapsError(f"{name} is required.")
    if len(candidate) > MAX_TEXT_LEN:
        raise MapsError(f"{name} is limited to {MAX_TEXT_LEN} characters.")
    return candidate


def _as_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapsError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise MapsError(f"{name} must be finite.")
    return number


def validate_coords(latitude: object, longitude: object) -> tuple[float, float]:
    lat = _as_float(latitude, "latitude")
    lon = _as_float(longitude, "longitude")
    if lat < -90 or lat > 90:
        raise MapsError("latitude must be between -90 and 90.")
    if lon < -180 or lon > 180:
        raise MapsError("longitude must be between -180 and 180.")
    return lat, lon


def _pack(url: str, mode: str, query: dict[str, str]) -> dict[str, Any]:
    return {
        "url": url,
        "scheme": "https",
        "host": MAPS_HOST,
        "mode": mode,
        "query": query,
    }


def build_search_url(
    query: str,
    *,
    latitude: object | None = None,
    longitude: object | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {"q": _clean_text(query, "query")}
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            raise MapsError("latitude and longitude are both required.")
        lat, lon = validate_coords(latitude, longitude)
        params["ll"] = f"{lat},{lon}"
    url = f"https://{MAPS_HOST}/?{urlencode(params)}"
    return _pack(url, "search", params)


def build_directions_url(
    *,
    daddr: str,
    saddr: str | None = None,
    dirflg: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {"daddr": _clean_text(daddr, "daddr")}
    if saddr:
        params["saddr"] = _clean_text(saddr, "saddr")
    if dirflg:
        flag = (dirflg or "").strip().lower()
        if flag not in ALLOWED_DIRFLG:
            raise MapsError("dirflg must be d (drive), w (walk), or r (transit).")
        params["dirflg"] = flag
    url = f"https://{MAPS_HOST}/?{urlencode(params)}"
    return _pack(url, "directions", params)


def validate_maps_url(url: str) -> str:
    """Accept only https://maps.apple.com/?… with no userinfo or controls."""
    candidate = _reject_controls((url or "").strip(), "url")
    if not candidate:
        raise MapsError("URL is required.")
    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise MapsError("Maps URL must use https.")
    if parsed.hostname != MAPS_HOST:
        raise MapsError("Maps URL host must be maps.apple.com.")
    if parsed.username or parsed.password:
        raise MapsError("Maps URL must not include userinfo.")
    if parsed.path not in {"", "/"}:
        raise MapsError("Maps URL path must be empty.")
    if parsed.fragment:
        raise MapsError("Maps URL must not include a fragment.")
    if not parsed.query:
        raise MapsError("Maps URL must include a query.")
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if not params:
        raise MapsError("Maps URL must include a query.")
    return candidate


def open_maps_url(url: str) -> None:
    canonical = validate_maps_url(url)
    result = subprocess.run(
        ["/usr/bin/open", canonical],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MapsError(result.stderr.strip() or "open failed")
