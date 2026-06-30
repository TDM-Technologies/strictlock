# StrictLock roadmap

StrictLock ships as an umbrella that grows. The shipped suite now spans three families —
**Gates** ([`plan-gate`](plan-gate/), [`commit-msg-gate`](commit-msg-gate/),
[`generated-sink-commit-gate`](generated-sink-commit-gate/) /
[`generated-sink-prepush-gate`](generated-sink-prepush-gate/),
[`eslint-plugin-strictlock`](eslint-plugin-strictlock/)), **Hygiene**
([`memory-cap`](memory-cap/), [`externalized-memory`](externalized-memory/) and its projection
bundle), and **Concurrency** ([`scope-lease`](scope-lease/), [`sink-resolver`](sink-resolver/),
[`liveness-scan`](liveness-scan/)) — plus a suite-wide [compliance mapping](COMPLIANCE.md). The README [Modules table](README.md#modules) is the
authoritative shipped list; below are the siblings and docs still on deck.

These are honest slots, not promises with dates. Each is marked:

- **Planned** — designed and running privately; the work is genericizing + publishing it.
- **Exploring** — useful in a specific setup; not yet general enough to ship as a clean
  standalone. May land, may stay a note.

---

## Shipped since launch

- **generated-sink integrity gates** — a commit-time freshness check plus a pre-push backstop,
  byte-exact against a regenerate, so a stale generated artifact (manifest, index, schema,
  README) never ships. Fail-closed; bypasses isolated per gate.
- **eslint-plugin-strictlock** (`no-smoke-only-assertions`) — flags false-safety-net tests
  (`toBeDefined`, a bare `expect(x)`, `toContain`-only). The suite's first JS sibling, shipped
  as a standalone npm package.
- **externalized-memory projection bundle** — a deterministic, **git-free and clock-free**
  rendering layer (record schema + region renderer + fenced-region splicing + canonical-UTC
  guard + required-body validation) that turns the shared blackboard into an auditable,
  reproducible status projection.
- **scope-lease** — a git-native, zero-service exclusive lock over a path set so N autonomous
  agents never edit the same file at once. The multi-agent extension of plan-gate: enumerated
  paths → enumerated **and exclusive**. The Concurrency flagship.
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
  The detective complement to the born-weak `eslint-plugin-strictlock`.
- **compliance mapping** ([`COMPLIANCE.md`](COMPLIANCE.md)) — SOC 2 / ISO 42001 / ISO 9001; the
  paper's §6 expanded suite-wide, mapping each gate mechanism to the control it evidences.

---

## Planned

### Session-ritual checklists (kickoff / close-out)
Genericized versions of the start-of-work and end-of-work checklists that make the
PLAN → CONFIRM → EXECUTE loop a habit rather than a hope: verify the gate environment, read
the prior handoff, author the plan, and at close-out confirm the work landed and archive the
plan. Ships as templates + prose, not as anyone's real checklists.

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
