"""Honest HealthKit stub.

Apple Health reads require a signed native helper with HealthKit entitlements.
This module never scrapes Health.app and never pretends unsigned reads work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HEALTH_HELPER_NAME = "icloudseal-health"


class HealthError(RuntimeError):
    """Raised when Health is requested but the signed helper is not available."""


def _helper_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "bin" / HEALTH_HELPER_NAME


def health_status() -> dict[str, Any]:
    helper = _helper_path()
    return {
        "ok": False,
        "helper": str(helper),
        "helperExists": helper.is_file(),
        "reason": (
            "HealthKit requires a signed native helper with HealthKit entitlements. "
            "icloudseal will not scrape Health.app or fake unsigned reads."
        ),
    }


def read_samples(*, kind: str, days: int = 7) -> dict[str, Any]:
    del kind, days
    status = health_status()
    raise HealthError(status["reason"])
