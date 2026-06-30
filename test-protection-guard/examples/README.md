# test-protection-guard — runnable walkthrough

Two doors, one behaviour. Both reproduce the canonical flag — an existing assertion changed to
match buggy output, committed alongside the source change, with no justification — in about a
minute. The guard is advisory, so nothing here ever blocks; watch the **warning** and the **log**.

```bash
GUARD="$(cd "$(dirname "$0")/.." && pwd)/test-protection-guard.py"   # or an absolute path
export TEST_PROTECTION_GUARD=on
export TEST_PROTECTION_GUARD_LOG="/tmp/tpg-demo.log"
```

## Build a tiny repo with a real coupling

```bash
cd "$(mktemp -d)"
git init -q
printf 'export const compute = () => 5;\n'                         > calc.ts
printf "it('computes', () => { expect(compute()).toBe(5); });\n"    > calc.test.ts
git add -A && git commit -qm baseline

# The bug + the cover-up, in one commit: the code now returns 4, and the test's
# EXISTING assertion was changed to expect 4 instead of fixing the code.
printf 'export const compute = () => 4;\n'                          > calc.ts
printf "it('computes', () => { expect(compute()).toBe(4); });\n"    > calc.test.ts
git add -A
```

## Door A — git pre-commit (universal)

```bash
python3 "$GUARD" </dev/null ; echo "exit=$?"
```

You'll see a `⚠ test-protection-guard …` warning on stderr naming the changed-out assertion, and
`exit=0` (advisory — the commit would proceed). The flag is recorded:

```bash
cat "$TEST_PROTECTION_GUARD_LOG"
# {"ts":"…","mode":"precommit",…,"decision":"flagged"}
```

## Door B — PreToolUse (agentic harness)

Feed the same staged state a tool-call payload, exactly as Claude Code would:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"fix compute\""}}' \
  | python3 "$GUARD"
```

The guard prints an `allow` decision whose `permissionDecisionReason` contains `…FLAGGED…`
(and the same stderr warning). The agent sees the reason inline and can fix the code instead.

## Clear the flag with a justification

If the **test** was genuinely wrong (the spec changed), say so — the flag becomes a logged-but-
justified record, no warning:

```bash
printf '// TEST-CORRECTNESS: spec changed; 4 is the correct value now\n' >  calc.test.ts
printf "it('computes', () => { expect(compute()).toBe(4); });\n"         >> calc.test.ts
git add -A
python3 "$GUARD" </dev/null ; echo "exit=$?"
grep '"decision":"justified"' "$TEST_PROTECTION_GUARD_LOG"
```

In PreToolUse mode the token also works in the commit message
(`git commit -m "TEST-CORRECTNESS: …"`), since the message is on the command line there.

## Install for real

- **PreToolUse:** wire [`settings.json`](settings.json) into `~/.claude/settings.json`.
- **git pre-commit:** copy [`pre-commit-hook.example.sh`](pre-commit-hook.example.sh) to
  `.git/hooks/pre-commit` (or point husky / `pre-commit` / lefthook at the guard).

Then make a habit of reviewing the log — that review is where the teeth are.
