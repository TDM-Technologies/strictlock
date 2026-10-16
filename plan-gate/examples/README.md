# plan-gate examples

This directory shows the gate end-to-end:

- [`sample-plan.md`](sample-plan.md) — a valid active plan.
- [`settings.json`](settings.json) — wiring `plan-gate.py` as a Claude Code `PreToolUse` hook.

Below: run the gate by hand and watch it allow one action and deny another. The gate reads a
tool-use JSON payload on **stdin** and writes a decision JSON on **stdout**. For installing
the gate in a real project, start from the
[first-install steps in the plan-gate README](../README.md#install-plan-gate-in-a-project-first-install).

Every path in this walkthrough is absolute, as it is in a real hook payload. `/absolute/path/to/project`
stands for the directory you run the work in, and `/absolute/path/to/strictlock` for where you
cloned this repository. Substitute your own.

## Setup

```bash
mkdir -p /home/you/.agent/plans /home/you/.agent/logs
cp sample-plan.md /home/you/.agent/plans/
export PLAN_GATE=on
export PLAN_GATE_PLANS_DIR=/home/you/.agent/plans
export PLAN_GATE_LOG_DIR=/home/you/.agent/logs
```

Then edit `/home/you/.agent/plans/sample-plan.md` and replace the three `allowed_paths`
entries with absolute paths into your project, for example
`/absolute/path/to/project/src/components/Header.tsx`.

**Why absolute.** The sample plan ships with repo-relative paths so it reads easily. In an
ordinary checkout those work. Inside a git **worktree**, a plan claims the worktree only if at
least one `allowed_paths` entry is an absolute path under it. With only relative paths the plan
claims nothing, and the gate denies every write and every non-read-only command there. Using
absolute paths everywhere avoids that trap and costs nothing elsewhere.

The edited plan lists `/absolute/path/to/project/src/components/Header.tsx` in `allowed_paths`
and `npm test` in `allowed_commands`.

The gate does use your working directory: it looks there for the git repository or worktree you
are in. Run the examples from a directory that is not inside an unrelated git worktree (a plain
shell directory is fine, or the project itself). Run from inside a different worktree, the plan
does not claim that tree and every example is denied as likely drift. Use `python3` (a bare `python` is not installed on many systems).

## Allowed: editing a file the plan names

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/absolute/path/to/project/src/components/Header.tsx"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
 "permissionDecisionReason": "plan-gate: /absolute/path/to/project/src/components/Header.tsx matches allowed_paths entry ..."}}
```

> Use an absolute `file_path`, as the harness sends. A relative `file_path` is resolved against
> the directory the gate runs in and will normally not match; do not try it as a test of
> "does the plan allow this file".

## Denied: editing a file the plan does NOT name

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/absolute/path/to/project/src/secrets/keys.ts"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "plan-gate: /absolute/path/to/project/src/secrets/keys.ts is NOT in allowed_paths ..."}}
```

## Allowed: a read-only command (always permitted)

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> allow (read-only bypass)
```

## Denied: a command outside allowed_commands

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf build"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> deny (rm is not an allowed_commands prefix)
```

## Allowed: a command the plan names

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"npm test -- --watch=false"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> allow (starts with the "npm test" prefix)
```

## Denied: an allowed prefix with something chained after it

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"npm test && rm -rf build"}}' \
  | python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> deny (segment "rm -rf build" has no grant; each segment is checked on its own)
```

## Check the decision log

With `PLAN_GATE_LOG_DIR` set, each of the runs above appended a row:

```bash
tail -n 3 /home/you/.agent/logs/plan-gate-decisions.log
python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py verify-log
# -> exit 0 means the hash chain is intact
```

If a hook you wired into a real session never produces rows like these, it is not running; see
[the install section](../README.md#the-gate-fails-closed-once-it-runs-but-it-cannot-protect-you-if-it-never-starts).

## What happens with no active plan

If no plan in `PLAN_GATE_PLANS_DIR` has `status: active`, every gated tool call is **denied**
— the deny-by-default floor. Set a plan active first. (Today `status: active` is written by
whoever can edit the plan file; nothing records who approved it. See
[Known limits](../README.md#known-limits-today).)
