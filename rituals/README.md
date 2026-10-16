# rituals

**The kickoff, close-out, and planning discipline the gates can't enforce mechanically.**

A [StrictLock](../README.md) module (the Rituals family). Everything else in this suite is a
hook, a script, or a template that *executes in code the agent doesn't control*. `rituals` is
the opposite: **prose, no code.** It covers the judgment-shaped steps around a unit of work
that no hook can check, because they require *reading and interpreting* rather than matching a
string:

- "Is this backlog item still worth doing, or has the world moved on?"
- "Did the last unit leave an obligation buried in its code for this one?"
- "Does the unit running beside this one need the same file?"
- "Is the state file the next session will read actually true?"

A gate can deny a write. It cannot tell you that the plan you are about to approve is scoped
around a stale premise. These checklists exist for that gap: they turn the loop
**PLAN → CONFIRM → EXECUTE → CLOSE** from a habit you hope for into a walkthrough you follow.

> This module is **not a gate.** It enforces nothing, blocks nothing, and emits no audit
> record by itself. Its leverage is that the checklists are written to be walked in order, and
> that the steps which *can* be made structural are handed to the gates that do enforce them
> (see "How it composes" below). Where a step is only prose, the checklist says so.

A **work package (WP)** throughout this module means one bounded unit of work with its own plan:
a feature, a fix, a doc pass. "Unit of work" and "WP" are interchangeable.

## What ships

| File | What it is |
|---|---|
| [`session-kickoff.md`](session-kickoff.md) | The start-of-work checklist: verify the gates are armed, read the state file, check for drift, run the planning checks, author and approve the plan, open the branch with a ceremony commit. |
| [`session-close.md`](session-close.md) | The end-of-work checklist: reconcile the state file, archive what closed, capture spillover, read the gate logs, push, and retire the plan **last**. |
| [`wp-interaction-analysis.md`](wp-interaction-analysis.md) | Two planning lenses: the **prior-unit deferred-marker grep** (what did the last unit leave for me?) and the **peer-unit collision pass** (what do the units running beside me collide with?) — plus the belt-and-suspenders note on which part a gate now enforces. |
| [`pre-wp-currency-check.md`](pre-wp-currency-check.md) | The three-question rubric and three verdicts for deciding whether a backlog item is still worth pulling into a unit of work. |

## Why these are checklists, not hooks

Each step here exists because skipping it was a *recurring* failure, not a hypothetical one:
a state file that drifted several units behind reality, a closed item left in the "open" table,
a plan locked around a premise that had already been overtaken, a plan archived one step too
early so the push was denied. Every checklist step carries a short **"Why this step exists"**
note so you can tell which steps earn their place and which a given project can drop. If you
find a step that has never once caught anything in your setup, delete it — a ritual that fires
on every run and never changes the outcome trains people to skip all of them.

Two design rules run through all four files:

1. **Report, then lock.** The planning checks produce *findings with evidence*, shown to the
   human *before* the plan is approved. Nothing is decided silently. The human is the
   rate-limiter on judgment calls; the checklist exists to make sure the call is *presented*,
   not to make it.
2. **Prefer the structural version.** Wherever a step can be handed to a gate, it should be.
   The checklists name the gate; they don't pretend prose is a substitute.

## How it composes with the rest of the suite

| Ritual step | Handed to / backed by |
|---|---|
| Verify the gate is armed (env switch on, plans dir set, exactly one plan active) | [`plan-gate`](../plan-gate/) — see [`CONFIG.md`](../plan-gate/CONFIG.md) |
| Author the plan: exact `allowed_paths`, narrow `allowed_commands`, flip-last lifecycle | [`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) and [`SCHEMA.md`](../plan-gate/SCHEMA.md) — the kickoff checklist *calls* these; it does not restate them |
| The kickoff and close-out commits carry a ceremony prefix | [`commit-msg-gate`](../commit-msg-gate/) — `chore(kickoff):` / `chore(handoff):` / `docs:` already pass the default prefixes; override the list with `COMMIT_MSG_GATE_PREFIXES` |
| The state file the next session reads first, and the archive that holds closed history | [`externalized-memory`](../externalized-memory/) — the `Meta / Next Action / Active Threads / Decisions` blackboard and its staleness tripwire |
| Two units running at once must not hold the same file | [`plan-gate`](../plan-gate/)'s cross-plan disjoint check for the shared-file case, and [`scope-lease`](../scope-lease/) for exclusive claims across a fleet of worktrees |
| Generated artifacts regenerated before commit and before push | [`generated-sink-commit-gate`](../generated-sink-commit-gate/) / [`generated-sink-prepush-gate`](../generated-sink-prepush-gate/); merge conflicts on them via [`sink-resolver`](../sink-resolver/) |
| A weakened test assertion in the same commit as a source change | [`test-protection-guard`](../test-protection-guard/) — its log is the close-out review surface |
| Which sessions in a fleet are still alive, stalled, or finished-but-unmerged | [`liveness-scan`](../liveness-scan/) |

Each module stays standalone. You can adopt the checklists without any gate (walk them by hand
and substitute your own mechanism for each row above), or adopt the gates without the
checklists. They are better together because the gates make the rituals *auditable* and the
rituals make the gates' plans *worth approving*.

## How to adopt

1. Copy the four files into your project's docs (or your agent's skill directory — each
   checklist is written so it can be loaded as a skill or a prompt, and walked in order).
2. Replace the bracketed placeholders (`<state-file>`, `<plans-dir>`, `<main-branch>`, your
   test command) with your project's equivalents. Every project-specific name in the
   checklists is a placeholder on purpose.
3. Add a **lessons file** beside each checklist and let it grow. Both checklists carry a
   "Lessons" section distilled from real runs; the pattern that produced them is to append one
   dated, concrete lesson after every run that surprised you, and fold the ones that prove
   durable back into the checklist body. Prune entries that turn out wrong.

## Requirements

None. Plain markdown. Reading level: an engineer, or an agent following instructions.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
