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
for v in 1 true yes ON On TRUE Yes " yes " " on"; do
  check "bypass truthy value '$v' passes" 0 "$(printf 'feat: sneaky change\n')" "COMMIT_MSG_GATE_BYPASS=$v"
done
for v in 0 off false no "" garbage "onn" "yes please" 2; do
  check "bypass non-truthy value '$v' does not bypass" 1 "$(printf 'feat: sneaky change\n')" "COMMIT_MSG_GATE_BYPASS=$v"
done
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

# --- CI example job (examples/ci-commit-msg-check.yml): run its `run:` block for real ----
YML="$HERE/../examples/ci-commit-msg-check.yml"
ci_script="$tmp/ci-run.sh"
awk '/^        run: \|/{f=1;next} f{ if ($0 ~ /^          /) print substr($0,11); else if ($0 !~ /^[[:space:]]*$/) f=0 }' "$YML" > "$ci_script"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
ci_repo="$tmp/ci-upstream"; ci_clone="$tmp/ci-clone"
git init -q -b main "$ci_repo" && git -C "$ci_repo" commit -q --allow-empty -m 'chore: base'
git clone -q "$ci_repo" "$ci_clone" && git -C "$ci_clone" checkout -q -b feature
# ci_run <expected_exit> <description> [BASE]
ci_run() {
  want="$1"; desc="$2"; base="${3:-main}"
  (cd "$ci_clone" && GATE="$GATE" BASE="$base" bash "$ci_script") >/dev/null 2>&1
  got=$?
  if [ "$got" -eq "$want" ]; then echo "ok   - $desc"; else echo "FAIL - $desc (want exit $want, got $got)"; fails=$((fails + 1)); fi
}
ci_run 0 "ci: no commits in range passes"
git -C "$ci_clone" commit -q --allow-empty -m 'feat: x' -m 'plan: real-plan'
ci_run 0 "ci: compliant commit passes"
git -C "$ci_clone" commit -q --allow-empty --allow-empty-message --no-verify -m ''
ci_run 1 "ci: empty-message commit (--no-verify) fails"
git -C "$ci_clone" reset -q --hard HEAD~1
git -C "$ci_clone" commit -q --allow-empty --no-verify -m 'feat: no plan'
ci_run 1 "ci: planless commit fails"
git -C "$ci_clone" reset -q --hard HEAD~1
ci_run 1 "ci: unresolvable base fails closed" nonexistent

if [ "$fails" -gt 0 ]; then
  echo "$fails commit-msg-gate test(s) failed"
  exit 1
fi
echo "all commit-msg-gate tests passed"
