# StrictLock

**Checks what an AI agent is about to do against a written plan, and refuses anything the plan
does not list.**

AI agents don't just write text anymore — they *take actions*. They edit files, run
commands, call APIs, and write to systems of record. In regulated work, an action the
operator never approved isn't a bad paragraph; it's a disclosed record, an unauthorized
transaction, a reportable event.

Today those agents are governed almost entirely by **prose** — "always ask before
deleting," "never touch production." Prose governance is advisory: the model has to choose to
honor it on every turn, and under momentum it is the first thing to slip. When it slips,
nothing catches the action.

StrictLock moves the rule **out of the prose and into code that runs at the tool boundary**.
Before a file edit or shell command runs, the gate checks it against a plan that lists the
exact files and command prefixes the work may use. If the action is not listed, or the gate
cannot tell, the answer is **no**. The agent cannot talk its way past a check that runs in
code it doesn't control. If you turn logging on, the same gate also writes a record of each
decision, which is the kind of evidence SOC 2 and ISO audits ask for.

> **Fail closed** means: when the gate is unsure, it refuses. The opposite, failing open, is
> what advisory prose does. Two limits to know up front. A gate that never starts does not
> gate (see [what is and isn't enforced today](#what-is-and-isnt-enforced-today)), and
> recorded plan approval needs a decision-log directory to be set.

**Terms used below.** The *harness* is the agent tool you run, such as Claude Code. A *hook* is
a small program the harness runs at a set point; a `PreToolUse` hook runs just before each tool
call (an edit, a shell command) and can allow or refuse it. The hook's *matcher* is the setting
that names which tools trigger it. A *hash-chained* log stores in each row a fingerprint of the
row before it, so changing or deleting an earlier row can be detected. A *worktree* is a separate
checked-out copy of a repository that shares its history, which many agent tools create per
session. A *generated file* is one built from other files, such as an index or manifest (module
names call it a "sink").

## Modules

Each module is **standalone** — adopt one without the others. They are plain files with no
machine-specific defaults; the Python and shell modules are configured entirely through
environment variables, and the ESLint plugin through ESLint rule options. Detail, caveats and
configuration live in each module's own README.

The modules fall into four families:

- **Gates** — checks that allow or refuse an action, or flag it.
- **Hygiene** — keeping an agent's memory and state small and durable.
- **Concurrency** — several agents working in the same repository.
- **Rituals** — written checklists for the judgment calls no hook can check.

| Module | Family | What it does | Status |
|---|---|---|---|
| [**plan-gate**](plan-gate/) | Gates | The flagship: before an agent edits a file or runs a command, checks it against a written plan and refuses anything the plan does not list. | v1 |
| [**commit-msg-gate**](commit-msg-gate/) | Gates | A git hook that rejects a commit unless its message names a plan or uses an approved prefix such as `docs:`. | v1 |
| [**generated-sink-commit-gate**](generated-sink-commit-gate/) | Gates | A git hook that refuses a commit when a generated file (an index, manifest or schema) is not an exact regeneration from its source. | v1 |
| [**generated-sink-prepush-gate**](generated-sink-prepush-gate/) | Gates | A git hook that re-checks generated files at push time, as a second line behind the commit-time check. | v1 |
| [**test-protection-guard**](test-protection-guard/) | Gates | Flags, but never blocks, a change to an existing test assertion made in the same commit as source code, unless the commit says why. | v1 |
| [**spill-gate**](spill-gate/) | Gates | A git hook that refuses a commit or push carrying private keys, tokens, stray UUIDs or machine paths, with an allowlist kept in the repo. Reduces spill risk; does not eliminate it. | v1 |
| [**eslint-plugin-strictlock**](eslint-plugin-strictlock/) | Gates | An ESLint rule ([on npm](https://www.npmjs.com/package/eslint-plugin-strictlock)) that fails the lint when a whole test asserts almost nothing. | v0.1.1 |
| [**memory-cap**](memory-cap/) | Hygiene | A hook that refuses edits that would make an agent's auto-loaded memory index too long. | v1 |
| [**externalized-memory**](externalized-memory/) | Hygiene | Templates and a small script for keeping project state in one file on disk that every session reads first; the script writes to a temporary file and then renames it, so a reader never sees a half-written file. | v1 |
| [**scope-lease**](scope-lease/) | Concurrency | A git-based lock that agents call to claim a set of files, so a second agent that also asks is refused. | v1 |
| [**sink-resolver**](sink-resolver/) | Concurrency | Resolves merge conflicts in generated files by regenerating them from the merged sources. | v1 |
| [**liveness-scan**](liveness-scan/) | Concurrency | A read-only report on which agent worktrees are still running, stalled, or finished but not merged. | v1 |
| [**rituals**](rituals/) | Rituals | Written checklists for starting a session, closing one out, and checking before you plan a piece of work whether it is still worth doing; it enforces nothing. | v1 |

See [**roadmap.md**](roadmap.md) for planned modules and what has shipped since launch.

## What is and isn't enforced today

The list below states what the code does and where its limits are. The exact behaviour is
in [`plan-gate/CONFIG.md`](plan-gate/CONFIG.md) and [`scope-lease/CONFIG.md`](scope-lease/CONFIG.md).

- **Plan approval.** With a log directory set, a plan takes effect only after someone runs
  `plan-gate.py approve` on its exact bytes. The decision log records who (self-declared OS and
  git identity), when, and the plan's sha256, in a hash chain that shows whether that record was
  later changed. That's tamper-evident, not authentication. plan-gate blocks the direct ways a
  gated agent could run `approve` itself, but a plan that lets the agent run code it can change
  can be used to approve another plan. Without `PLAN_GATE_LOG_DIR`, `status: active` alone
  activates a plan. The latest approval per plan name wins. To retire a plan, archive it and run
  `plan-gate.py revoke` (human-only, like approve); see [`plan-gate/CONFIG.md`](plan-gate/CONFIG.md#plan-approval).
- **MCP tools** (tools provided by connected servers): a tool listed in
  `PLAN_GATE_MCP_WRITE_TOOLS` with an argument spec has each path in that argument checked like
  an Edit/Write target; a bare entry is denied. An unlisted MCP tool gets no decision from
  plan-gate, so the harness's normal permission prompt applies (a log row is kept). Only the
  arguments you name are checked, nested arguments cannot be named, and names are
  case-sensitive. The example matcher routes MCP tools to the gate.
- **Writer scripts.** `python3 atomic-write.py FILE` (and any script you list in
  `PLAN_GATE_WRITER_SCRIPTS`) is checked as a write to FILE, like a `>` redirect. Only one
  target per script is checked, and launchers such as `uv run` or `bash -c` are not recognized.
- **Leases.** With `PLAN_GATE_SCOPE_LEASE` set, plan-gate denies a gated agent's write to a
  file another agent holds a scope-lease on, on the write surfaces it parses (Edit/Write/
  NotebookEdit, Bash redirects, writer scripts, MCP path arguments), and it checks both the
  spelled path and the real path. Agents are told apart by `SCOPE_LEASE_OWNER`, else by the
  session's git branch (agents sharing a branch count as one). With
  `PLAN_GATE_REQUIRE_LEASE=on`, every such write must hold a lease. Not covered: `cp`, `mv`,
  `tee`, `sed -i` and build tools admitted through `allowed_commands`, hardlinks, nested repos
  or submodules, and code the agent controls.
- **PowerShell** commands are checked segment by segment; parentheses, script blocks,
  backticks and redirections are denied, and file-writing cmdlets are denied even if listed.
- **A hook that cannot start does not gate.** A missing interpreter (for example `python` where
  only `python3` exists) makes the harness proceed without the check. A wrong script path makes
  every tool call fail with a "can't open file" error instead. Verify the gate after wiring it
  (below).

## The thesis, in four moves

1. **Put the rule at the tool boundary, not in the prompt.** A pre-action hook that checks
   every file edit and shell command against a written plan changes your risk posture
   immediately.
2. **Fail closed.** When an action is unlisted, ambiguous, or surprising, block it and
   stop for a human. Bias the whole system toward consent over completion.
3. **Enumerate; never grant broadly.** Authorize the exact files and enumerated command
   prefixes — not the directory, not the tool. Broad grants are invitations to fail open.
4. **Keep a record of every decision.** Turn on the decision log and you have already
   produced most of what SOC 2 and ISO ask you to evidence.

The full argument is in [`plan-gate/paper.md`](plan-gate/paper.md).

## Install plan-gate

The modules target the [Claude Code](https://docs.claude.com/en/docs/claude-code)
`PreToolUse` hook convention, but the mechanism is harness-agnostic — adapt the JSON
contract to whatever agent runtime you use.

The step-by-step path, from a fresh clone to a gate you have seen deny something, is in
[**plan-gate/README.md: Install plan-gate in a project**](plan-gate/README.md#install-plan-gate-in-a-project-first-install).
In short:

1. Clone this repository somewhere the agent does not work, and use the absolute path to
   `plan-gate/plan-gate.py` in the hook command.
2. Name the interpreter `python3` in the hook command, and check `python3 --version` is 3.8+.
3. Set `PLAN_GATE=on`, `PLAN_GATE_PLANS_DIR` and `PLAN_GATE_LOG_DIR` (all absolute paths).
4. Write a plan with **absolute** `allowed_paths`; a plan with only relative paths cannot claim
   a git worktree, and the gate then denies every file write and every command outside the
   read-only set there.
5. Wire the hook (see [`plan-gate/examples/settings.json`](plan-gate/examples/settings.json)).
6. **Check the gate is live:** ask the agent to edit a file that is not in the plan. You should
   see a `plan-gate:` deny and a new row in the decision log.

**Why step 6 is not optional.** If the interpreter named in the hook command does not exist
(for example `python` on a system that only has `python3`), Claude Code treats the "command not
found" as a non-blocking error, so the agent's action proceeds with no deny and no log row. If
the script path is wrong, the interpreter exits with "can't open file", which blocks every tool
call. Either way the gate is not deciding anything. It fails closed only once it is running.

Full configuration (all `PLAN_GATE_*` variables, on Windows, Linux and macOS) is in
[`plan-gate/CONFIG.md`](plan-gate/CONFIG.md).

## Switches and bypass values

Every module's on-switch and bypass variable accepts `on`, `1`, `true` or `yes`, in any case
(for example `PLAN_GATE_BYPASS=1`, `MEMORY_CAP_BYPASS=on`, `COMMIT_MSG_GATE_BYPASS=true`).
Values such as `PLAN_GATE_BYPASS=1`, which used to do nothing, now bypass. Any other value
leaves the switch off. All variables are read from the process environment. `scope-lease`
and `sink-resolver` run unless their switch is set to something other than those four values.

**Where the variable has to be set.**

- **Hooks run by the agent harness** (plan-gate, memory-cap, test-protection-guard's agent mode)
  are started by the harness, not by the agent's shell. They see the environment the harness was
  launched with and the `env` block in its settings. `export X=on` in the agent's own Bash
  tool, or typing `X=on` before the agent's command, does **not** reach them. Set the variable
  where you set the gate's other variables, then restart the session.
- **Git hooks** (commit-msg-gate, generated-sink-commit-gate, generated-sink-prepush-gate,
  test-protection-guard's pre-commit mode, spill-gate, the sink-resolver `post-merge` example) run inside
  whatever process calls git. The variable must be present in the environment of **every** such
  process: a terminal you did not export it in, a GUI git client, an IDE's commit button, a
  script or CI job. Where it is missing, an on-switch means the gate is simply off for that
  commit, and a bypass variable does not apply. Set it for the processes you mean, and for
  one-off bypasses prefer an inline prefix on the git command itself
  (`COMMIT_MSG_GATE_BYPASS=on git commit ...`).
- `git commit --no-verify` and `git push --no-verify` skip git hooks entirely.

## Design principles

- **Fail closed.** When in doubt, deny. If plan-gate's own code crashes or receives a payload
  it cannot parse, it denies with a loud message (the emergency bypass is still honored, and
  logged). This covers a gate that is running; a hook that fails to start does not gate.
- **Least privilege by construction.** Exact-path, enumerated-command-prefix authorization,
  scoped per unit of work and then retired.
- **Evidence from the gate itself.** With `PLAN_GATE_LOG_DIR` set, each decision is written to
  a hash-chained log as the gate makes it; it is opt-in, not automatic. See
  [`COMPLIANCE.md`](COMPLIANCE.md) for the suite-wide SOC 2 / ISO 42001 / ISO 9001 control
  mapping.
- **No magic.** Plain hooks, plain config, no daemon, no service. Read the code; it's small.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 TDM Technologies LLC.
