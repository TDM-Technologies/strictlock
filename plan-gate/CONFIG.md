# plan-gate configuration

Everything is configured through **environment variables** — no machine-specific defaults
are baked into the script. If `PLAN_GATE` is not `on`, the hook is inert and allows
everything.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_GATE` | yes | Set to `on` (case-insensitive) to enable the gate. Anything else makes the hook inert (allow-all). |
| `PLAN_GATE_PLANS_DIR` | yes | Directory holding plan files. Exactly one plan may be `status: active` at a time (scoped per git worktree). |
| `PLAN_GATE_ALWAYS_WRITABLE` | no | `os.pathsep`-separated list of directories that are *always* writable regardless of plan state. Intended for the plans dir itself and any agent-memory dir that never ships. Same blast radius as plan files — keep it tight. Putting the plans dir here lets a gated agent write a plan with `status: active` and have it take effect at once; leave it out if a human should write the plans. |
| `PLAN_GATE_LOG_DIR` | no (recommended) | Directory for the append-only, hash-chained decision log (`plan-gate-decisions.log`). This log is your audit trail and it is opt-in: if unset, no log is written. A planned release will require it for plan approval. |
| `PLAN_GATE_LOG_DECISIONS` | no | `all` (default) records every decision on a gated surface, allow and deny; `deny` records denials plus emergency-bypass rows only. |
| `PLAN_GATE_MCP_WRITE_TOOLS` | no | Comma-separated list of MCP tool names that modify external state (e.g. a cloud-drive create/copy tool). When set, those tools are **always denied** while a plan is active (their arguments are not read, so a plan cannot allow one). The hook only sees tools its `matcher` routes to it, and the example matcher does not include MCP tools: add them (e.g. `mcp__.*`). |
| `PLAN_GATE_BYPASS` | no | Emergency escape hatch. `on` (the only accepted value, case-insensitive) disables the gate; every use is logged. Also honored when the gate itself faults. Use deliberately. It must be in the environment the **harness** gives the hook (the environment Claude Code was launched with, or the `env` block of its settings); `export` in the agent's Bash tool, or an inline prefix on the agent's command, does not reach the hook. Set it, restart the session, and remove it afterwards. |
| `_PLAN_GATE_TEST_PLANS_DIR` | no | Test-only override that redirects the plans scan to a temp dir. Not a security boundary — used by the test suite. |

`os.pathsep` is `;` on Windows and `:` on Linux/macOS — it applies to
`PLAN_GATE_ALWAYS_WRITABLE`.

## Per-OS examples

### Linux / macOS (bash/zsh)

```bash
export PLAN_GATE=on
export PLAN_GATE_PLANS_DIR="$HOME/.agent/plans"
export PLAN_GATE_LOG_DIR="$HOME/.agent/logs"
export PLAN_GATE_ALWAYS_WRITABLE="$HOME/.agent/plans:$HOME/.agent/memory"
```

### Windows (PowerShell)

```powershell
$env:PLAN_GATE = "on"
$env:PLAN_GATE_PLANS_DIR = "$env:USERPROFILE\.agent\plans"
$env:PLAN_GATE_LOG_DIR = "$env:USERPROFILE\.agent\logs"
$env:PLAN_GATE_ALWAYS_WRITABLE = "$env:USERPROFILE\.agent\plans;$env:USERPROFILE\.agent\memory"
```

### Windows (cmd)

```bat
set PLAN_GATE=on
set PLAN_GATE_PLANS_DIR=%USERPROFILE%\.agent\plans
set PLAN_GATE_LOG_DIR=%USERPROFILE%\.agent\logs
set PLAN_GATE_ALWAYS_WRITABLE=%USERPROFILE%\.agent\plans;%USERPROFILE%\.agent\memory
```

### In a Claude Code `settings.json`

Set the variables in the `env` block and register the hook in `hooks.PreToolUse` — see
[`examples/settings.json`](examples/settings.json).

## Path resolution notes

- `allowed_paths` entries may be **absolute** or **repo-root-relative**.
- Relative entries resolve against the nearest repo root, walking up from the session's
  working directory. If the session runs inside a **git worktree** (where `.git` is a file,
  not a directory), the **worktree root** is the anchor — not the main repo.
- **A plan with only relative paths cannot claim a worktree** (next bullet). Inside a worktree
  the sample plan therefore denies everything until at least one `allowed_paths` entry is an
  absolute path under that worktree. Use absolute paths for real plans.
- **Worktree claim (claim-by-absolute):** a plan counts as active for a worktree session only
  if at least one of its *absolute* `allowed_paths` resolves under that worktree (or it
  declares `worktree_bypass: true`). If none does, the plan is not active there, so gated file
  writes and commands outside the read-only set are denied; read-only inspection
  (`git status`, `ls`, ...) and always-writable directories stay available. When active plans
  exist elsewhere the deny says "N active plan(s) exist, none claims this worktree — likely
  drift" (decision class `silent_drift`); with no active plan anywhere it is the generic "no
  active plan" text. This catches the "wrote to the wrong tree" failure. Opt out for
  deliberately cross-tree work with `worktree_bypass: true` in the plan (see
  [SCHEMA.md](SCHEMA.md)).

## Bash commands: segments and redirections

Every Bash command is parsed before it is decided. It is split on `&&`, `||`, `;`, `|`, `|&`,
a lone `&` and unquoted newlines; the bodies of `$(...)`, backticks, `<(...)` / `>(...)` and
`( ... )` are checked as segments too. **Each** segment must be a read-only command, a bare
`cd`, or start with an `allowed_commands` entry. Redirections `>`, `>>`, `>|`, `&>`, `N>` are
file writes gated like Edit/Write (exact `allowed_paths` match or an always-writable
directory; never the log directory; `/dev/null`, `/dev/stdout`, `/dev/stderr`, `/dev/tty` and, on
Windows, `NUL` always fine; `>&N` fd dups and `<` input are not writes; a target with an unquoted
glob or brace is denied). Anything the parser cannot confidently place is denied — including a
heredoc with a `$`-form or unterminated delimiter. A `#` starts a comment only at the start of a
word, as in the shell. An empty `allowed_commands` entry matches nothing (Bash and PowerShell).
PowerShell is unchanged (prefix match, no compound parsing). Details and the migration note:
[`AUTHORING.md`](AUTHORING.md) §2 and [`README.md`](README.md).

