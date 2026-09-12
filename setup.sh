#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="python3"

if ! command -v "$PYTHON_CMD" &>/dev/null; then
    echo "Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

exec "$PYTHON_CMD" "$SCRIPT_DIR/agy_acp.py" "${@:-setup}"
