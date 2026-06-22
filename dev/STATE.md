# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-06-22
- agent: Claude Code (Opus 4.8)
- git_ref: d104580 (main, v1 + externalized-memory)
- touched: dev/README.md, dev/STATE.md, dev/BACKLOG.md

## Next Action
Run the historical-plan import to land **plans-into-repo** and the **read-only
compound false-positive analysis** — both depend on operational artifacts (the
original plan files and the gate's runtime deny log) that live on the original
development workstation, not in this repo's environment. The maintainer holds a
local import checklist for that pass; it is intentionally not in the public tree.

## Active Threads
See [BACKLOG.md](BACKLOG.md) for the full entries; in brief:

- plans-into-repo — pending — import the historical plan files that drove the gate
  as worked examples (off-machine; see import checklist).
- false-positive-analysis — pending — quantify read-only compound denies from real
  deny-log data, then decide whether to broaden compound handling.
- reversibility-tiers — exploring — tier authorization by reversibility (depends on auto-commit).
- auto-commit — exploring — PostToolUse auto-commit for trivially-reversible writes.
- memory-cap-changed-region — ready — port the delta-scan fix that removes the self-wedge.
- plan-gate-authoring-guide — ready — genericize the authoring/discipline standard into the repo.

## Decisions
- 2026-06-22 — track StrictLock's own development with its `externalized-memory` module — dogfood the pattern the product ships.
- 2026-06-22 — keep StrictLock's dev-tracking **public** (tracked in-repo, not gitignored); `dev/` files stay public-safe (no machine paths, usernames, or unrelated project internals), and the private import checklist stays off the public tree.
- 2026-06-01 — v1 shipped: plan-gate, commit-msg-gate, memory-cap, externalized-memory — four standalone, env-configured modules.

## Constraints
- Some historical operational artifacts (the plan files; the runtime deny log) exist
  only on the original development workstation, not in this repo's environment. Work
  that depends on them is blocked until imported — it is not reconstructable here.
- Runtime gate logs are exhaust and are git-ignored (`*.log`); never commit them.
