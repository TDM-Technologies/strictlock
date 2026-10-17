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
> approval of a plan is currently a convention, not a recorded step.

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
| [**eslint-plugin-strictlock**](eslint-plugin-strictlock/) | Gates | An ESLint rule ([on npm](https://www.npmjs.com/package/eslint-plugin-strictlock)) that fails the lint when a whole test asserts almost nothing. | v0.1.1 |
| [**memory-cap**](memory-cap/) | Hygiene | A hook that refuses edits that would make an agent's auto-loaded memory index too long. | v1 |
| [**externalized-memory**](externalized-memory/) | Hygiene | Templates and a small script for keeping project state in one file on disk that every session reads first; the script writes to a temporary file and then renames it, so a reader never sees a half-written file. | v1 |
| [**scope-lease**](scope-lease/) | Concurrency | A git-based lock that agents call to claim a set of files, so a second agent that also asks is refused. | v1 |
| [**sink-resolver**](sink-resolver/) | Concurrency | Resolves merge conflicts in generated files by regenerating them from the merged sources. | v1 |
| [**liveness-scan**](liveness-scan/) | Concurrency | A read-only report on which agent worktrees are still running, stalled, or finished but not merged. | v1 |
| [**rituals**](rituals/) | Rituals | Written checklists for starting a session, closing one out, and checking before you plan a piece of work whether it is still worth doing; it enforces nothing. | v1 |

See [**roadmap.md**](roadmap.md) for planned modules and what has shipped since launch.

## What is and isn't enforced today

The list below describes what the code does now. The first four items have planned changes
written up in [`dev/CLAIMS-TRUE-ROUND-2-SPEC.md`](dev/CLAIMS-TRUE-ROUND-2-SPEC.md); the last is
a property of how hooks work, with no code change planned.

- **Plan approval is a convention.** A plan is live when its frontmatter says
  `status: active`. Nothing records who approved it or when, and with the example settings a
  gated agent can write an active plan itself. Recorded approval is planned, not built.
- **MCP tools** (tools provided by connected servers): the ones you name in
  `PLAN_GATE_MCP_WRITE_TOOLS` are always denied while a plan is active; their arguments are not
  read. The example hook `matcher` does not route MCP tools to the gate, so add them (for
  example `mcp__.*`) or the gate never sees them. Other MCP tools are not gated.
- **Scripts are matched as commands.** Allowing a command such as
  `python3 atomic-write.py STATE.md` does not check the file it writes against `allowed_paths`.
- **Leases are voluntary.** scope-lease excludes only agents that call `acquire` themselves.
  plan-gate does not read lock refs, and any merge-time check on a lease is something you
  build.
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

Every gate has an on-switch or a bypass variable, and the accepted values **differ by module
today**. A value a module does not accept is silently ignored, so for example
`MEMORY_CAP_BYPASS=on` does nothing because that module wants `1`. All variables are read from
the process environment; a planned change will make every module accept `on`, `1`, `true` and
`yes`.

| Module | Turn on | Bypass variable | Value accepted today |
|---|---|---|---|
| plan-gate | `PLAN_GATE=on` (any case) | `PLAN_GATE_BYPASS` | `on` only (any case) |
| memory-cap | `MEMORY_CAP=on` (any case) | `MEMORY_CAP_BYPASS` | `1` only |
| generated-sink-commit-gate | `GENERATED_SINK_COMMIT_GATE=on` (any case) | `GENERATED_SINK_COMMIT_GATE_BYPASS` | `1` only |
| generated-sink-prepush-gate | `GENERATED_SINK_PREPUSH_GATE=on` (any case) | `GENERATED_SINK_PREPUSH_GATE_BYPASS` | `1` only |
| commit-msg-gate | the hook being installed (no switch) | `COMMIT_MSG_GATE_BYPASS` | lowercase `on` only |
| test-protection-guard | `TEST_PROTECTION_GUARD=on` (any case) | none (it never blocks) | not applicable |
| scope-lease | runs unless disabled | `SCOPE_LEASE` as an opt-out | `on`, `1`, `true`, `yes` mean enabled; any other set value disables it |
| sink-resolver | runs unless disabled | `SINK_RESOLVER` as an opt-out | `on`, `1`, `true`, `yes` mean enabled; any other set value disables it |
| liveness-scan | no switch (read-only) | none | not applicable |

One further `1`-only switch: `MEMORY_CAP_DECISION_LOG` (the opt-in decision log).

**Where the variable has to be set.**

- **Hooks run by the agent harness** (plan-gate, memory-cap, test-protection-guard's agent mode)
  are started by the harness, not by the agent's shell. They see the environment the harness was
  launched with and the `env` block in its settings. `export X=on` in the agent's own Bash
  tool, or typing `X=on` before the agent's command, does **not** reach them. Set the variable
  where you set the gate's other variables, then restart the session.
- **Git hooks** (commit-msg-gate, generated-sink-commit-gate, generated-sink-prepush-gate,
  test-protection-guard's pre-commit mode, the sink-resolver `post-merge` example) run inside
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