## Fault posture

A stdin payload that does not parse, or an unhandled exception in the gate, **denies** (JSON on
stdout, exit 0) with a message naming the failure and the bypass. Set `PLAN_GATE_BYPASS=on`
in the harness environment (see the variable table) to proceed anyway — that is honored on the fault path too and logged as
`emergency_bypass`. `PLAN_GATE` unset or not `on` still allows. A best-effort `gate_error` row
is written to the decision log; a logging failure never changes the decision. (A test-only
`_PLAN_GATE_TEST_CRASH=early|late` variable injects a fault for the test suite; it can only
make the gate deny.)

## Decision log

When `PLAN_GATE_LOG_DIR` is set, every decision on a gated surface appends a JSON line to
`plan-gate-decisions.log`: a UTC timestamp, the decision (`allow`/`deny`), the surface
(`file`/`bash`/`ps`/`mcp`/`gate`), the tool, the command/target, a decision class (e.g.
`allowed_paths`, `allowed_command`, `read_only_bypass`, `navigation`, `emergency_bypass`,
`log_self_protect`, `silent_drift`, `unparseable_command`, `gate_error`), the reason, the
governing plan, and the working directory. File contents and full payloads are never logged.
`unparseable_command` is a Bash command the parser could not confidently place (denied);
`gate_error` is a fault inside the gate itself (denied, see Fault posture).

Rows are hash-chained — each carries `prev` (the previous row's hash) and `h` (its own) — so
edits or deletions within the record are detectable:

```
python3 plan-gate.py verify-log [path]   # exit 0 = chain intact, 2 = damage/tampering
```

Concurrent sessions share one log safely (advisory locking). The gate never authorizes a
gated file-write into the log directory itself, no matter what a plan or always-writable
entry says (`log_self_protect`). A logging failure never changes a decision. That
append-only trail is the audit evidence described in [`paper.md`](paper.md) §6. It is written
by the gate as it decides, but only when you set `PLAN_GATE_LOG_DIR`; it is opt-in.
