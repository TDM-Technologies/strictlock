# plan-gate configuration

Everything is configured through **environment variables** — no machine-specific defaults
are baked into the script. If `PLAN_GATE` is not `on` / `1` / `true` / `yes`, the hook is inert and allows
everything.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_GATE` | yes | Set to `on` / `1` / `true` / `yes` (case-insensitive, whitespace-trimmed) to enable the gate. Anything else makes the hook inert (allow-all). |
| `PLAN_GATE_PLANS_DIR` | yes | Directory holding plan files. Exactly one plan may be `status: active` at a time (scoped per git worktree). |
| `PLAN_GATE_ALWAYS_WRITABLE` | no | `os.pathsep`-separated list of directories that are *always* writable regardless of plan state. Intended for the plans dir itself and any agent-memory dir that never ships. Same blast radius as plan files — keep it tight. Putting the plans dir here lets a gated agent write a plan with `status: active` and have it take effect at once; leave it out if a human should write the plans. |
| `PLAN_GATE_LOG_DIR` | no (recommended) | Directory for the append-only, hash-chained decision log (`plan-gate-decisions.log`). This log is your audit trail and it is opt-in: if unset, no log is written. Setting it also turns on plan approval (see `PLAN_GATE_REQUIRE_APPROVAL`). |
| `PLAN_GATE_LOG_DECISIONS` | no | `all` (default) records every decision on a gated surface, allow and deny; `deny` records denials plus emergency-bypass rows only. |
| `PLAN_GATE_REQUIRE_APPROVAL` | no | Plan approval (see [Plan approval](#plan-approval)). Required **whenever `PLAN_GATE_LOG_DIR` is set**; only an explicit `off` / `0` / `false` / `no` (case-insensitive) turns it off. Any other non-empty value counts as on, and with no log dir that is a configuration error (every gated call that needs a plan is denied with a message). Unset or empty: follows `PLAN_GATE_LOG_DIR`. |
| `PLAN_GATE_MCP_WRITE_TOOLS` | no | Comma-separated MCP tools that modify external state. Each entry is `tool` (a bare name: **default-denied**, arguments never read) or `tool:arg` / `tool:arg1+arg2` (the argument(s) that hold target paths; see below). |
| `PLAN_GATE_WRITER_SCRIPTS` | no | Extra "writer scripts", comma-separated `name.py:argpos`. A Bash/PowerShell command that runs `python`/`python3`/`py` on a listed script is treated as a file write to that script's target argument and gated like a `>` redirect target (see below). `atomic-write.py:1` is built in. A malformed value **fails closed**. |
| `PLAN_GATE_SCOPE_LEASE` | no | Path to `scope-lease.py`. When set, every file-write target (Edit/Write/NotebookEdit `file_path`, Bash redirect targets, writer-script targets, MCP path arguments) is looked up with `scope-lease.py holder` before the plan check; a path leased by another agent is denied (see [scope-lease honored](#scope-lease-honored)). Unset: no lookup, no subprocess. |
| `PLAN_GATE_SCOPE_LEASE_TIMEOUT` | no | Seconds to wait for the `holder` lookup (default `10`). A timeout denies. |
| `PLAN_GATE_REQUIRE_LEASE` | no | `on` / `1` / `true` / `yes`: a write to a path nobody holds a lease on is also denied ("acquire it first"). Needs `PLAN_GATE_SCOPE_LEASE`; without it every file write is denied as a configuration error. Default off. |
| `PLAN_GATE_BYPASS` | no | Emergency escape hatch. `on` / `1` / `true` / `yes` (case-insensitive) disables the gate for the call. It reaches the hook only through the harness environment (the env Claude Code was started with, or the `settings.json` `env` block), not as a prefix on an agent's command or an `export` in the agent's Bash tool; every use is logged. Also honored when the gate itself faults. Set it, restart the session, and remove it afterwards. |
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

**PowerShell:** checked segment by segment, like Bash. The command is split on `;`, `|`, `||`,
`&`, `&&` and newlines outside quotes (single and double quotes, including the doubled-quote
escape, are honored; a leading `&` call operator stays with its command). **Each segment** must
be a known read-only command, or start with an `allowed_commands` entry **at a word boundary**
(so `npm test` admits `npm test -- x` but not `npm testing`). Writer-script detection applies per
segment. The gate refuses outright (fail closed, class `unparseable_command`) a command that
contains anything it cannot check: a backtick, `$(` or `@(`, parentheses, a script block `{ }`,
a redirection (`>`, `>>`, `2>`, `<`), or an unterminated quote. It also refuses (class
`ps_unchecked_write`) a segment that runs a file-writing cmdlet whose target it cannot check
(`Out-File`, `Set-Content`, `Add-Content`, `New-Item`, `Copy-Item`, `Move-Item`, `Tee-Object`,
`Export-Csv`, `Export-Clixml` and their common aliases), even if `allowed_commands` lists it; use
Edit/Write, which is gated by `allowed_paths`. Earlier versions matched the whole command against
`allowed_commands` by prefix, so an allowed prefix followed by `; other-command` was admitted;
that no longer works.

## Plan approval

A plan is active only if it says `status: active` **and** the decision log holds an `approve`
row for that plan's name and the sha256 of its **whole file as it is now**. Any edit after
approval (one byte, including the `status` line) voids it.

```
python plan-gate.py approve <plan.md> --log-dir <DIR> [--plans-dir <DIR>] [--no-tty-check]
```

- Run it yourself, in a terminal. The log directory comes from `--log-dir`, else
  `PLAN_GATE_LOG_DIR` (your shell usually does not have the harness environment, so pass
  `--log-dir`; without either the command exits nonzero). It **refuses without an interactive
  terminal on stdin** (exit 3) unless `--no-tty-check` is given (CI / scripted approval); the
  flag is recorded on the row.
- Before recording anything it prints the plan's absolute path, size, sha256, the log it will
  write to, and the plan's `allowed_paths` and `allowed_commands` as the gate parses them.
  On a terminal you must then type `yes`; anything else aborts and records nothing. With
  `--no-tty-check` it prints the same and does not prompt.
- `<plan.md>` is a path, or a file name inside the plans directory (`--plans-dir`, else
  `PLAN_GATE_PLANS_DIR`); a relative name is looked up there first, not in the current
  directory. When a plans directory is known, a plan that is not directly inside it is refused.
  A plan's approval is tied to its **file name** and its bytes (a renamed or copied file needs
  its own approval).
- **Latest approval wins.** For each plan name only the most recent `approve` row counts.
  Approving a new version retires the earlier approval, and restoring older approved bytes does
  not reactivate the plan (approve it again). There is no expiry.
- An `approve` row counts only if it sits on an unbroken chain link (its `prev` is the hash of
  the log line right before it) and its own hash verifies; a row copied in from another log, or
  edited, is ignored. The gate streams the log in 1 MiB blocks and parses only approve rows
  (bounded memory). **Rotating, truncating or archiving the log voids every approval**: plans
  must then be approved again.
- The row is appended to the same hash-chained log under the same lock. Fields (all strings):
  the usual `ts`, `decision` / `surface` / `class` = `approve`, `tool` = `plan-gate approve`,
  `target` (absolute plan path), `plan` (file name), `cwd`, plus `sha256`, `os_user`,
  `git_user_name`, `git_user_email` (from `git config` in the plan's directory; empty if
  unavailable), `hostname` and `no_tty_check` (`"true"` / `"false"`).
- An unapproved `status: active` plan is **inert** (it does not count toward the one-active
  rule either) and the gate denies with: *plan X is marked active but not approved (or changed
  since approval). Ask a human to run, in a terminal: `<python> <abs path to plan-gate.py>
  approve <abs plan path> --log-dir <abs log dir this gate reads>`* (decision class
  `plan_not_approved`). Read-only commands and always-writable directories stay available, so
  an agent can keep drafting plans.
- `plan-gate.py verify-log` still verifies the chain and also lists every approve row
  (who, when, which plan hash), marking rows the gate would ignore (`BROKEN LINK, not
  honoured`, `ROW ALTERED, not honoured`) and older rows replaced by a later approval
  (`superseded by line N`). Altering an approve row fails the chain check.
- A gated Bash/PowerShell command that runs `plan-gate.py approve` is **denied** (class
  `approve_self_protect`): interpreter + `plan-gate.py` + `approve` in any spelling the
  writer-script detection recognizes, `plan-gate.py` executed directly, a script or subcommand
  hidden behind a variable expansion, and shell / `eval` / `-c` style runners whose words
  mention both. An agent cannot write the log directory either (`log_self_protect`), so it
  cannot forge a row. Indirection through some other script is not parsed; the terminal
  requirement and the protected log do not depend on parsing.
- **What this is, and is not.** The approver's identity is the OS user and git config the person
  themselves set. It shows approval happened outside the gated agent, at a recorded time, for
  exact plan bytes, and the chain shows whether the record was altered. It is tamper-evident,
  not authentication: someone with a shell can approve as anyone they can configure. The
  terminal check is not authentication either.
- **Honest limits.** Spotting the `approve` command is best effort. plan-gate gates tool calls,
  not what an allowed program does: an approved plan that admits running code the agent can
  change (`python3`, `make`, `npm test`, a script the agent can edit) can be used to approve
  another plan. Keep such entries out of plans you do not fully trust, or approve plans from
  a tree the agent cannot write.
- **Windows terminals.** On Windows a real console is required (`GetConsoleMode`); a NUL stdin
  does not count. Git Bash / mintty without `winpty` may not present a console to Python. Then
  run it through `winpty`, or use `--no-tty-check` (recorded on the row).
- Upgrading: setting `PLAN_GATE_LOG_DIR` now implies approval. Existing plans stay inert
  until approved once; set `PLAN_GATE_REQUIRE_APPROVAL=off` to keep the old behavior.
- Cost: each hook call that reaches the plan check streams the log once to find approve rows
  (tens of milliseconds on a log of tens of MB).

## scope-lease honored

`PLAN_GATE_SCOPE_LEASE=/path/to/scope-lease.py` makes plan-gate respect scope-lease locks for
every gated agent write. All the write targets of one tool call are batched into one
`python scope-lease.py holder --json --root <repo> -- <abs paths>` call per repo (same
interpreter as the hook; the repo is the nearest git root above each target, else the hook's
working directory), run **before** the plan check:

- a path **held by someone else** (a live lease whose lock id is not `SCOPE_LEASE_LOCK_ID` and
  whose owner is not `SCOPE_LEASE_OWNER` / the worktree's branch / `user.email`) is **denied**,
  naming the holder, the lease age and its deadline (decision class `scope_lease`);
- a lookup that errors, times out, returns something unreadable, or whose script is missing is
  **denied** (fail closed) with the bypass how-to;
- free, expired and mine fall through to the normal plan decision;
- `PLAN_GATE_REQUIRE_LEASE=on` additionally denies a path with no live lease (or an expired
  one): "acquire it first". Targets under `PLAN_GATE_ALWAYS_WRITABLE` (plans, memory) are
  exempt from that rule only, so drafting plans keeps working.

The owner identity is read from the hook's environment, so export the same `SCOPE_LEASE_OWNER`
(or `SCOPE_LEASE_LOCK_ID`) in the harness environment that the agent used when it ran
`acquire`. Canonicalization (case and Unicode folding, repo-relative keys) happens only inside
scope-lease. Reads are never looked up; a human's own edits or merges never pass through the
hook, so a lease never blocks a person. `PLAN_GATE_BYPASS` skips the lookup.

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
`log_self_protect`, `silent_drift`, `unparseable_command`, `gate_error`, `scope_lease`,
`plan_not_approved`, `approval_config`, `approve_self_protect`; plus `approve` rows written by
`plan-gate.py approve`), the reason, the
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
