#!/usr/bin/env bash
# Example .git/hooks/pre-commit wiring for generated-sink-commit-gate.
#
# Install:
#   cp this file to <your-repo>/.git/hooks/pre-commit
#   chmod +x <your-repo>/.git/hooks/pre-commit
#   (edit the paths + config below to match your repo)
#
# Git runs a pre-commit hook with no arguments and honors its exit code:
#   exit 0 -> the commit proceeds
#   exit non-zero -> the commit is blocked
# This script simply execs the gate, which produces that exit code.

set -euo pipefail

# --- gate configuration (or set these in your shell / CI environment) --------
export SINK_COMMIT_GATE=on
# The generator's --check command. MUST exit non-zero iff the sink is stale and
# MUST NOT mutate the working tree.
export SINK_COMMIT_GATE_GENERATOR="npm run build:manifest -- --check"
# Repo-root-relative source path prefix(es) that trigger the check. ':'-separated.
export SINK_COMMIT_GATE_SOURCE_PATHS="docs/manifest"
# Optional: run the generator from a subproject dir (repo-root-relative or absolute).
# export SINK_COMMIT_GATE_GENERATOR_CWD="app"
# Optional: append a per-decision audit log.
# export SINK_COMMIT_GATE_LOG_DIR="$HOME/.agent/logs"

# --- run the gate ------------------------------------------------------------
# Absolute path to where you installed the StrictLock module.
exec python3 /path/to/strictlock/generated-sink-commit-gate/generated-sink-commit-gate.py
