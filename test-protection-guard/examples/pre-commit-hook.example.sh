#!/usr/bin/env sh
# test-protection-guard — the git pre-commit door (universal; any committer).
#
# Copy this to .git/hooks/pre-commit (and `chmod +x`), then edit GUARD and the
# config below. The guard is ADVISORY: it warns on stderr and ALWAYS exits 0,
# so a commit is never blocked — the value is the logged record reviewed later.
#
#   cp examples/pre-commit-hook.example.sh /path/to/repo/.git/hooks/pre-commit
#   chmod +x /path/to/repo/.git/hooks/pre-commit
#
# Prefer a managed hook runner (husky / pre-commit / lefthook)? Point its
# "local"/"system" hook at the same command — the guard needs no arguments and
# reads no stdin in this mode.

GUARD="/abs/path/to/test-protection-guard/test-protection-guard.py"

export TEST_PROTECTION_GUARD=on
export TEST_PROTECTION_GUARD_LOG_DIR="$HOME/.agent/logs"   # your audit trail (stable path)
# Optional retargeting (defaults cover JS/TS/py/Go/Rust/…):
# export TEST_PROTECTION_GUARD_TEST_GLOBS="*.test.ts:test_*.py"
# export TEST_PROTECTION_GUARD_SOURCE_GLOBS="*.ts:*.py"
# export TEST_PROTECTION_GUARD_ASSERTION_RE='\bexpect\s*\(|\bassert\b'

# No stdin -> the guard auto-selects pre-commit mode. exec keeps the exit code.
exec python3 "$GUARD" </dev/null
