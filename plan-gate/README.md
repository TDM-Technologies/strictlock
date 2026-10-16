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
- **MCP tools:** tools named in `PLAN_GATE_MCP_WRITE_TOOLS` are denied while a plan is active.
  Their arguments are not read, so a configured tool is never allowed, even for a path the plan
  lists. The gate only sees tool calls the hook is wired to: the example `matcher` in
  [`examples/settings.json`](examples/settings.json) does not include MCP tools, so add them to
  the matcher (for example `mcp__.*`) or the gate never sees them. Unconfigured MCP tools are
  not gated.
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
throughout: the harness does not expand `~` or `$HOME` inside a settings file, and a plan that
lists only relative paths cannot claim a git worktree (step 4).

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
decision. It is opt-in, so without it the gate still decides but leaves no record. A later
release (see [Known limits](#known-limits-today)) will require the log directory for plan
approval, so set it now.

**4. Write a plan.** Copy [`examples/sample-plan.md`](examples/sample-plan.md) into the plans
directory and edit it. Three things matter for a first run:

- List every file you want writable under `allowed_paths`, one entry per file.
- **Use absolute paths.** In an ordinary checkout a repo-relative entry works. Inside a git
  worktree (including the ones many agent tools create for each session), a plan claims that
  worktree only if at least one `allowed_paths` entry is an absolute path under it. A plan with
  only relative paths does not claim the worktree, so the gate denies every write and every
  non-read-only command there, whatever else the plan says. The sample plan's relative paths
  are for readability; replace them with absolute ones for your project. Details:
  [`CONFIG.md`](CONFIG.md#path-resolution-notes) and [`AUTHORING.md`](AUTHORING.md) section 3.
- Set `status: active`. Today this is how a plan becomes live; nothing records who set it (see
  [Known limits](#known-limits-today)).

**5. Wire the hook.** Put the `env` and `hooks` blocks from
[`examples/settings.json`](examples/settings.json) into your Claude Code settings file, with your
own absolute paths, `python3` as the interpreter, and the absolute path to `plan-gate.py` from
step 1. Restart the session so the settings load. For any other harness, adapt the
stdin/stdout JSON contract.

**6. Check that the gate is live.** Do not assume the hook is running. First by hand, with the
same environment the settings give it:

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/absolute/path/not/in/plan.txt"}}' \
  | PLAN_GATE=on PLAN_GATE_PLANS_DIR=/home/you/.agent/plans PLAN_GATE_LOG_DIR=/home/you/.agent/logs \
    python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py
# -> {"hookSpecificOutput": {... "permissionDecision": "deny" ...}}
```

Then inside the real session: ask the agent to edit a file that is **not** in the plan. You
should see a deny that starts `plan-gate:`, and a new row should appear in
`/home/you/.agent/logs/plan-gate-decisions.log`. Run
`python3 /absolute/path/to/strictlock/plan-gate/plan-gate.py verify-log` to confirm the chain is
intact. If the out-of-plan edit goes through, or the log gets no new row, the hook is not
running (see the next section).

A full allowed-vs-denied walkthrough is in [`examples/README.md`](examples/README.md).

### The gate fails closed once it runs, but it cannot protect you if it never starts

Everything above about failing closed describes a gate that is running. A hook command that
cannot launch (for example `python` on a system that only has `python3`, a mistyped script
path, or a relative path that does not resolve from where the harness runs it) produces a
launch error, not a deny. The harness treats a hook that fails to start as non-blocking, so the
agent's action proceeds. The symptoms: an out-of-plan action is not blocked, and the decision
log gets no rows. This is why step 6 is part of the install, and why it is worth repeating
whenever you change the hook command, the interpreter, or the location of the code.

## Known limits today

These are the places where the gate does less than a reader might assume. Planned changes are
written up in [`../dev/CLAIMS-TRUE-ROUND-2-SPEC.md`](../dev/CLAIMS-TRUE-ROUND-2-SPEC.md); this
section describes what the code does now.

- **Plan approval is a convention.** A plan is active when its frontmatter says
  `status: active`. Nothing records who approved it or when. With the example settings the
  plans directory is always writable (`PLAN_GATE_ALWAYS_WRITABLE`), so a gated agent can write
  a plan with `status: active` itself and it takes effect immediately. If you rely on a human
  approving each plan, keep the plans directory out of `PLAN_GATE_ALWAYS_WRITABLE` and write
  plans yourself. Recorded approval (who, when, and which exact plan bytes) is planned; it is
  not built.
- **MCP tools** are denied outright when named in `PLAN_GATE_MCP_WRITE_TOOLS`, and only if the
  hook `matcher` routes them to the gate (see above). Checking their target paths against the
  plan is planned.
- **Scripts that write files are matched as commands, not by target.**
  `python3 atomic-write.py STATE.md` (from [`externalized-memory`](../externalized-memory/))
  is allowed or denied by its command prefix in `allowed_commands`. The file it writes is not
  checked against `allowed_paths`. An `allowed_commands` entry that admits the script lets it
  write any path the process can write, so listing a state file in `allowed_paths` does not by
  itself limit what that script touches.
- **Leases are not read.** plan-gate does not look at [`scope-lease`](../scope-lease/) locks.
  Two gated agents can both be allowed to edit a file; a lease only excludes agents that call
  `scope-lease.py acquire` themselves.
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
