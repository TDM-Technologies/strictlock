# plan-gate configuration

Everything is configured through **environment variables** — no machine-specific defaults
are baked into the script. If `PLAN_GATE` is not `on` / `1` / `true` / `yes`, the hook is inert and allows
everything.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_GATE` | yes | Set to `on` / `1` / `true` / `yes` (case-insensitive, whitespace-trimmed) to enable the gate. Anything else makes the hook inert (allow-all). |
| `PLAN_GATE_PLANS_DIR` | yes | Directory holding plan files. Exactly one plan may be `status: active` at a time (scoped per git worktree). |
| `PLAN_GATE_ALWAYS_WRITABLE` | no | `os.pathsep`-separated list of directories that are *always* writable regardless of plan state. Intended for the plans dir itself and any agent-memory dir that never ships. Same blast radius as plan files — keep it tight. |
| `PLAN_GATE_LOG_DIR` | no | Directory for the append-only, hash-chained decision log (`plan-gate-decisions.log`). This log is your audit trail; if unset, no log is written. |
| `PLAN_GATE_LOG_DECISIONS` | no | `all` (default) records every decision on a gated surface, allow and deny; `deny` records denials plus emergency-bypass rows only. |
| `PLAN_GATE_MCP_WRITE_TOOLS` | no | Comma-separated MCP tools that modify external state. Each entry is `tool` (a bare name: **default-denied**, arguments never read) or `tool:arg` / `tool:arg1+arg2` (the argument(s) that hold target paths; see below). |
| `PLAN_GATE_WRITER_SCRIPTS` | no | Extra "writer scripts", comma-separated `name.py:argpos`. A Bash/PowerShell command that runs `python`/`python3`/`py` on a listed script is treated as a file write to that script's target argument and gated like a `>` redirect target (see below). `atomic-write.py:1` is built in. A malformed value **fails closed**. |
| `PLAN_GATE_BYPASS` | no | Emergency escape hatch. `on` / `1` / `true` / `yes` (case-insensitive) disables the gate for the call (set it inline per command; every use is logged). Also honored when the gate itself faults. Use deliberately. |
| `_PLAN_GATE_TEST_PLANS_DIR` | no | Test-only override that redirects the plans scan to a temp dir. Not a security boundary — used by the test suite. |

`os.pathsep` is `;` on Windows and `:` on Linux/macOS — it applies to
`PLAN_GATE_ALWAYS_WRITABLE`.

## MCP write tools

`PLAN_GATE_MCP_WRITE_TOOLS=mcp__fs__write_file:path,mcp__fs__move:source+destination,mcp__drive__create`

- A tool **with** an argument spec: every path in the named argument(s) is a target. A string
  argument is one target; a list-of-strings argument makes every string a target. Each target
  is checked exactly like an Edit/Write `file_path`: log self-protection, then always-writable
  directories, then the active plan's `allowed_paths` (which also applies the worktree claim).
  **All** targets must pass. A missing, empty or non-string argument (or a non-string list
  element) is denied (decision-log class `mcp_target`). Relative paths never match an
  `allowed_paths` entry. A call whose targets all pass gets an `allow` decision.
- A tool **without** a spec (a bare name, or `tool:` with no argument names): denied while a
  plan is active, as before. If a tool appears both bare and with a spec, the bare entry wins.
- **MCP tools that plan-gate does not affirmatively check get no decision at all** (exit 0, no
  JSON on stdout), so the harness keeps its own permission prompt. That covers MCP tools not
  listed here (a row with class `mcp_unconfigured` is written when logging is on, subject to
  `PLAN_GATE_LOG_DECISIONS`), and any MCP call while the gate is off or `PLAN_GATE_BYPASS` is
  set. An `allow` decision would skip that prompt.
- The hook only sees MCP calls if the harness matcher routes them to it. The example
  `settings.json` matcher is `^(Edit|Write|NotebookEdit|Bash|PowerShell)$|^mcp__`: the anchors
  matter, because a matcher with punctuation is an unanchored regular expression (a bare
  `Write|...|mcp__.*` would also match `TodoWrite`).
- **Decision-log rows:** an `mcp_unconfigured` row records the tool name only. A tool with a
  spec records the tool name, `target` (the first target) and `extra.targets` (each target, up
  to 20, each capped at 2048 characters; `extra.targets_n` gives the total when more). No other
  argument value or name is ever logged for an MCP call. A row is also capped at 32 KiB in total:
  extra targets are dropped (`extra.targets_truncated`), then long strings are shortened with a
  `...[truncated]` marker.

Limits to know about:

- **Only the arguments you list are checked.** A path argument you do not name (for example
  the `source` of a move when you configured only `destination`) is not looked at.
- **Nested arguments cannot be specified.** An argument name is a top-level key of the tool's
  input; there is no dotted or indexed form.
- **Tool and argument names are case-sensitive**, and tool names must match exactly (no
  wildcards).
- **A configured tool that the harness matcher does not route to the hook is ungated.** Keep
  the matcher and this variable in step (the `^mcp__` matcher routes every MCP tool).
- **Parsing:** entries are separated by `,`; whitespace around names is ignored; arguments are
  joined by `+`; an entry with an empty name is dropped; `tool:` or `tool:+` (no usable
  argument name) is treated as a bare name, i.e. default-deny.

## Writer scripts

`python3 externalized-memory/atomic-write.py STATE.md` is a Bash command, and `allowed_commands`
matches by prefix, so an entry admitting it would otherwise let the script write anywhere. A
segment that runs a script whose **basename** is on the writer list is treated as a file write
to the script's target argument. The target is gated exactly like a `>` redirect target: log
self-protection (raw and resolved forms), then always-writable directories, then the plan's
`allowed_paths`. The segment must still be admitted by `allowed_commands`; the two checks are
independent.

Recognized forms:

- an interpreter: `python`, `python3`, `python3.N`, `python3.13t`, `python3-dbg`, `pythonw`,
  `pypy`, `pypy3`, `py` (with or without `.exe`, any directory prefix), including `py -3` and
  `py -V:...`;
- interpreter options before the script, including ones that take a value
  (`-W x`, `-X x`, `--check-hash-based-pycs x`) and combined short flags (`-uX utf8`); `-m` and
  `-c` mean no script file, so those are unaffected;
- behind `VAR=val`, `&`, and the wrappers `env` (with `-i`, `--`, `-u NAME`, `VAR=val`),
  `time`, `sudo`, `nice`, `timeout DURATION`, `nohup`, `exec`, `command`, each with its options;
- direct execution, where the program itself is a writer (`./atomic-write.py STATE.md`).

**Not recognized** (the writer's target is then not gated; only `allowed_commands` applies):
other launchers or runners (`uv run`, `poetry run`, `pipx run`, `xargs`, `find -exec`,
`bash -c "..."`, `sh script`), shell functions and aliases, `python -m runpy path`, and any
wrapper not listed above. Admit writers in `allowed_commands` only by the narrow prefix.

- **Built in:** `atomic-write.py`, target = its first (only) positional argument.
- **Extend:** `PLAN_GATE_WRITER_SCRIPTS=name.py:argpos,other.py:argpos`. `argpos` is **1-based**
  (1 to 64): 1 is the script's first positional argument, 2 the second. Positional arguments
  are words not starting with `-`; everything after a bare `--` is positional. `name.py` is a
  bare file name ending in `.py` (no `:`, `;`, spaces or path separators), matched by lowercased
  basename. An entry for a built-in name overrides its `argpos`. Entries are separated by `,`.
- **Only one target per writer is checked.** A writer script that has other output options or
  writes to several files is not a good fit.
- **Options before the target are refused.** An option (a `-`-prefixed word with no `=`, other
  than a lone `--`) before the target position is denied, because it may take a separate value
  word and shift which argument is the target (`--encoding ALLOWED /etc/evil`). Write options
  as `--flag=value`, or after the target. `atomic-write.py` takes exactly one argument, so it
  is unaffected.
- **Deny (fail closed):** the target is missing; the target, or any argument before it, uses a
  shell expansion (`$VAR`, `$(...)`, backticks), a glob (`* ? [`) or a brace; the target is
  relative after a `cd` in the same command; the command cannot be tokenized.
- **Malformed `PLAN_GATE_WRITER_SCRIPTS`** (no `:`, a name that is not a bare `*.py` file name
  (so `a.py:1 b.py:2`, `a.py:2;b.py:1`, `a.py:2:b.py:1` and `a.py:1:2` are all malformed),
  `argpos` not an integer from 1 to 64): every Bash/PowerShell command that is not read-only
  is denied with a message naming the variable, until it is fixed or `PLAN_GATE_BYPASS` is
  set. Read-only commands are not affected.
- Decision-log class for these denials: `writer_script` (or `log_self_protect` for a log-dir
  target).

**PowerShell:** the command is split on `; | & ( ) { }` and newlines (quotes, including the
doubled-quote escape, are honored) only to find writer scripts. PowerShell redirections (`>`),
`Out-File`, `Set-Content`, `Add-Content` and other cmdlet writes are **not** parsed or gated;
they are controlled only by `allowed_commands`. The read-only fast path applies only to a single
simple command; a command containing `; | & < > ( ) { }`, a newline or a backtick is checked
against `allowed_commands` instead.

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
inline to proceed anyway — that is honored on the fault path too and logged as
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
python plan-gate.py verify-log [path]   # exit 0 = chain intact, 2 = damage/tampering
```

Concurrent sessions share one log safely (advisory locking). The gate never authorizes a
gated file-write into the log directory itself, no matter what a plan or always-writable
entry says (`log_self_protect`). A logging failure never changes a decision. That
append-only trail is the compliance exhaust described in [`paper.md`](paper.md) §6 —
produced as a byproduct of the gate doing its job, not as a separate feature.
