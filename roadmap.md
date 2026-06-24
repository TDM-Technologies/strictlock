# StrictLock roadmap

StrictLock ships as an umbrella that grows. Four standalone modules are shipped today —
[`plan-gate`](plan-gate/), [`commit-msg-gate`](commit-msg-gate/),
[`memory-cap`](memory-cap/), and [`externalized-memory`](externalized-memory/). Below are
the sibling modules and docs on deck.

These are honest slots, not promises with dates. Each is marked:

- **Planned** — designed and running privately; the work is genericizing + publishing it.
- **Exploring** — useful in a specific setup; not yet general enough to ship as a clean
  standalone. May land, may stay a note.

---

## Planned

### Session-ritual checklists (kickoff / close-out)
Genericized versions of the start-of-work and end-of-work checklists that make the
PLAN → CONFIRM → EXECUTE loop a habit rather than a hope: verify the gate environment, read
the prior handoff, author the plan, and at close-out confirm the work landed and archive the
plan. Ships as templates + prose, not as anyone's real checklists.

### Compliance-mapping doc (SOC 2 / ISO 42001 / ISO 9001)
A standalone expansion of the paper's §6: how the gate's append-only decision log and
exact-path authorization become audit evidence — which control each mechanism maps to, and
what an auditor can trace. The point the paper makes in one page, as a usable reference.

### Smoke-only assertion detector (ESLint rule)
An ESLint rule that flags false-safety-net tests — `toBeDefined`, a bare `expect(x)` with no
matcher, `toContain` as the only assertion — the green checks that assert nothing real. Zero
coupling; drops into any JS/TS project.

### Generated-sink integrity gates
A layered, fail-closed guard so a **stale generated artifact** (a manifest, an index, a build
output, a checked-in schema) never ships: a commit-time freshness check plus a pre-push
backstop, byte-exact against a regenerate. For any repo carrying generated state that can
silently fall out of sync.

### externalized-memory projection bundle
An add-on to the shipped `externalized-memory` module: a deterministic, **git-free and
clock-free** rendering layer (record schema + region renderer + fenced-region splicing +
canonical-UTC timestamp guard + required-body validation) that turns the shared blackboard into
a blackboard with an auditable, reproducible status projection.

---

## Exploring

### autohygiene
Automated working-tree hygiene checks for setups running **multiple concurrent worktrees**
(stale branches, orphaned worktrees, drifted local state). Conditional: only earns its
keep when you actually run parallel agent sessions across worktrees.

---

*Have a use case or a module you'd want to see? Open an issue — the roadmap is shaped by
what people actually point their agents at.*
