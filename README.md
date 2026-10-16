# StrictLock

**Fail-closed action authorization for AI agents in regulated work.**

AI agents don't just write text anymore — they *take actions*. They edit files, run
commands, call APIs, and write to systems of record. In regulated work, an action the
operator never approved isn't a bad paragraph; it's a disclosed record, an unauthorized
transaction, a reportable event.

Today those agents are governed almost entirely by **prose** — "always ask before
deleting," "never touch production." Prose governance **fails open**: the instruction is
advisory, the model must choose to honor it every turn, and under momentum it's the first
thing to slip. When it slips, nothing catches the action.

StrictLock moves governance **out of the prose and into a structural gate** at the tool
boundary. Every file edit, shell command, and recognized external-write tool call is
intercepted *before it runs* and checked against an explicitly approved plan. Unapproved, ambiguous, or surprising → **deny**. The agent
can't talk its way past a check that executes in code it doesn't control. And the same gate
emits a per-decision audit trail as a byproduct — the evidence SOC 2 and ISO ask for.

> **fail-closed** is the principle. **StrictLock** is the suite. **plan-gate /
> commit-msg-gate / memory-cap / externalized-memory / generated-sink gates** and the
> others in the table below are the modules.

## Modules

| Module | What it does | Status |
|---|---|---|
| [**plan-gate**](plan-gate/) | Flagship. A pre-action `PreToolUse` hook: an agent may modify only the exact files and run only commands matching the command prefixes an approved plan enumerates. Everything else is denied before it runs. Ships with the formal paper. | ✅ v1 |
| [**commit-msg-gate**](commit-msg-gate/) | A `commit-msg` git hook that requires every commit to name a plan (a `plan: <slug>` line in the message body; with the opt-in `COMMIT_MSG_GATE_PLANS_DIR` the slug must resolve to a plan file in your plans directory) or carry an approved chore/docs prefix — so the version-control trail names the plan behind each change. A local hook: `--no-verify` skips it; an example CI job (one you add to your own workflow) runs the same check on the pushed commits. | ✅ v1 |
| [**memory-cap**](memory-cap/) | A `PreToolUse` hook that structurally caps the size of an agent's auto-loaded memory index (length of each entry by default; entry-count and total-size caps opt-in), keeping per-session context cost in check instead of relying on a convention nobody enforces. | ✅ v1 |
| [**externalized-memory**](externalized-memory/) | The shared blackboard: one on-disk state file as the source of truth across stateless agent sessions, written atomically. Ships as templates + an atomic-write reference + the design write-up — plus a **projection bundle** (deterministic, git-free/clock-free renderer + fenced-region splicing + UTC guard) for a reproducible status projection. | ✅ v1 |
| [**generated-sink-commit-gate**](generated-sink-commit-gate/) | A `pre-commit` git hook: if a staged change touches a configured source, the gate re-runs the generator in `--check` mode and refuses the commit unless the checked-in generated artifact (manifest/index/schema/README) is a byte-exact regeneration. Refuses a commit whose staged generated file is stale (fail-closed at commit time; `--no-verify` skips it). | ✅ v1 |
| [**generated-sink-prepush-gate**](generated-sink-prepush-gate/) | A `pre-push` git hook: the last-line backstop that validates the terminal sink in your working tree on *every* push (catching a stale artifact that slipped past commit time via `--no-verify` or a one-shot bypass). The hook checks your tree (fast, local); CI checks the pushed commit (authoritative) — a CI job you add to your own workflow, copied from the module's examples. Fail-closed; its bypass is isolated from the commit gate's. | ✅ v1 |
| [**eslint-plugin-strictlock**](eslint-plugin-strictlock/) | The suite's first JS sibling, **published to npm** as [`eslint-plugin-strictlock`](https://www.npmjs.com/package/eslint-plugin-strictlock). Its `no-smoke-only-assertions` rule fails the lint when a whole test block asserts ~nothing (`toBeDefined` / bare `expect(x)` / `toContain`-only) — catching false-safety-net tests before they ship. Builds + tests in-repo. | ✅ v0.1.1 |
| [**scope-lease**](scope-lease/) | Concurrency flagship. A git-native, zero-service exclusive lock over a path set (`refs/locks/*` compare-and-swap, off-branch, no daemon) so N autonomous agents never edit the same file at once. The multi-agent extension of plan-gate: enumerated paths → enumerated *and exclusive*. Reads its path set from an active plan-gate plan by default, or `--paths`/stdin/own-frontmatter standalone. Never walls a human's manual merge. | ✅ v1 |
| [**sink-resolver**](sink-resolver/) | Concurrency / Gates. Deterministic auto-resolution of generated-file merge conflicts — a pure render has no merge, it has a *regenerate*. `merge=binary` on the sink + a `resolve` that regenerates from the merged sources, byte-checks, and **escalates any non-sink conflict**, plus a `check` CI backstop for web-UI merges. A coverage guard refuses to finalize a sink the generator didn't actually rewrite. Sound only for a pure, `--check`-able render — loud precondition on the tin. | ✅ v1 |
| [**liveness-scan**](liveness-scan/) | Concurrency / Meta. A read-only reporter that triages a fleet of agent worktrees — running / stalled / done-unmerged / ambiguous / idle — on a heartbeat-or-commit-mtime signal, attributing each to its plan-gate session. **Report-only: never reaps, exits 0 always**; ambiguous stalls escalate with a non-destructive recovery command. Completes the concurrency family. | ✅ v1 |
| [**test-protection-guard**](test-protection-guard/) | Gates. An **advisory** co-commit-coupling detector: when an *existing* test assertion is changed/removed in the same commit as non-test source — the "rewrite the failing test to match the buggy output" move — it flags + logs it unless a `TEST-CORRECTNESS:` justification is present. **Always allows, never blocks**; the teeth are the close-out log review. Dual-mode: a Claude Code `PreToolUse` hook *or* a universal git `pre-commit` hook over one shared core. The detective complement to the born-weak `eslint-plugin-strictlock`. | ✅ v1 |
| [**rituals**](rituals/) | Rituals. **Prose, not a gate** — it enforces nothing. The kickoff and close-out checklists, the unit-of-work interaction analysis (prior-unit deferred-marker grep + peer-unit collision pass), and the pre-work currency check: the judgment-shaped discipline around a plan that no hook can check, wired to the gates that *can* back each step ([`plan-gate`](plan-gate/), [`commit-msg-gate`](commit-msg-gate/), [`externalized-memory`](externalized-memory/), [`scope-lease`](scope-lease/)). Each step carries a "why this step exists" note from a real miss. | ✅ v1 |

