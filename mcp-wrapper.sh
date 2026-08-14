#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE=0
for arg in "$@"; do
  case "$arg" in
    -v|--verbose) VERBOSE=1 ;;
    -h|--help)
      printf 'Usage: mcp-wrapper.sh [--verbose|-v]\n'
      printf 'Runs the icloudseal-mcp stdio MCP server (Python).\n'
      exit 0
      ;;
    *) printf 'icloudseal-mcp-wrapper: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  PYTHON_BIN="$(command -v python3.13 || command -v python3 || true)"
  [[ -n "$PYTHON_BIN" ]] || {
    printf 'icloudseal-mcp-wrapper: python3 is required.\n' >&2
    exit 127
  }
  printf '%s script=icloudseal-mcp-wrapper pid=%s event=bootstrap detail=creating venv\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" >&2
  "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv"
  VENV_PY="$SCRIPT_DIR/.venv/bin/python"
fi

# Ensure the editable package and compatible MCP SDK are importable.
if ! "$VENV_PY" -c 'from importlib.metadata import version; from icloudseal_mcp.mcp import server; assert version("mcp").split(".", 1)[0] == "2"' >/dev/null 2>&1; then
  printf '%s script=icloudseal-mcp-wrapper pid=%s event=bootstrap detail=pip install -e .[mcp]\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" >&2
  "$VENV_PY" -m pip install -q -U pip
  # Prefer extras if declared; fall back to base + mcp.
  if ! "$VENV_PY" -m pip install -q -e "$SCRIPT_DIR[mcp]"; then
    "$VENV_PY" -m pip install -q -e "$SCRIPT_DIR" "mcp>=2.0,<3"
  fi
fi

export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
export ICLOUDSEAL_PROJECT_ROOT="$SCRIPT_DIR"

printf '%s script=icloudseal-mcp-wrapper pid=%s event=start detail=python=%s verbose=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" "$VENV_PY" "$VERBOSE" >&2

exec "$VENV_PY" -m icloudseal_mcp.mcp.server
