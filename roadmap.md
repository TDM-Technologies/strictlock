# StrictLock roadmap

StrictLock ships as an umbrella that grows. The shipped suite spans four families, the same
four the README [Modules table](README.md#modules) uses:

- **Gates** — [`plan-gate`](plan-gate/), [`commit-msg-gate`](commit-msg-gate/),
  [`generated-sink-commit-gate`](generated-sink-commit-gate/),
  [`generated-sink-prepush-gate`](generated-sink-prepush-gate/),
  [`test-protection-guard`](test-protection-guard/) and
  [`eslint-plugin-strictlock`](eslint-plugin-strictlock/).
- **Hygiene** — [`memory-cap`](memory-cap/) and [`externalized-memory`](externalized-memory/)
  (including its projection bundle).
- **Concurrency** — [`scope-lease`](scope-lease/), [`sink-resolver`](sink-resolver/) and
  [`liveness-scan`](liveness-scan/).
- **Rituals** — [`rituals`](rituals/), written checklists for the steps no hook can check.

There is also a suite-wide [compliance mapping](COMPLIANCE.md). The README table is the
authoritative list of what has shipped; below are the modules and docs still on deck.

These are honest slots, not promises with dates. Each is marked:

- **Planned** — designed and running privately; the work is genericizing + publishing it.
- **Exploring** — useful in a specific setup; not yet general enough to ship as a clean
  standalone. May land, may stay a note.

---

## Shipped since launch

- **generated-sink integrity gates** — a commit-time freshness check plus a pre-push backstop,
  byte-exact against a regenerate, so a stale generated file (manifest, index, schema,
  README) is refused at commit and push time. When a check cannot run, it refuses; each gate has
  its own bypass variable.
- **eslint-plugin-strictlock** (`no-smoke-only-assertions`) — flags false-safety-net tests
  (`toBeDefined`, a bare `expect(x)`, `toContain`-only). The suite's first JS sibling, shipped
  as a standalone npm package.
- **externalized-memory projection bundle** — a deterministic, **git-free and clock-free**
  rendering layer (record schema + region renderer + fenced-region splicing + canonical-UTC
  guard + required-body validation) that turns the shared blackboard into an auditable,
  reproducible status projection.
- **scope-lease** — a git-native, zero-service exclusive lock over a path set. Agents that
  call `acquire` cannot hold the same file at once, and with `PLAN_GATE_SCOPE_LEASE` set plan-gate
  denies a gated agent's write to a file another agent holds (with `PLAN_GATE_REQUIRE_LEASE=on`,
  every gated write must hold a lease). Reads its path set from a plan-gate plan. The
  Concurrency flagship.
- **sink-resolver** — deterministic auto-resolution of generated-file merge conflicts:
  `merge=binary` on the sink + a `resolve` that regenerates from the merged sources, byte-checks,
  and escalates any non-sink conflict + a `check` CI backstop, with a coverage guard that refuses
  to finalize a sink the generator didn't actually rewrite. Safe **because the generator is pure**.
- **liveness-scan** — a read-only reporter that triages a fleet of agent worktrees
  (running / stalled / done-unmerged / ambiguous / idle) from a heartbeat-or-commit-mtime signal.
  Report-only, never reaps, exits 0 always. Completes the Concurrency family.
- **test-protection-guard** — an advisory co-commit-coupling detector that flags the
  "rewrite the failing test to match the buggy output" move (an existing assertion changed
  alongside source, no `TEST-CORRECTNESS:` justification). Always allows, never blocks; the
  teeth are the close-out log review. Dual-mode (Claude Code `PreToolUse` *or* git `pre-commit`).
  It looks for what a weak test rule (such as `eslint-plugin-strictlock`) cannot, which is
  an assertion that was changed to make a failing test pass.
- **rituals** ([`rituals/`](rituals/)) — prose, not a gate: the session **kickoff** and
  **close-out** checklists (key steps carry a "why this step exists" note, plus a distilled
  lessons section), the **unit-of-work interaction analysis** (prior-unit deferred-marker grep +
  peer-unit collision pass, with the note that plan-gate's disjoint check now structurally backs
  the shared-file case), and the **pre-work currency check** (three questions, three verdicts).
  Wired to the gates that can back each step: plan-gate, commit-msg-gate, externalized-memory,
  scope-lease.
- **compliance mapping** ([`COMPLIANCE.md`](COMPLIANCE.md)) — SOC 2 / ISO 42001 / ISO 9001; the
  paper's §6 expanded suite-wide, mapping each gate mechanism to the control it evidences.

---

## Planned

### Governance-of-governance (the marquee)
Rule-archaeology / "Cleanup Day" — the reverse audit that asks whether your own agent-control
rules still earn their keep (the inversion: zero-fire telemetry means a guard is *working*, not
dead; plus a trip-test that proves a guard still fires on its original failure) — paired with an
ROI / carrying-cost gate, as one governance narrative. Plus a blast-radius review-depth trigger
that replaces reviewer judgment-drift with objective scoring. The highest-cred work; sequenced
last by choice.

---

## Exploring

### autohygiene
Automated working-tree hygiene checks for setups running **multiple concurrent worktrees**
(stale branches, orphaned worktrees, drifted local state). Conditional: only earns its
keep when you actually run parallel agent sessions across worktrees.

---

*Have a use case or a module you'd want to see? Open an issue — the roadmap is shaped by
what people actually point their agents at.*
