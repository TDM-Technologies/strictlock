#!/usr/bin/env bash
# Example .git/hooks/pre-push wiring for generated-sink-prepush-gate.
#
# Install:
#   cp this file to <your-repo>/.git/hooks/pre-push
#   chmod +x <your-repo>/.git/hooks/pre-push
#   (edit the paths + config below to match your repo)
#
# Git runs a pre-push hook with (remote-name remote-url) as arguments and the
# refs being pushed on stdin, and honors its exit code:
#   exit 0 -> the push proceeds
#   exit non-zero -> the push is blocked
# The gate validates the working-tree sink, so it ignores the args and stdin;
# we forward "$@" only to mirror the real invocation.

set -euo pipefail

# --- gate configuration (or set these in your shell / CI environment) --------
export GENERATED_SINK_PREPUSH_GATE=on
# The generator's --check command. MUST exit non-zero iff the sink is stale and
# MUST NOT mutate the working tree.
export GENERATED_SINK_PREPUSH_GATE_GENERATOR="npm run build:manifest -- --check"
# Optional: run the generator from a subproject dir (repo-root-relative or absolute).
# export GENERATED_SINK_PREPUSH_GATE_GENERATOR_CWD="app"
# Optional: append a per-decision audit log.
# export GENERATED_SINK_PREPUSH_GATE_LOG_DIR="$HOME/.agent/logs"

# --- run the gate ------------------------------------------------------------
# Absolute path to where you installed the StrictLock module.
exec python3 /path/to/strictlock/generated-sink-prepush-gate/generated-sink-prepush-gate.py "$@"