Each module is **standalone** — adopt one without the others — plain files with no
machine-specific defaults baked in; the Python and shell gates are **configured entirely by
environment variables** (the ESLint plugin takes ESLint rule options instead).

See [**roadmap.md**](roadmap.md) for the planned sibling modules (the governance-of-governance
work and more) and what has shipped since launch.

## The thesis, in four moves

1. **Put the rule at the tool boundary, not in the prompt.** A pre-action hook that checks
   every file edit, shell command, and recognized external-write call against an approved plan
   changes your risk posture immediately.
2. **Fail closed.** When an action is unapproved, ambiguous, or surprising, block it and
   stop for a human. Bias the whole system toward consent over completion.
3. **Enumerate; never grant broadly.** Authorize the exact files and enumerated command
   prefixes — not the directory, not the tool. Broad grants are fail-open invitations.
4. **Let the floor be your audit trail.** Log every gate decision and you've already
   produced most of what SOC 2 and ISO ask you to evidence.

The full argument is in [`plan-gate/paper.md`](plan-gate/paper.md).

## 60-second quickstart (plan-gate)

The modules target the [Claude Code](https://docs.claude.com/en/docs/claude-code)
`PreToolUse` hook convention, but the mechanism is harness-agnostic — adapt the JSON
contract to whatever agent runtime you use.

```bash
# 1. Point the gate at a plans directory and turn it on.
export PLAN_GATE=on
export PLAN_GATE_PLANS_DIR="$HOME/.agent/plans"

# 2. Write an approved plan (see plan-gate/SCHEMA.md and plan-gate/examples/).
#    A plan enumerates allowed_paths and allowed_commands in YAML frontmatter.

# 3. Wire plan-gate.py as a PreToolUse hook (see plan-gate/examples/settings.json).
#    Now the agent can only touch what the active plan names.
```

Full configuration (all `PLAN_GATE_*` env vars, on Windows/Linux/macOS) is in
[`plan-gate/CONFIG.md`](plan-gate/CONFIG.md).

## Design principles

- **Fail closed.** When in doubt, deny. A bug in the gate fails toward safety: if plan-gate's
  own code crashes or receives an unparseable payload, it denies with a loud message (the
  emergency bypass is still honored, and logged).
- **Least privilege by construction.** Exact-path, enumerated-command-prefix authorization,
  scoped per unit of work and then retired.
- **Compliance as exhaust.** The audit trail is a byproduct of the gate doing its job — not
  a separate feature you build. See [`COMPLIANCE.md`](COMPLIANCE.md) for the suite-wide
  SOC 2 / ISO 42001 / ISO 9001 control mapping.
- **No magic.** Plain hooks, plain config, no daemon, no service. Read the code; it's small.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 TDM Technologies LLC.
