#!/usr/bin/env bash
# Example .git/hooks/pre-commit wiring for spill-gate.
#
# Install: copy to <your-repo>/.git/hooks/pre-commit, chmod +x, edit the path below.
# Git runs the hook with no arguments; exit 0 lets the commit proceed, non-zero blocks it.
set -euo pipefail

export SPILL_GATE=on
# Optional: allowlist elsewhere than <repo>/.spill-gate-allow, and an audit log.
# export SPILL_GATE_ALLOWLIST=".spill-gate-allow"
# export SPILL_GATE_LOG_DIR="$HOME/.agent/logs"

exec python3 /path/to/strictlock/spill-gate/spill-gate.py pre-commit
