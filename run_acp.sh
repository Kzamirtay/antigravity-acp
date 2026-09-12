#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Force execute permissions on harness if missing
if [ -f "$SCRIPT_DIR/localharness_external" ] && [ ! -x "$SCRIPT_DIR/localharness_external" ]; then
    chmod 755 "$SCRIPT_DIR/localharness_external" 2>/dev/null || true
fi

# Prefer IPv4 in environments like WSL2 where IPv6 drops packets/times out
if [ -f "$SCRIPT_DIR/libforce_ipv4.so" ]; then
    export LD_PRELOAD="$SCRIPT_DIR/libforce_ipv4.so${LD_PRELOAD:+:$LD_PRELOAD}"
fi

exec "$SCRIPT_DIR/agy_acp_server.par" "--uid=" "$@"
