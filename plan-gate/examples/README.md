# plan-gate examples

This directory shows the gate end-to-end:

- [`sample-plan.md`](sample-plan.md) — a valid approved plan.
- [`settings.json`](settings.json) — wiring `plan-gate.py` as a Claude Code `PreToolUse` hook.

Below: run the gate by hand and watch it allow one action and deny another. The gate reads a
tool-use JSON payload on **stdin** and writes a decision JSON on **stdout**.

## Setup

```bash
mkdir -p ~/.agent/plans
cp sample-plan.md ~/.agent/plans/
export PLAN_GATE=on
export PLAN_GATE_PLANS_DIR="$HOME/.agent/plans"
```

`sample-plan.md` lists `src/components/Header.tsx` in `allowed_paths` and `npm test` in
`allowed_commands`.

## Allowed: editing a file the plan names

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"src/components/Header.tsx"}}' \
  | python ../plan-gate.py
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
 "permissionDecisionReason": "plan-gate: src/components/Header.tsx matches allowed_paths entry ..."}}
```

> Relative paths resolve against your repo root / worktree. Run the gate from the repo whose
> paths the plan names, or list absolute paths in the plan.

## Denied: editing a file the plan does NOT name

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"src/secrets/keys.ts"}}' \
  | python ../plan-gate.py
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "plan-gate: src/secrets/keys.ts is NOT in allowed_paths ..."}}
```

## Allowed: a read-only command (always permitted)

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | python ../plan-gate.py
# -> allow (read-only bypass)
```

## Denied: a command outside allowed_commands

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf build"}}' | python ../plan-gate.py
# -> deny (rm is not an allowed_commands prefix)
```

## Allowed: a command the plan names

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"npm test -- --watch=false"}}' \
  | python ../plan-gate.py
# -> allow (starts with the "npm test" prefix)
```

## What happens with no active plan

If no plan in `PLAN_GATE_PLANS_DIR` has `status: active`, every gated tool call is **denied**
— the deny-by-default floor. Set a plan active first.
