#!/usr/bin/env bash
# Tests for commit-msg-gate. No dependencies beyond bash + grep + sed.
# Run:  bash commit-msg-gate/tests/test_commit_msg_gate.sh
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../commit-msg-gate.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

# check <description> <expected_exit> <message> [VAR=val ...]
check() {
  desc="$1"; want="$2"; msg="$3"; shift 3
  f="$tmp/msg"
  printf '%s' "$msg" > "$f"
  env "$@" bash "$GATE" "$f" >/dev/null 2>&1
  got=$?
  if [ "$got" -eq "$want" ]; then
    echo "ok   - $desc"
  else
    echo "FAIL - $desc (want exit $want, got $got)"
    fails=$((fails + 1))
  fi
}

check "plan reference passes"     0 "$(printf 'feat: thing\n\nplan: add-logout-button\n')"
check "chore prefix passes"       0 "$(printf 'chore: tidy up\n')"
check "chore(scope) passes"       0 "$(printf 'chore(deps): bump dependency\n')"
check "docs prefix passes"        0 "$(printf 'docs: update readme\n')"
check "ci prefix passes"          0 "$(printf 'ci: cache pip\n')"
check "no plan no prefix fails"   1 "$(printf 'feat: sneaky change\n')"
check "bypass passes"             0 "$(printf 'feat: sneaky change\n')" COMMIT_MSG_GATE_BYPASS=on
check "custom prefix override"    0 "$(printf 'wip: scratch\n')" COMMIT_MSG_GATE_PREFIXES='^wip:'
check "custom prefix still gates" 1 "$(printf 'feat: thing\n')" COMMIT_MSG_GATE_PREFIXES='^wip:'

if [ "$fails" -gt 0 ]; then
  echo "$fails commit-msg-gate test(s) failed"
  exit 1
fi
echo "all commit-msg-gate tests passed"
