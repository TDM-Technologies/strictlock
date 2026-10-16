# plan-gate

**A fail-closed, pre-action authorization gate for AI coding agents.**

`plan-gate` is the flagship [StrictLock](../README.md) module. It's a single Python script
wired as a **`PreToolUse` hook**: before an agent edits a file or runs a command, the gate
reads the active *plan* and decides allow/deny. An agent may modify only the exact files and
run only commands matching the command prefixes an approved plan enumerates. Everything else
is denied **before it runs**. When in doubt, the gate denies.

Read the full argument in [`paper.md`](paper.md).

## What it does

- Reads a tool-use JSON payload on **stdin** (the PreToolUse convention).
- Finds the single `status: active` plan in your plans directory (scoped per git worktree).
- **Edit / Write / NotebookEdit:** the target path must *exactly* match an `allowed_paths`
  entry. A directory entry authorizes nothing inside it — enumerate each file.
- **Bash:** every command is split into segments (`&&`, `||`, `;`, `|`, a lone `&`, newlines,
  plus the bodies of `$(...)`, backticks and `<(...)`), and **each segment** must independently
  *start with* an `allowed_commands` prefix, be a bare `cd`, or be a known read-only command
  (`git status`, `ls`, `cat`, … — narrowed to their non-mutating forms). A compound command is
  only as authorized as its least-authorized segment. A redirection that writes (`>`, `>>`,
  `>|`, `&>`, `2>`) is a file write, gated like Edit/Write (`/dev/null` is always fine). A
  command the gate cannot confidently parse is denied. See [`AUTHORING.md`](AUTHORING.md) §2.
- **PowerShell:** the command must *start with* an `allowed_commands` prefix or be a known
  read-only cmdlet; there is no compound parsing yet (list each shape explicitly).
- Emits `{"hookSpecificOutput": {"permissionDecision": "allow"|"deny", ...}}` on **stdout**.
- Appends every decision, allow and deny, to a hash-chained decision log (when
  `PLAN_GATE_LOG_DIR` is set) — your audit trail. `plan-gate.py verify-log` proves the
  chain, and the gate never authorizes a gated write to the log itself.

It **fails closed** for governance *and for its own bugs*: a payload it cannot parse, or an
unhandled exception inside the gate, produces a **deny** (JSON, exit 0) with a loud message
saying what failed and how to bypass — never a non-zero exit, which a harness treats as
"proceed". The escape hatch is `PLAN_GATE_BYPASS=on` set inline; it is honored even on that
crash path, and every use is logged (`emergency_bypass`). The trade-off is accepted: if the
harness changes its payload protocol, the gate denies until it is updated or bypassed.

## Migration note (plans written before segment checking)

Plans that relied on a prefix riding along with what followed it — `pytest -q && git push`
matching `pytest`, `cat x; rm y` matching the read-only `cat`, `echo x > f` matching `echo` —
**now deny. That is intended.** To migrate:

1. Run the work and read each deny: it names the offending segment.
2. Add an `allowed_commands` entry for each *distinct command* in the chain (one entry per
   command, as narrow as [`AUTHORING.md`](AUTHORING.md) §2 advises). An entry that spells out a
   whole compound (`git add -A && git commit`) no longer authorizes the compound.
3. Add every file a command redirects into (`> out.txt`) to `allowed_paths`; a relative target
   resolves against the working directory, and after a `cd` in the same command an absolute path
   is required.
4. `git fetch` and mutating `git branch` / `git remote` / `find -delete` forms are no longer
   treated as read-only; grant the ones the work needs explicitly.

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
