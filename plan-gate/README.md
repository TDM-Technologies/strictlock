# plan-gate

**A fail-closed, pre-action authorization gate for AI coding agents.**

`plan-gate` is the flagship [StrictLock](../README.md) module. It's a single Python script
wired as a **`PreToolUse` hook**: before an agent edits a file or runs a command, the gate
reads the active *plan* and decides allow/deny. An agent may modify only the exact files and
run only the exact commands an approved plan enumerates. Everything else is denied **before
it runs**. When in doubt, the gate denies.

Read the full argument in [`paper.md`](paper.md).

## What it does

- Reads a tool-use JSON payload on **stdin** (the PreToolUse convention).
- Finds the single `status: active` plan in your plans directory (scoped per git worktree).
- **Edit / Write / NotebookEdit:** the target path must *exactly* match an `allowed_paths`
  entry. A directory entry authorizes nothing inside it — enumerate each file.
- **Bash / PowerShell:** the command must *start with* an `allowed_commands` prefix, or be a
  known read-only command (`git status`, `ls`, `cat`, …) which is always permitted.
- Emits `{"hookSpecificOutput": {"permissionDecision": "allow"|"deny", ...}}` on **stdout**.
- Appends every denial to a decision log (when `PLAN_GATE_LOG_DIR` is set) — your audit trail.

It **fails closed** for governance and **fails open for its own bugs**: an unhandled
exception in the gate exits non-zero with a warning rather than wedging your agent.

## Quickstart (60 seconds)

```bash
# 1. Turn it on and point it at a plans directory.
export PLAN_GATE=on
export PLAN_GATE_PLANS_DIR="$HOME/.agent/plans"

# 2. Author an approved plan with allowed_paths / allowed_commands frontmatter.
#    See examples/sample-plan.md and SCHEMA.md.

# 3. Wire plan-gate.py as a PreToolUse hook. See examples/settings.json for the
#    Claude Code shape; adapt the stdin/stdout JSON contract to any other harness.
```

Then sanity-check the gate by hand:

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/not/in/plan.txt"}}' \
  | PLAN_GATE=on PLAN_GATE_PLANS_DIR="$HOME/.agent/plans" python plan-gate.py
# -> {"hookSpecificOutput": {... "permissionDecision": "deny" ...}}
```

A full allowed-vs-denied walkthrough is in [`examples/README.md`](examples/README.md).

## Targets & portability

Built against the **Claude Code** `PreToolUse` hook contract, but nothing about the gate is
Claude-specific. It's stdin-JSON in, stdout-JSON out, configured by environment variables,
with no machine-specific defaults baked in. Adapt the payload shape and you can put it in
front of any agent runtime that supports a pre-action hook.

## Files

| File | Purpose |
|---|---|
| [`plan-gate.py`](plan-gate.py) | The gate. One file, standard library only. |
| [`paper.md`](paper.md) | The formal write-up of the approach. |
| [`CONFIG.md`](CONFIG.md) | Every `PLAN_GATE_*` environment variable (Windows/Linux/macOS). |
| [`SCHEMA.md`](SCHEMA.md) | The plan-file frontmatter specification. |
| [`examples/`](examples/) | A sample plan, a hook config, and a walkthrough. |
| [`tests/`](tests/) | A standalone test suite (stdlib `unittest`, no dependencies). |

## Requirements

Python 3.8+ (standard library only — no pip install).

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
