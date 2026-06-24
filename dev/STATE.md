# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-06-24
- agent: Claude Code (Opus 4.8)
- git_ref: c8b875e (main, v1 + externalized-memory + harvest plan)
- touched: dev/HARVEST-PLAN.md, dev/STATE.md, dev/BACKLOG.md, roadmap.md

## Next Action
Start **Wave 0** of the harvest ([HARVEST-PLAN.md](HARVEST-PLAN.md) is the authoritative wave
map) — the unblocked, lowest-effort work, sources all present in this environment:

1. **memory-cap changed-region fix** — port the delta-scan that removes the self-wedge (fixes a
   defect in a shipped module).
2. **plan-authoring guide** — genericize the discipline standard into `plan-gate/AUTHORING.md`.
3. **compliance-mapping doc** — expand `plan-gate/paper.md` §6 into a standalone `COMPLIANCE.md`.

(The historical-plan import — plans-into-repo + the read-only compound false-positive analysis —
stays **pending**, not dropped: it is blocked on off-machine artifacts, see Active Threads /
Constraints. It moved off "next" only because Wave 0 is unblocked and nearer-ready.)

## Active Threads
Forward map: [HARVEST-PLAN.md](HARVEST-PLAN.md) (waves 0–4 + later). Public module roadmap:
[../roadmap.md](../roadmap.md). Finer-grained entries: [BACKLOG.md](BACKLOG.md). In brief:

- harvest-wave-0 — ready — memory-cap fix · plan-authoring guide · compliance-mapping (the
  current Next Action; sources all present).
- harvest-wave-1 — ready — workhorse modules: smoke-only ESLint rule · generated-sink gates ·
  externalized-memory projection bundle.
- harvest-wave-2 — planned — marquee IP: Cleanup-Day / rule-archaeology (+ design paper) ·
  ROI / harvest governance narrative · blast-radius (needs pattern-config genericization).
- scope-lease (wave 3) — **decided: Option A** — decoupled primitive; plan-gate plan as the
  default adapter, standalone fallback for non-plan-gate fleets. Source = vault
  `work-registry.py` Feature C. Spec in HARVEST-PLAN §6.
- sink-resolver (wave 3) — planned — behind its pure-generator precondition; pairs with the
  Wave-1 projection bundle.
- plans-into-repo — pending — import the historical plan files that drove the gate as worked
  examples (off-machine; see import checklist).
- false-positive-analysis — pending — quantify read-only compound denies from real deny-log
  data, then decide whether to broaden compound handling (off-machine).
- reversibility-tiers / auto-commit — exploring (later) — need a shared 2–3 month evidence window.

## Decisions
- 2026-06-24 — adopted the **harvest plan** ([HARVEST-PLAN.md](HARVEST-PLAN.md)): sequence the HIPAAPath/vault "old-process" machinery into StrictLock as env-configured modules across waves 0–4. Sources verified present in this environment (the vault + HIPAAPath are reachable; only the plan-files + deny-log imports remain off-machine).
- 2026-06-24 — `scope-lease` input source = **Option A** (decoupled primitive + plan-gate plan as default adapter, standalone fallback otherwise) — preserves the suite's "every module standalone" promise while keeping the flagship-extension story.
- 2026-06-22 — track StrictLock's own development with its `externalized-memory` module — dogfood the pattern the product ships.
- 2026-06-22 — keep StrictLock's dev-tracking **public** (tracked in-repo, not gitignored); `dev/` files stay public-safe (no machine paths, usernames, or unrelated project internals), and the private import checklist stays off the public tree.
- 2026-06-01 — v1 shipped: plan-gate, commit-msg-gate, memory-cap, externalized-memory — four standalone, env-configured modules.

## Constraints
- Some historical operational artifacts (the plan files; the runtime deny log) exist
  only on the original development workstation, not in this repo's environment. Work
  that depends on them is blocked until imported — it is not reconstructable here.
- Runtime gate logs are exhaust and are git-ignored (`*.log`); never commit them.
