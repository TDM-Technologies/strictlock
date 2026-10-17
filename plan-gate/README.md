# plan-gate

**A fail-closed, pre-action authorization gate for AI coding agents.**

`plan-gate` is the flagship [StrictLock](../README.md) module. It's a single Python script
wired as a **`PreToolUse` hook**: before an agent edits a file or runs a command, the gate
reads the active *plan* and decides allow/deny. An agent may modify only the exact files and
run only commands matching the command prefixes the active plan enumerates. Everything else
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
- **PowerShell:** the command is checked segment by segment, like Bash. Each segment must be a
  known read-only command or start with an `allowed_commands` entry at a word boundary.
  Parentheses, subexpressions, script blocks, backticks and redirections are denied, and
  file-writing cmdlets (`Out-File`, `Set-Content`, `New-Item`, ...) are denied even if listed;
  use Edit/Write for files.
- **MCP tools:** a tool named in `PLAN_GATE_MCP_WRITE_TOOLS` with an argument spec
  (`tool:path`) has each path in that argument checked like an Edit/Write target. A bare entry
  is denied. An MCP tool you did not list gets no decision from plan-gate, so the harness's
  normal permission prompt applies (a log row is kept). The hook only sees MCP calls if the
  `matcher` routes them to it; the example matcher does (`^mcp__`). Limits: only the
  arguments you name are checked, nested arguments cannot be named, and names are case-sensitive.
  See [`CONFIG.md`](CONFIG.md#mcp-write-tools).
- **Writer scripts:** `python3 atomic-write.py FILE` (and any script listed in
  `PLAN_GATE_WRITER_SCRIPTS`) is gated as a write to FILE, like a `>` redirect. Only one target
  per script is checked.
- **Plan approval and leases:** see [Known limits](#known-limits-today) below.
- Emits `{"hookSpecificOutput": {"permissionDecision": "allow"|"deny", ...}}` on **stdout**.
- Appends every decision, allow and deny, to a hash-chained decision log (when
  `PLAN_GATE_LOG_DIR` is set) — your audit trail. `plan-gate.py verify-log` proves the
  chain, and the gate never authorizes a gated write to the log itself.

It **fails closed** for governance *and for its own bugs*: a payload it cannot parse, or an
unhandled exception inside the gate, produces a **deny** (JSON, exit 0) with a loud message
saying what failed and how to bypass — never a non-zero exit, which a harness treats as
"proceed". The escape hatch is `PLAN_GATE_BYPASS=on`; it is honored even on that
crash path, and every use is logged (`emergency_bypass`). The trade-off is accepted: if the
harness changes its payload protocol, the gate denies until it is updated or bypassed.

**Where to set the bypass.** The hook is started by the harness, not by the agent's shell, so
`PLAN_GATE_BYPASS` (like every `PLAN_GATE_*` variable) reaches it only through the environment
the harness gives it: the environment Claude Code was launched with, or the `env` block in its
settings. Running `export PLAN_GATE_BYPASS=on` in the agent's Bash tool, or prefixing the
agent's command with it, does nothing to the hook. Set it where you set the other
`PLAN_GATE_*` variables, restart the session, and remove it afterwards.

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

## Install plan-gate in a project (first install)

This takes you from a fresh clone to a gate you have seen deny something. Use absolute paths
throughout and do not rely on `~` or `$HOME` expanding (it does not in a settings `env` value),
and note that a plan that lists only relative paths cannot claim a git worktree (step 4).

**1. Get the code, outside the project the agent will work in.**

```bash
git clone https://github.com/TDM-Technologies/strictlock.git /absolute/path/to/strictlock
```

plan-gate is one file with no dependencies: `/absolute/path/to/strictlock/plan-gate/plan-gate.py`.

**2. Check the interpreter you will name in the hook.** Python 3.8 or newer. On macOS and most
Linux systems the command is `python3`; a bare `python` often does not exist.

```bash
python3 --version
```

**3. Make a plans directory and a log directory** (outside the project, for example
`/home/you/.agent/plans` and `/home/you/.agent/logs`).

```bash
mkdir -p /home/you/.agent/plans /home/you/.agent/logs
```

Setting `PLAN_GATE_LOG_DIR` is what turns on the audit log: the hash-chained record of every
decision. It also turns on plan approval: with a log directory set, a plan only takes effect
after a human approves it (step 4b). Without it the gate still decides but leaves no record and
asks for no approval.

**4. Write a plan.** Copy [`examples/sample-plan.md`](examples/sample-plan.md) into the plans
directory and edit it. Three things matter for a first run:

- List every file you want writable under `allowed_paths`, one entry per file.
- **Use absolute paths.** In an ordinary checkout a repo-relative entry works. Inside a git
  worktree (including the ones many agent tools create for each session), a plan claims that
  worktree only if at least one `allowed_paths` entry is an absolute path under it. A plan with
  only relative paths does not claim the worktree, so the gate denies every file write and every
  command outside the read-only set there (read-only commands such as `git status` and `ls`
  still work), whatever else the plan says. The sample plan's relative paths
  are for readability; replace them with absolute ones for your project. Details:
  [`CONFIG.md`](CONFIG.md#path-resolution-notes) and [`AUTHORING.md`](AUTHORING.md) section 3.
- Set `status: active`. With `PLAN_GATE_LOG_DIR` set, that alone does not make the plan live:
  it is live once a human approves it (step 4b). Without a log directory, `status: active` is
  all it takes.

**4b. Approve the plan** (when `PLAN_GATE_LOG_DIR` is set). In a terminal of your own, not the
agent's, run the command below; the log directory must be passed because your shell does not
have the harness environment. It shows the plan's path, size, hash, `allowed_paths` and
`allowed_commands`, and records your approval of those exact bytes when you type `yes`.

```bash
python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py approve \
  /home/you/.agent/plans/my-plan.md --log-dir /home/you/.agent/logs
```

Any later edit to the plan file, even one byte, voids the approval; approve again. If the gate
denies with "marked active but not approved", its message holds this exact command. Details,
including CI use (`--no-tty-check`), are in [`CONFIG.md`](CONFIG.md#plan-approval).

**5. Wire the hook.** Put the `env` and `hooks` blocks from
[`examples/settings.json`](examples/settings.json) into your Claude Code settings file, with your
own absolute paths, `python3` as the interpreter, and the absolute path to `plan-gate.py` from
step 1. Restart the session so the settings load. For any other harness, adapt the
stdin/stdout JSON contract.

**6. Check that the gate is live.** Do not assume the hook is running. First by hand, with the
same environment the settings give it. This proves the script runs and decides; it does not
prove the harness is calling it:

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/absolute/path/not/in/plan.txt"}}' \
  | PLAN_GATE=on PLAN_GATE_PLANS_DIR=/home/you/.agent/plans PLAN_GATE_LOG_DIR=/home/you/.agent/logs \
    python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> {"hookSpecificOutput": {... "permissionDecision": "deny" ...}}
```

Then the check that matters, inside the real session: ask the agent to edit a file that is
**not** in the plan. You should see a deny that starts `plan-gate:`, and a new row should
appear in `/home/you/.agent/logs/plan-gate-decisions.log`. Then confirm the chain is intact,
passing the log path as an argument (the log directory is set in the harness settings, not in
your shell):

```bash
python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py verify-log /home/you/.agent/logs/plan-gate-decisions.log
```

If the out-of-plan edit goes through, or the log gets no new row, the hook is not running (see
the next section).

A full allowed-vs-denied walkthrough is in [`examples/README.md`](examples/README.md).

### The gate fails closed once it runs, but it cannot protect you if it never starts

Everything above about failing closed describes a gate that is running. A hook command that
cannot launch fails in one of two ways, depending on what is wrong:

- **The interpreter is missing** (for example `python` on a system that only has `python3`).
  The shell reports "command not found" (exit 127), which Claude Code treats as a non-blocking
  error, so the agent's action proceeds. The symptoms: an out-of-plan action is not blocked,
  and the decision log gets no rows. This is a silent fail-open.
- **The script path is wrong** (a mistyped or relative path that does not resolve). Python
  reports "can't open file" and exits 2, which Claude Code treats as a blocking error for a
  `PreToolUse` hook, so **every** tool call is blocked with a "can't open file" message. This
  is loud, not silent, but it is not the gate deciding anything.

This is why step 6 is part of the install, and why it is worth repeating whenever you change the
hook command, the interpreter, or the location of the code.

## Known limits today

These are the places where the gate does less than a reader might assume. The source of truth
is [`CONFIG.md`](CONFIG.md).

- **Plan approval needs a log directory, and is tamper-evident, not authentication.** With a
  log directory set, a plan takes effect only after someone runs `plan-gate.py approve` on its
  exact bytes. The decision log records who (self-declared OS and git identity), when, and the
  plan's sha256, in a hash chain that shows whether that record was later changed. Without
  `PLAN_GATE_LOG_DIR`, `status: active` alone activates a plan. The latest approval for a plan
  name wins; for retiring an approved plan, see [`CONFIG.md`](CONFIG.md#plan-approval).
  plan-gate blocks the direct ways a gated agent could run `approve` itself, but a plan that
  lets the agent run code it can change can be used to approve another plan. Rotating or
  truncating the log voids every approval. Having the plans directory in
  `PLAN_GATE_ALWAYS_WRITABLE` is fine: a draft does nothing until approved.
- **MCP checks are only as complete as your configuration.** Only listed tools and listed
  arguments are checked (see above); an unlisted MCP tool is left to the harness's own
  permission prompt.
- **Writer scripts:** only the scripts and launch forms listed in `CONFIG.md` are recognized.
  Other launchers (`uv run`, `xargs`, `bash -c "..."`) are not.
- **Leases.** With `PLAN_GATE_SCOPE_LEASE` set to the path of
  [`scope-lease.py`](../scope-lease/scope-lease.py), a gated agent's write to a file another
  agent holds a lease on is denied, on the write surfaces plan-gate parses (Edit/Write/
  NotebookEdit, Bash redirects, writer scripts, MCP path arguments), checking both the spelled
  path and the real path. Agents are told apart by `SCOPE_LEASE_OWNER`, else by the session's
  git branch, so agents sharing a branch count as one. With `PLAN_GATE_REQUIRE_LEASE=on`, every
  such write must hold a lease. Not covered: `cp`/`mv`/`tee`/`sed -i` or build tools admitted
  through `allowed_commands`, hardlinks, nested repos or submodules, and code the agent
  controls. A human's own edits are never blocked.
- **A hook that does not start does not gate** (see the install section above).

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
