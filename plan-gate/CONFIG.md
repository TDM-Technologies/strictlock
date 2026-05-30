# plan-gate configuration

Everything is configured through **environment variables** — no machine-specific defaults
are baked into the script. If `PLAN_GATE` is not `on`, the hook is inert and allows
everything.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_GATE` | yes | Set to `on` (case-insensitive) to enable the gate. Anything else makes the hook inert (allow-all). |
| `PLAN_GATE_PLANS_DIR` | yes | Directory holding plan files. Exactly one plan may be `status: active` at a time (scoped per git worktree). |
| `PLAN_GATE_ALWAYS_WRITABLE` | no | `os.pathsep`-separated list of directories that are *always* writable regardless of plan state. Intended for the plans dir itself and any agent-memory dir that never ships. Same blast radius as plan files — keep it tight. |
| `PLAN_GATE_LOG_DIR` | no | Directory for the append-only decision log (`plan-gate-denies.log`, plus PowerShell/MCP telemetry). This log is your audit trail; if unset, no log is written. |
| `PLAN_GATE_MCP_WRITE_TOOLS` | no | Comma-separated list of MCP tool names that modify external state (e.g. a cloud-drive create/copy tool). When set, those tools are **default-denied** so they can't evade the gate. |
| `PLAN_GATE_BYPASS` | no | Emergency escape hatch. `on` disables the gate for the session. Use deliberately. |
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
- **Hard guard:** if the session is under a worktree and *none* of `allowed_paths` resolves
  under that worktree, every tool call is blocked. This catches the "wrote to the wrong
  tree" failure. Opt out for deliberately cross-tree work with `worktree_bypass: true` in
  the plan (see [SCHEMA.md](SCHEMA.md)).

## Decision log

When `PLAN_GATE_LOG_DIR` is set, every denial appends a JSON line with a UTC timestamp, the
tool, the command/target, the reason, the governing plan, and the working directory. That
append-only trail is the compliance exhaust described in [`paper.md`](paper.md) §6 — produced
as a byproduct of the gate doing its job, not as a separate feature.
