#!/usr/bin/env bash
# Example .git/hooks/pre-push wiring for spill-gate.
#
# Install: copy to <your-repo>/.git/hooks/pre-push, chmod +x, edit the path below.
# Git passes the remote name and URL as arguments and the refs being pushed on stdin;
# "$@" and the inherited stdin go straight through to the gate (exec keeps stdin).
set -euo pipefail

export SPILL_GATE=on
# export SPILL_GATE_LOG_DIR="$HOME/.agent/logs"

exec python3 /path/to/strictlock/spill-gate/spill-gate.py pre-push "$@"
