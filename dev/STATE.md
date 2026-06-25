# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-06-25
- agent: Claude Code (Opus 4.8)
- git_ref: db4763a (main) — v1 + harvest plan + Wave 0 (3 modules) merged; this session adds the dev-tracking update on top and pushes to origin
- touched: dev/STATE.md, dev/BACKLOG.md; Wave 0 merged COMPLIANCE.md, plan-gate/AUTHORING.md, memory-cap/{memory-cap.py,tests/test_memory_cap.py,README.md}

## Next Action
Start **Wave 1** of the harvest ([HARVEST-PLAN.md](HARVEST-PLAN.md) is the authoritative wave map)
— the workhorse modules (clean extraction, broad adoption):

1. **smoke-only assertion ESLint rule** — flags false-safety-net tests (`toBeDefined`, bare
   `expect(x)`, `toContain`); new `gates` member, zero coupling.
2. **generated-sink integrity gates** — manifest-freshness + pre-push, renamed
   `generated-sink-commit-gate` / `-prepush-gate`.
3. **externalized-memory projection bundle** — record schema + git-free/clock-free render +
   fenced-region splicing + UTC-timestamp guard + required-body validation, as an add-on to the
   shipped module.

Run via the build → adversarial-verify → consistency workflow harness proven in Wave 0. Per the
2026-06-25 verify-gate decision, Wave 1 **auto-merges the mechanical modules** on a clean verify;
marquee items stay on the maintainer's explicit merge. FIRST apply the harness hardening (see
Decisions / BACKLOG `harvest-harness`): verify/consistency agents must not mutate the shared main
checkout.

## Active Threads
Forward map: [HARVEST-PLAN.md](HARVEST-PLAN.md) (waves 0–4 + later). Public module roadmap:
[../roadmap.md](../roadmap.md). Finer-grained entries: [BACKLOG.md](BACKLOG.md). In brief:

- harvest-wave-0 — **shipped 2026-06-25** — memory-cap self-wedge fix · plan-gate/AUTHORING.md ·
  suite-wide COMPLIANCE.md (merged to main; the pilot that proved the verify-gate harness).
- harvest-wave-1 — ready — workhorse modules (the current Next Action): smoke-only ESLint rule ·
  generated-sink gates · externalized-memory projection bundle.
- harvest-wave-2 — planned — marquee IP: Cleanup-Day / rule-archaeology (+ design paper) ·
  ROI / harvest governance narrative · blast-radius (needs pattern-config genericization).
- scope-lease (wave 3) — **decided: Option A** — decoupled primitive; plan-gate plan as the
  default adapter, standalone fallback for non-plan-gate fleets. Source = vault
  `work-registry.py` Feature C. Spec in HARVEST-PLAN §6.
- sink-resolver (wave 3) — planned — behind its pure-generator precondition; pairs with the
  Wave-1 projection bundle.
- paper-§6-reconcile — ready — `paper.md` §6 overclaims the decision log ("allow and deny",
  "tamper-evident"); the shipped gate logs denials by default. Reword §6, or build the opt-in
  allow/deny log. Surfaced by the Wave 0 verify-gate. See BACKLOG.
- ci-os-matrix — ready — add windows-latest + macos-latest to CI so OS-agnostic is proven, not
  just reviewed.
- harvest-harness — ready — workflow verify/consistency agents must never mutate the shared main
  checkout (do before Wave 1 auto-merge). See BACKLOG.
- plans-into-repo — pending — import the historical plan files that drove the gate as worked
  examples (off-machine; see import checklist).
- false-positive-analysis — pending — quantify read-only compound denies from real deny-log
  data, then decide whether to broaden compound handling (off-machine).
- reversibility-tiers / auto-commit — exploring (later) — need a shared 2–3 month evidence window.

## Decisions
- 2026-06-25 — **Wave 0 shipped**: three modules merged to main — the memory-cap changed-region
  fix (removes the self-wedge; CRLF/LF-agnostic; +4 tests), `plan-gate/AUTHORING.md`, and a
  suite-wide `COMPLIANCE.md`. Built + verified by a fan-out workflow (one agent per item in its
  own worktree → independent adversarial verify → cross-item consistency).
- 2026-06-25 — the verify-gate **pilot proved out** (it caught a real `paper.md` §6 logging
  overclaim and a backlog-vs-code lifecycle discrepancy — it did not rubber-stamp) → **Wave 1
  auto-merges the mechanical modules** on a clean verify; marquee items (scope-lease, the design
  paper) stay on the maintainer's explicit merge.
- 2026-06-25 — **harness hardening (do before Wave 1 auto-merge)**: a verify/consistency agent
  left a stray uncommitted `git merge` in the shared main checkout (zero loss; aborted). Workflow
  agents must never mutate the shared main checkout — mergeability checks use `git merge-tree`
  (dry-run) or always `--abort`. Under auto-merge a stray half-merge in main is a silent hazard.
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
