#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="python3"

# Ensure common binary directories like ~/.local/bin are in PATH
if [ -d "$HOME/.local/bin" ] && [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v "$PYTHON_CMD" &>/dev/null; then
    echo "Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

exec "$PYTHON_CMD" "$SCRIPT_DIR/agy_acp.py" "${@:-setup}"
