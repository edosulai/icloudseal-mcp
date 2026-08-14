"""MCP surface for icloudseal-mcp (stdio tools + Touch ID approval)."""

from __future__ import annotations

__all__ = ["run"]


def run() -> None:
    from .server import main

    main()
