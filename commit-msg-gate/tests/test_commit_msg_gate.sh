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

# --- body-only plan: match -------------------------------------------------
check "plan: on subject line only fails"  1 "$(printf 'plan: add-logout-button\n')"
check "plan: subject + no body line fails" 1 "$(printf 'plan: add-logout-button\n\nfeat: thing\n')"
check "plan: in body (no blank line) ok"  0 "$(printf 'feat: thing\nplan: add-logout-button\n')"
check "comment lines do not shift subject" 0 "$(printf '# a comment\nfeat: thing\n\nplan: x\n')"

# --- COMMIT_MSG_GATE_PLANS_DIR (opt-in slug validation) ---------------------
plans="$tmp/plans"
mkdir -p "$plans"
printf -- '---\nname: real-plan\nstatus: active\n---\n' > "$plans/real-plan.md"
check "plans dir: existing slug passes"   0 "$(printf 'feat: x\n\nplan: real-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: unknown slug fails"     1 "$(printf 'feat: x\n\nplan: ghost-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: any resolving slug ok"  0 "$(printf 'feat: x\n\nplan: ghost-plan\nplan: real-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: unset dir not validated" 0 "$(printf 'feat: x\n\nplan: ghost-plan\n')"
check "plans dir: dir slug is not a plan"  1 "$(printf 'feat: x\n\nplan: sub\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
mkdir -p "$plans/sub.md"
check "plans dir: sub.md directory rejected" 1 "$(printf 'feat: x\n\nplan: sub\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: dotdot slug rejected"   1 "$(printf 'feat: x\n\nplan: ..\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: ceremony prefix needs no plan" 0 "$(printf 'docs: x\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
check "plans dir: missing dir fails closed" 1 "$(printf 'feat: x\n\nplan: real-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$tmp/nonexistent"
check "plans dir: file-not-dir fails closed" 1 "$(printf 'feat: x\n\nplan: real-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans/real-plan.md"
check "plans dir: bypass still wins"      0 "$(printf 'feat: x\n\nplan: ghost\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans" COMMIT_MSG_GATE_BYPASS=on
if [ "$(id -u)" -ne 0 ] && chmod 000 "$plans" 2>/dev/null && [ ! -r "$plans" ]; then
  check "plans dir: unreadable dir fails closed" 1 "$(printf 'feat: x\n\nplan: real-plan\n')" COMMIT_MSG_GATE_PLANS_DIR="$plans"
  chmod 755 "$plans"
else
  chmod 755 "$plans" 2>/dev/null
  echo "skip - unreadable-dir case (root or chmod unsupported)"
fi

if [ "$fails" -gt 0 ]; then
  echo "$fails commit-msg-gate test(s) failed"
  exit 1
fi
echo "all commit-msg-gate tests passed"
