#!/bin/sh
# heartbeat.example.sh — give liveness-scan a SHARP activity signal for this worktree.
#
# liveness-scan reads a per-worktree heartbeat file by EXISTENCE + mtime only. Touch it at
# session start (alongside your scope-lease acquire) and periodically while the agent works;
# liveness-scan then distinguishes a running session from a stalled one far more precisely
# than the commit-mtime fallback can.
#
# Point LIVENESS_SCAN_HEARTBEAT_RELPATH at the same relative path (e.g. .agent/heartbeat).
# Keep the file gitignored — it is liveness exhaust, not source.
#
# Usage:
#   ./heartbeat.example.sh                 # touch once (e.g. at session start)
#   ./heartbeat.example.sh --loop 30       # touch every 30s in the background until killed

set -eu

REL="${LIVENESS_SCAN_HEARTBEAT_RELPATH:-.agent/heartbeat}"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not in a git repo" >&2; exit 1; }
hb="$root/$REL"

beat() {
    mkdir -p "$(dirname "$hb")"
    : > "$hb"            # truncate-or-create updates mtime; content is irrelevant
}

if [ "${1:-}" = "--loop" ]; then
    interval="${2:-30}"
    while :; do beat; sleep "$interval"; done
else
    beat
    echo "heartbeat: touched $hb"
fi
