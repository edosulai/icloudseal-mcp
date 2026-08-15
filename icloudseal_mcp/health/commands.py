"""Health commands. Fail-closed until a signed HealthKit helper exists."""

from __future__ import annotations

import argparse
import json

from ..common import console
from .helper import health_status


def cmd_status(args: argparse.Namespace) -> int:
    payload = health_status()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    console.rule("Health")
    console.print(f"ok: {payload['ok']}")
    console.print(f"helper exists: {payload['helperExists']}")
    console.print(payload["reason"])
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    sp = sub.add_parser("status", help="Report HealthKit helper status (always fail-closed).")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)


__all__ = ["register"]
