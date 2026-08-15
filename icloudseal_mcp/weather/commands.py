"""Weather commands (Open-Meteo). Reads only — never opens Weather.app."""

from __future__ import annotations

import argparse
import json

from ..common import console
from . import client
from .client import WeatherError


def _guard(fn):
    try:
        return fn()
    except WeatherError as exc:
        console.print(f"[red]Weather error:[/red] {exc}")
        return None


def _print_forecast(payload: dict) -> None:
    place = payload.get("place") or {}
    label = place.get("name") or f"{place.get('latitude')}, {place.get('longitude')}"
    extras = [part for part in (place.get("admin1"), place.get("country")) if part]
    if extras:
        label = f"{label} ({', '.join(extras)})"
    console.rule(str(label))
    current = payload.get("current") or {}
    units = payload.get("units") or {}
    temp_u = units.get("temperature") or ""
    wind_u = units.get("wind_speed") or ""
    precip_u = units.get("precipitation") or ""
    if current.get("temperature") is not None:
        condition = current.get("condition") or "Unknown"
        console.print(f"{current['temperature']}{temp_u} — {condition}")
    if current.get("wind_speed") is not None:
        console.print(f"wind: {current['wind_speed']}{wind_u}")
    if current.get("precipitation") is not None:
        console.print(f"precip: {current['precipitation']}{precip_u}")
    for day in payload.get("daily") or []:
        console.print(
            f"{day.get('date')}: {day.get('temperature_min')}–{day.get('temperature_max')}"
            f"{temp_u} {day.get('condition') or ''}"
        )
    for hour in payload.get("hourly") or []:
        console.print(
            f"{hour.get('time')}: {hour.get('temperature')}{temp_u} "
            f"{hour.get('condition') or ''}"
        )
    console.print(f"[dim]{payload.get('attribution')}[/dim]")


def cmd_forecast(args: argparse.Namespace) -> int:
    has_place = bool(args.place)
    has_coords = args.lat is not None or args.lon is not None
    if has_place == has_coords:
        console.print("[red]Provide either --place or both --lat and --lon.[/red]")
        return 2
    payload = _guard(
        lambda: client.forecast(
            place=args.place,
            latitude=args.lat,
            longitude=args.lon,
            days=args.days,
            temperature_unit=args.unit,
            hourly=args.hourly,
        )
    )
    if payload is None:
        return 2
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _print_forecast(payload)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser(
        "forecast",
        help="Current weather plus a short daily forecast (Open-Meteo).",
    )
    sp.add_argument("--place", help="Place name to geocode (public Open-Meteo).")
    sp.add_argument("--lat", type=float, help="Latitude (-90..90).")
    sp.add_argument("--lon", type=float, help="Longitude (-180..180).")
    sp.add_argument("--days", type=int, default=client.DEFAULT_DAYS)
    sp.add_argument(
        "--unit",
        choices=sorted(client.ALLOWED_TEMP_UNITS),
        default="celsius",
    )
    sp.add_argument("--hourly", action="store_true", help="Include hourly rows.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_forecast)


__all__ = ["register"]
