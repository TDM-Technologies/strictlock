# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-06-25
- agent: Claude Code (Opus 4.8)
- git_ref: 4e30534 (main, pushed to origin) — Wave 1 landed: generated-sink-commit-gate +
  generated-sink-prepush-gate + the externalized-memory projection bundle, registered in
  README/COMPLIANCE and wired into CI
- touched: generated-sink-commit-gate/*, generated-sink-prepush-gate/*,
  externalized-memory/{projection.py,PROJECTION-SCHEMA.md,tests/test_projection.py,examples/*,README.md},
  README.md, COMPLIANCE.md, .github/workflows/ci.yml, dev/STATE.md, dev/BACKLOG.md

## Next Action
Wave 1 is **landed except the ESLint rule**. Two things tee up the next build:

1. **(Holds Wave 1's 3rd module — Tim's call)** the **smoke-only assertion ESLint rule** is deferred on a
   toolchain decision: an ESLint rule drags a JS toolchain into this Python+shell, zero-dep repo. Decide
   whether it ships as its **own npm package** (the current lean) or folds in. Until then it is NOT built.
   See [BACKLOG.md](BACKLOG.md) `eslint-smoke-rule`.
2. **(Direction — Tim) marquee vs workhorse for the next wave.** With the salvage deadline gone, the open
   choice is **Wave 2 marquee IP** (rule-archaeology/Cleanup-Day + a design paper · ROI/governance narrative ·
   blast-radius after pattern-config genericization) vs the **Wave 3 scope-lease flagship** (Option A, sourced
   from vault Feature C) + sink-resolver. Per the positioning strategy the consulting cred lives in the marquee.

Run any build via the proven build → adversarial-verify → consistency workflow harness; the fail-closed merge
guard (`harvest-merge-guard.sh`) lands clean branches — **agents never merge.**

## Active Threads
Forward map: [HARVEST-PLAN.md](HARVEST-PLAN.md) (waves 0–4 + later). Public module roadmap:
[../roadmap.md](../roadmap.md). Finer-grained entries: [BACKLOG.md](BACKLOG.md). In brief:

- harvest-wave-0 — **shipped 2026-06-25** — memory-cap self-wedge fix · plan-gate/AUTHORING.md ·
  suite-wide COMPLIANCE.md (the pilot that proved the verify-gate harness).
- harvest-wave-1 — **landed 2026-06-25 (2 of 3 modules)** — generated-sink commit-gate + prepush-gate (new
  Gates-family members, harvested from HIPAAPath manifest-freshness) + the externalized-memory projection
  bundle (deterministic git-free/clock-free render + fenced-region splicing + UTC guard + required-body
  validation). On main @ 4e30534, pushed, registered in README/COMPLIANCE, CI wired. Both came back
  verify-clean (zero blocking issues); landed by the guard.
- eslint-smoke-rule — **held (the Wave-1 remainder)** — needs Tim's JS-toolchain call (own npm package vs
  fold into the Python+shell repo). The only undecided Wave-1 piece.
- harvest-harness — **DONE 2026-06-25** — the fail-closed merge guard is built AND now proven in use: it
  landed both Wave-1 modules (clean-main pre-flight, full-suite validation after each merge, rollback-on-fail,
  no push). No workflow agent ever touched the shared main checkout — builds happen only in isolated worktrees.
- harvest-wave-2 — planned — marquee IP: Cleanup-Day / rule-archaeology (+ design paper) · ROI / harvest
  governance narrative · blast-radius (needs pattern-config genericization).
- scope-lease (wave 3) — **decided: Option A** — decoupled primitive; plan-gate plan as the default adapter,
  standalone fallback for non-plan-gate fleets. Source = vault `work-registry.py` Feature C. Spec in
  HARVEST-PLAN §6.
- sink-resolver (wave 3) — planned — behind its pure-generator precondition; now pairs naturally with the
  **shipped** Wave-1 projection bundle (the kind of pure renderer it requires).
- paper-§6-reconcile — ready — `paper.md` §6 overclaims the decision log ("allow and deny",
  "tamper-evident"); the shipped gate logs denials by default. Reword §6, or build the opt-in allow/deny log.
- ci-os-matrix — ready — add windows-latest + macos-latest to CI so OS-agnostic is proven, not just reviewed.
- plans-into-repo — pending — import the historical plan files as worked examples (off-machine).
- false-positive-analysis — pending — quantify read-only compound denies from real deny-log data (off-machine).
- reversibility-tiers / auto-commit — exploring (later) — need a shared 2–3 month evidence window.

## Decisions
- 2026-06-25 — **Wave 1 landed (2 of 3)**: generated-sink commit+prepush gates + the externalized-memory
  projection bundle, built by the fan-out workflow (one build agent per module in its own worktree →
  independent adversarial verify [both verdict-clean, zero blocking issues] → cross-module consistency), then
  merged to main by the fail-closed guard and pushed (`4e30534`). The decided auto-merge of the mechanical
  modules worked end-to-end.
- 2026-06-25 — **integrator conformance calls (mechanical, on the established house convention)**: renamed
  the sink-gate env prefixes to `GENERATED_SINK_{COMMIT,PREPUSH}_GATE_*` (1:1 with the directory name,
  matching all four shipped modules — the build agent had used the shorter `SINK_*`); tightened the
  commit-gate path trigger (dropped a bare `startswith(pref)` → exact-or-under-`<pref>/`, the fail-safe
  precision fix our own verify flagged); registered the new modules in README + COMPLIANCE (new §I) + CI.
  Flagged to Tim for veto on the prefix length.
- 2026-06-25 — **smoke-only ESLint rule HELD**: not built this wave; it forces a JS-toolchain decision that
  is Tim's (own npm package vs fold in). The only undecided Wave-1 piece.
- 2026-06-25 — **harness hardening proven (resolves the prior open item)**: the fail-closed merge guard
  (built earlier) was used to land Wave 1 — agents never merged; the guard pre-flighted a clean main and
  validated the full suite after each merge. The earlier "do harness hardening first / a verify agent left a
  stray merge in main" hazard is now structurally closed.
- 2026-06-25 — the verify-gate **pilot proved out** (Wave 0: it caught a real `paper.md` §6 logging
  overclaim and a backlog-vs-code discrepancy — it did not rubber-stamp) → **Wave 1 auto-merges the
  mechanical modules** on a clean verify; marquee items (scope-lease, the design paper) stay on the
  maintainer's explicit merge.
- 2026-06-24 — adopted the **harvest plan** ([HARVEST-PLAN.md](HARVEST-PLAN.md)): sequence the HIPAAPath/vault
  "old-process" machinery into StrictLock as env-configured modules across waves 0–4. Sources verified
  present in this environment (only the plan-files + deny-log imports remain off-machine).
- 2026-06-24 — `scope-lease` input source = **Option A** (decoupled primitive + plan-gate plan as default
  adapter, standalone fallback otherwise) — preserves the suite's "every module standalone" promise while
  keeping the flagship-extension story.
- 2026-06-22 — track StrictLock's own development with its `externalized-memory` module — dogfood the pattern
  the product ships.
- 2026-06-22 — keep StrictLock's dev-tracking **public** (tracked in-repo, not gitignored); `dev/` files stay
  public-safe (no machine paths, usernames, or unrelated project internals).
- 2026-06-01 — v1 shipped: plan-gate, commit-msg-gate, memory-cap, externalized-memory — four standalone,
  env-configured modules.

## Constraints
- Some historical operational artifacts (the plan files; the runtime deny log) exist only on the original
  development workstation, not in this repo's environment. Work that depends on them is blocked until
  imported — it is not reconstructable here.
- Runtime gate logs are exhaust and are git-ignored (`*.log`); never commit them.
