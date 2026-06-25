# liveness-scan

**A read-only liveness reporter for a fleet of agent worktrees — which sessions are working,
which went quiet, which finished but never merged, and which are ambiguously stalled.**

A [StrictLock](../README.md) Concurrency / Meta-process module. When you run N autonomous agents
in N git worktrees, one supervision job no single agent can do for itself is *triage the fleet*.
`liveness-scan` makes that triage recurring and cheap — with **zero write-path exposure**.

It completes the concurrency family: [`plan-gate`](../plan-gate/) enumerates a unit of work's
paths, [`scope-lease`](../scope-lease/) makes that claim exclusive, and `liveness-scan` watches
the fleet of claimants and surfaces the ones that need a human's judgment.

## It reports; it never reaps

This is a **reporter**, not a gate and not a reaper. It never edits a tracked file, never blocks
a tool call, registers no hook, and **exits 0 always** — a pure reporter must never break a
scheduled run or a session kickoff.

The load-bearing invariant: an ambiguous stall **escalates** (it is written to the report) — the
scanner **never auto-reaps a worktree**. Killing a "dead" session that was merely slow is
unrecoverable data loss; the scanner refuses that call and hands it to a person, with a
copy-pasteable, **non-destructive** inspect command (never `reset --hard`). It can *under*-claim
liveness (a session it can't see classifies idle) but it never *over*-claims death. That
asymmetry is deliberate and safe for a report-only tool.

## What it classifies

Each git worktree gets exactly one status:

| Status | Meaning | Escalates? |
|---|---|---|
| **running** | an owning active plan + a fresh activity signal | no |
| **stalled** | an owning active plan, quiet past the heartbeat/commit window — likely slow; watch | no |
| **ambiguous** | an owning active plan idle past the whole-WP ceiling, *or* no activity signal at all — crashed-or-slow unclear | **yes** |
| **done-unmerged** | work complete / branch ahead of base, not yet merged — pending merge | no |
| **idle** | no plan claims it; branch clean or merged | no |
| **clean** | the `main` / detached worktree — not a session worktree | no |

## Where the signal comes from (two-tier, degrades gracefully)

- A per-worktree **heartbeat file** (`LIVENESS_SCAN_HEARTBEAT_RELPATH`), read by **existence +
  mtime only** — the sharp signal, if your harness touches one (e.g. a one-line `touch` alongside
  a [`scope-lease`](../scope-lease/) `acquire` at session start, then periodically). This is the
  natural composition seam with the rest of the concurrency family.
- Otherwise **commit mtime** — coarser, and flagged in the reason so you know the signal is weaker.
  Always available, no setup.

`HEARTBEAT_TIMEOUT_MIN` (the lease-fresh window) is distinct from `CEILING_MIN` (the whole-unit-
of-work ceiling, beyond which even fresh activity is suspect and escalates).

## Who owns a worktree (attribution)

If you run [`plan-gate`](../plan-gate/), the scanner maps a worktree to the session running in it
via the active/executed plan whose **absolute** `allowed_paths` resolve under that worktree root —
the same enumeration plan-gate and scope-lease already read. Plans are optional: without them,
attribution falls back to "branch ahead of base ⇒ done-unmerged" / "idle".

One subtlety it gets right: a `worktree_bypass: true` plan has authority across *many* worktrees
but is the *session* of none, so it is **excluded** from per-worktree attribution — otherwise it
would mis-mark every worktree as its session.

## Quickstart

```bash
# Optional: point it at your plan-gate plans (reuses PLAN_GATE_PLANS_DIR if already set) and a
# heartbeat file your harness touches. All optional — it works with zero config.
export LIVENESS_SCAN_PLANS_DIR="$HOME/.agent/plans"
export LIVENESS_SCAN_HEARTBEAT_RELPATH=".agent/heartbeat"
export LIVENESS_SCAN_BASE="origin/main"
export LIVENESS_SCAN_LOG_DIR="$HOME/.agent/logs"   # gitignored exhaust; unset = print only

python3 liveness-scan.py            # scan + print the report (+ write the gitignored logs)
python3 liveness-scan.py --dry-run  # print, write nothing
python3 liveness-scan.py --json     # structured digest for tooling
```

Run it from a scheduler (cron / a CI cron / your harness's idle tick) to get a recurring fleet
digest. Because it exits 0 always and only ever writes gitignored logs, it cannot wedge a
schedule or pollute a tree.

## What it surfaces vs. decides vs. can't address

- **Surfaces**: the fleet's state at a glance, and specifically the **ambiguous stalls** that need
  a human — each with a non-destructive inspect command.
- **Decides nothing destructive**: it never reaps, merges, or commits. Every recovery is a
  human's call.
- **Can't address**: a running session it can't attribute (a `worktree_bypass` plan with no path
  under its own worktree) is invisible and classifies idle/done-unmerged — a safe **false
  negative** (under-claims liveness, never over-claims death). And the commit-mtime fallback is
  coarse: a long-thinking agent that hasn't committed recently can read as `stalled`. Wire a
  heartbeat for precision.

## Files

| File | Purpose |
|---|---|
| [`liveness-scan.py`](liveness-scan.py) | The reporter. One file, standard library only. |
| [`CONFIG.md`](CONFIG.md) | Every `LIVENESS_SCAN_*` environment variable. |
| [`SCHEMA.md`](SCHEMA.md) | The classification model, the signals, and the attribution rule. |
| [`examples/`](examples/) | A heartbeat-touch snippet and a sample plan. |
| [`tests/`](tests/) | A standalone suite (stdlib `unittest`): a fake probe drives the classifier; real git proves the probe. |

## Requirements

Python 3.8+ (standard library only — no pip install) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
