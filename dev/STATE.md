# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-07-05 (blackboard refresh — brings Meta/Next-Action current with PR #7/#8, which shipped without updating this file)
- agent: Claude Code (Fable 5)
- git_ref: d17e1a9 (**PUSHED + MERGED to origin/main via PR #6, all CI green**) — **Wave 3 COMPLETE & LIVE on
  public main**: `sink-resolver` (binary-sink auto-resolver; 29 tests; 3 adversarial lenses → 1 blocking + 1
  structural gap fixed) AND `liveness-scan` (read-only fleet reporter; 31 tests; 2 lenses clean, 4
  safe-direction polish items folded in) landed on top of the `scope-lease` flagship (a6054e8). Full suite
  green: **185 tests / 9 Python modules**. README + COMPLIANCE (§K/§L) + CI + roadmap registered, then
  **PUSHED + MERGED to origin/main via PR #6 (2026-06-25, CI green: 9 jobs incl python 3.8–3.12 + node + shell
  + secret-scan). origin/main == local main; the concurrency family is LIVE on the public repo.**
- touched (this wave): sink-resolver/*, liveness-scan/*, README.md, COMPLIANCE.md (§K/§L),
  .github/workflows/ci.yml, roadmap.md, dev/STATE.md, dev/BACKLOG.md

## Next Action
**❄ PAUSED 2026-07-05 (Tim's portfolio ruling):** StrictLock is **link-only evidence** during the 13-week
HIPAAPath distribution push (to Oct 1). No new waves until Tim unpauses. State below is current and ready.

1. **Wave 3 — DONE + LIVE** (PR #6, `d17e1a9`, 2026-06-25): scope-lease + sink-resolver + liveness-scan.
2. **Wave 4 — 1 of 4 SHIPPED:** the **test-protection co-commit guard is DONE + MERGED** (PR #7, 43 tests,
   2026-06-30; this file previously still called it "next build" — stale, corrected 2026-07-05). The
   **harvest source map** also landed (PR #8, `dev/HARVEST-SOURCE-MAP.md`).
3. **Wave 4 remainder (when unpaused) — the 3 prose items:** session-ritual checklists · WP-interaction
   analysis · pre-WP currency check. **Faithful ports, not reconstruction:** sources are STAGED at
   `/Users/tim/StrictLock-harvest-import/skills/` (all four `*-hipaapath` skills + ~58KB learnings, imported
   2026-06-26 — see the corrected HARVEST-SOURCE-MAP §OFF-MACHINE/STAGED). Then Wave 2 (marquee IP, last).
4. **(Done)** `eslint-plugin-strictlock@0.1.0` PUBLISHED to npm (2026-06-25, acct `downsmullen`).

Run any build via the proven build → adversarial-verify → consistency workflow harness. **NOTE:** the
`harvest-merge-guard.sh` referenced earlier was an ephemeral helper, not committed — this session applied the
guard DISCIPLINE inline (clean-main pre-flight → `merge --no-ff` → full-suite validation → rollback-on-fail →
**never push**). **Agents never merge** (the workflow agents this session were read-only verifiers). Optional
backlog item: commit a reusable `harvest-merge-guard.sh` so the discipline is a script, not a convention.

## Active Threads
Forward map: [HARVEST-PLAN.md](HARVEST-PLAN.md) (waves 0–4 + later). Public module roadmap:
[../roadmap.md](../roadmap.md). Finer-grained entries: [BACKLOG.md](BACKLOG.md). **Before any
HIPAAPath cleanup, check [HARVEST-SOURCE-MAP.md](HARVEST-SOURCE-MAP.md)** — what's left in
HIPAAPath, exact paths, and whether each item is already safe. In brief:

- harvest-wave-0 — **shipped 2026-06-25** — memory-cap self-wedge fix · plan-gate/AUTHORING.md ·
  suite-wide COMPLIANCE.md (the pilot that proved the verify-gate harness).
- harvest-wave-1 — **COMPLETE 2026-06-25 (3 of 3 modules)** — generated-sink commit-gate + prepush-gate (new
  Gates-family members, harvested from HIPAAPath manifest-freshness) + the externalized-memory projection
  bundle (deterministic git-free/clock-free render + fenced-region splicing + UTC guard + required-body
  validation) + eslint-plugin-strictlock (the smoke-only-assertions rule as a standalone npm package). On
  main @ e436a72, pushed, registered in README/COMPLIANCE, CI wired (incl. a scoped node job). All came back
  verify-clean (zero blocking issues); landed by the guard.
- eslint-plugin-publish — **pending (Tim's release call)** — eslint-plugin-strictlock@0.1.0 is built +
  CI-tested in-repo but not yet on npm; `npm publish` needs an account/token and is outward-facing.
- harvest-harness — **DONE 2026-06-25** — the fail-closed merge guard is built AND now proven in use: it
  landed both Wave-1 modules (clean-main pre-flight, full-suite validation after each merge, rollback-on-fail,
  no push). No workflow agent ever touched the shared main checkout — builds happen only in isolated worktrees.
- scope-lease (wave 3) — **SHIPPED 2026-06-25** (main @ a6054e8) — Option A git-ref CAS exclusive path-lock;
  plan-gate adapter default + --paths/stdin/own-frontmatter fallback; faithful port of vault Feature C, all
  binding invariants kept (agents-only/never-walls-a-human, fail-loud machine boundary, surfaced reclaims,
  FS-aware case/unicode fold). 37 tests (incl. a commit-race abort regression added at integration); the 3
  perspective-diverse adversarial verifies (CAS-atomicity · normalization · fencing/human-bypass) all clean.
- sink-resolver (wave 3) — **SHIPPED + MERGED to origin/main (PR #6, CI green) 2026-06-25** — binary-sink
  auto-resolver: `merge=binary` + `resolve` (regenerate from merged sources → byte-oracle → escalate any
  non-sink → finalize) + `check` (web-UI/CI backstop). Vault Feature B git-native shape as default;
  `conductor-resolve.py` documented as the pre-merge web-UI variant. 29 tests (real `merge=binary` conflicts,
  git 2.54). 3 adversarial lenses: escalation-boundary CLEAN; found **1 proven blocking** (`check` weak path
  used `git checkout --` → destroyed uncommitted sink edits + false STALE verdict on a dirty tree → fixed:
  snapshot/regenerate/compare/restore-exact-bytes) + **1 structural gap** (a configured sink the generator
  doesn't cover got 'ours' staged → fixed with a **coverage guard**: a conflicted sink left byte-unchanged
  after regenerate escalates). Both fixed + regression-tested.
- liveness-scan (wave 3) — **SHIPPED + MERGED to origin/main (PR #6, CI green) 2026-06-25** — read-only fleet
  reporter (running/stalled/done-unmerged/ambiguous/idle/clean) ported from HIPAAPath `conductor-scan.py`,
  decoupled from machine-specific defaults; report-only, never reaps, exits 0 always. 31 tests (fake GitProbe
  drives the classifier; real git proves the probe). 2 adversarial lenses **CLEAN, zero blocking** (the
  never-over-claim-death invariant + exit-0-always held under every hostile input; decoupling complete). 4
  safe-direction polish items folded in: `_canon` resolves symlinks + drops case-fold in attribution; idle
  reason distinguishes unknown-base from merged; LOG_DIR-file collision prints friendly (still exit 0);
  doc'd the main/master/detached blind spot. **Wave 3 (concurrency family) is now COMPLETE.**
- harvest-wave-2 (marquee) — **DEFERRED to end of queue (Tim, 2026-06-25)** — Cleanup-Day / rule-archaeology
  (+ design paper) · ROI / harvest governance narrative · blast-radius (needs pattern-config genericization).
  Still the highest-cred work; just sequenced last by choice.
- paper-§6-reconcile — ready — `paper.md` §6 overclaims the decision log ("allow and deny",
  "tamper-evident"); the shipped gate logs denials by default. Reword §6, or build the opt-in allow/deny log.
- ci-os-matrix — ready — add windows-latest + macos-latest to CI so OS-agnostic is proven, not just reviewed.
- plans-into-repo — pending — import the historical plan files as worked examples (off-machine).
- false-positive-analysis — pending — quantify read-only compound denies from real deny-log data (off-machine).
- reversibility-tiers / auto-commit — exploring (later) — need a shared 2–3 month evidence window.

## Decisions
- 2026-06-25 — **Wave 3 COMPLETE — `sink-resolver` + `liveness-scan` shipped to LOCAL main** (`189285c`,
  **not pushed**). Built solo (drafting/file-ops per the charter), each adversarially verified via a
  perspective-diverse workflow fan-out (3 lenses for the correctness-critical sink-resolver where silent
  clobber = data loss; 2 for the report-only liveness-scan), fixes applied, then guard-landed (clean-main
  pre-flight → `merge --no-ff` → full-suite green → no push). The verify earned its keep: it caught a **proven
  data-loss defect** in sink-resolver's `check` and a **silent-clobber coverage gap**, both fixed before
  landing. Coverage guard added as a genuine upgrade to the core safety claim (the tool now PROVES every
  conflicted sink was regenerated, vs trusting operator CHECK_CMD coverage). liveness-scan came back
  zero-blocking. Registered in README + COMPLIANCE §K/§L + CI + roadmap, then **PUSHED + MERGED to origin/main
  via PR #6 (Tim's go-ahead 2026-06-25; CI green, 9 jobs).** Remaining harvest: Wave 4 (rituals/gates), then
  Wave 2 (marquee, last).
- 2026-06-25 — **Wave 3 `scope-lease` flagship SHIPPED** (queue reordered first: marquee deferred to end,
  concurrency next). Faithful port of vault Feature C's git-ref CAS lease, retargeted per HARVEST-PLAN §6
  Option A (path-source seam: plan-gate adapter default + standalone fallbacks; env owner/lock-id; standalone
  CLI; dropped the ULID/work-record glue). Verified by **build + 3 perspective-diverse adversaries**
  (CAS-atomicity, the normalization crux, fencing/reclaim+human-bypass) — all clean, zero blocking. Integrator
  added a commit-race abort **regression test** (the one advisory gap: the Phase-2 DECIDE→commit race was
  confirmed-by-hand but untested) → 37 tests. Landed by the guard (a6054e8); registered in README + COMPLIANCE
  §J + CI; roadmap.md reconciled.
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
- 2026-06-25 — **smoke-only ESLint rule shipped as `eslint-plugin-strictlock`** (Tim's call: own npm package,
  cross-linked as a Gates-family sibling — keeps the rest of the repo Python+shell, zero-dep). Ported faithfully
  from HIPAAPath `no-smoke-only-assertions`; the HIPAAPath baseline.json was genericized into ESLint rule
  options (baseline / marker / extraSmokeMatchers, default strict). 18/18 RuleTester cases; flat-config-first
  plugin export + legacy shim; Apache-2.0. Landed by the guard (validated `npm ci && npm test`), with a scoped
  `node` CI job so only this package carries the JS toolchain. **Not yet published to npm** — that's a separate
  release call (see `eslint-plugin-publish`).
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
