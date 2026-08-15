"""Open-Meteo forecast + geocoding.

Hosts are hardcoded. User input is only a place name or coordinates — never a
URL. Reads do not open Weather.app and do not use device location.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

FORECAST_HOST = "api.open-meteo.com"
GEOCODE_HOST = "geocoding-api.open-meteo.com"
ALLOWED_HOSTS = frozenset({FORECAST_HOST, GEOCODE_HOST})
USER_AGENT = "icloudseal-mcp"
TIMEOUT_SEC = 15
MAX_PLACE_LEN = 200
MIN_DAYS = 1
MAX_DAYS = 7
DEFAULT_DAYS = 3
ALLOWED_TEMP_UNITS = frozenset({"celsius", "fahrenheit"})
ATTRIBUTION = "Weather data by Open-Meteo.com"

CURRENT_FIELDS = "temperature_2m,weather_code,wind_speed_10m,precipitation"
DAILY_FIELDS = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"

WMO_TEXT = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

UrlOpen = Callable[..., Any]


class WeatherError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        raise WeatherError("refusing HTTP redirect")


def urlopen(req: urllib.request.Request, timeout: float = TIMEOUT_SEC) -> Any:
    opener = urllib.request.build_opener(_NoRedirect())
    return opener.open(req, timeout=timeout)


def wmo_text(code: int | None) -> str | None:
    if code is None:
        return None
    return WMO_TEXT.get(code, f"WMO {code}")


def _reject_controls(value: str, name: str) -> str:
    if any(ch in value for ch in "\r\n\x00"):
        raise WeatherError(f"{name} must not contain control characters.")
    return value


def _as_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise WeatherError(f"{name} must be finite.")
    return number


def validate_coords(latitude: object, longitude: object) -> tuple[float, float]:
    lat = _as_float(latitude, "latitude")
    lon = _as_float(longitude, "longitude")
    if lat < -90 or lat > 90:
        raise WeatherError("latitude must be between -90 and 90.")
    if lon < -180 or lon > 180:
        raise WeatherError("longitude must be between -180 and 180.")
    return lat, lon


def validate_place(place: str) -> str:
    candidate = _reject_controls((place or "").strip(), "place")
    if not candidate:
        raise WeatherError("place is required.")
    if len(candidate) > MAX_PLACE_LEN:
        raise WeatherError(f"place is limited to {MAX_PLACE_LEN} characters.")
    return candidate


def validate_days(days: object) -> int:
    if isinstance(days, bool) or not isinstance(days, int):
        raise WeatherError("days must be an integer.")
    if days < MIN_DAYS or days > MAX_DAYS:
        raise WeatherError(f"days must be between {MIN_DAYS} and {MAX_DAYS}.")
    return days


def validate_temperature_unit(unit: str) -> str:
    candidate = (unit or "").strip().lower()
    if candidate not in ALLOWED_TEMP_UNITS:
        raise WeatherError("temperature_unit must be celsius or fahrenheit.")
    return candidate


def _request(url: str, *, opener: UrlOpen) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise WeatherError("internal: refusing non-Open-Meteo host.")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read()
    except WeatherError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WeatherError(f"Open-Meteo request failed: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WeatherError("Open-Meteo returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise WeatherError("Open-Meteo returned an unexpected payload.")
    return payload


def geocode(place: str, *, opener: UrlOpen | None = None) -> dict[str, Any]:
    name = validate_place(place)
    query = urllib.parse.urlencode(
        {"name": name, "count": 1, "language": "en", "format": "json"}
    )
    url = f"https://{GEOCODE_HOST}/v1/search?{query}"
    data = _request(url, opener=opener or urlopen)
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise WeatherError(f"No matching place for {name!r}.")
    first = results[0]
    if not isinstance(first, dict):
        raise WeatherError("Open-Meteo geocoding returned an unexpected payload.")
    try:
        lat, lon = validate_coords(first.get("latitude"), first.get("longitude"))
    except WeatherError as exc:
        raise WeatherError("Open-Meteo geocoding returned invalid coordinates.") from exc
    return {
        "name": first.get("name") or name,
        "country": first.get("country"),
        "admin1": first.get("admin1"),
        "latitude": lat,
        "longitude": lon,
        "timezone": first.get("timezone"),
    }


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _daily_rows(daily: dict[str, Any]) -> list[dict[str, Any]]:
    times = daily.get("time") or []
    if not isinstance(times, list):
        return []
    codes = daily.get("weather_code") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(times):
        code = _optional_int(codes[index]) if index < len(codes) else None
        rows.append(
            {
                "date": day,
                "weather_code": code,
                "condition": wmo_text(code),
                "temperature_max": (
                    _optional_number(tmax[index]) if index < len(tmax) else None
                ),
                "temperature_min": (
                    _optional_number(tmin[index]) if index < len(tmin) else None
                ),
                "precipitation_sum": (
                    _optional_number(precip[index]) if index < len(precip) else None
                ),
            }
        )
    return rows


def forecast(
    *,
    place: str | None = None,
    latitude: object | None = None,
    longitude: object | None = None,
    days: object = DEFAULT_DAYS,
    temperature_unit: str = "celsius",
    opener: UrlOpen | None = None,
) -> dict[str, Any]:
    """Current conditions plus a short daily forecast."""
    has_place = bool((place or "").strip())
    has_any_coord = latitude is not None or longitude is not None
    if has_place == has_any_coord:
        raise WeatherError("Provide either place or latitude+longitude, not both.")
    if has_any_coord and (latitude is None or longitude is None):
        raise WeatherError("latitude and longitude are both required.")

    forecast_days = validate_days(days)
    unit = validate_temperature_unit(temperature_unit)
    resolved: dict[str, Any] | None = None
    if has_place:
        resolved = geocode(place or "", opener=opener)
        lat, lon = resolved["latitude"], resolved["longitude"]
    else:
        lat, lon = validate_coords(latitude, longitude)

    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current": CURRENT_FIELDS,
            "daily": DAILY_FIELDS,
            "forecast_days": forecast_days,
            "timezone": "auto",
            "temperature_unit": unit,
        }
    )
    url = f"https://{FORECAST_HOST}/v1/forecast?{query}"
    data = _request(url, opener=opener or urlopen)
    current = data.get("current") if isinstance(data.get("current"), dict) else {}
    daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
    current_units = (
        data.get("current_units") if isinstance(data.get("current_units"), dict) else {}
    )
    daily_units = data.get("daily_units") if isinstance(data.get("daily_units"), dict) else {}
    code = _optional_int(current.get("weather_code"))
    place_out = resolved or {
        "name": None,
        "country": None,
        "admin1": None,
        "latitude": lat,
        "longitude": lon,
        "timezone": data.get("timezone"),
    }
    if resolved is None and data.get("timezone"):
        place_out = {**place_out, "timezone": data.get("timezone")}
    return {
        "place": place_out,
        "current": {
            "time": current.get("time"),
            "temperature": _optional_number(current.get("temperature_2m")),
            "weather_code": code,
            "condition": wmo_text(code),
            "wind_speed": _optional_number(current.get("wind_speed_10m")),
            "precipitation": _optional_number(current.get("precipitation")),
        },
        "daily": _daily_rows(daily),
        "units": {
            "temperature": current_units.get("temperature_2m")
            or daily_units.get("temperature_2m_max"),
            "wind_speed": current_units.get("wind_speed_10m"),
            "precipitation": current_units.get("precipitation")
            or daily_units.get("precipitation_sum"),
        },
        "timezone": data.get("timezone") or place_out.get("timezone"),
        "attribution": ATTRIBUTION,
    }
