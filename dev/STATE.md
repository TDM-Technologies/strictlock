# StrictLock — state (the shared blackboard)

## Meta
- last_session: 2026-10-16 (claims-truth PR 1 + PR 2 merged; Wave 4 rituals rebased onto main in PR #14)
- agent: Claude Code
- git_ref: origin/main **0e5746a** (PR #17 merge) — on top of 720a24e (PR #15 merge) and PR #16 (park note).
  Full suite on main: **367 Python tests across 10 test files** (plan-gate 78 · memory-cap 35 ·
  externalized-memory 7 + 38 · generated-sink-commit-gate 13 · generated-sink-prepush-gate 12 · scope-lease 43 ·
  sink-resolver 39 · liveness-scan 46, 1 skipped · test-protection-guard 56) **+ 30 shell checks**
  (commit-msg-gate). **CI: 13 jobs, all green on Windows, macOS and Ubuntu** (9-entry Python matrix + node +
  2 shell + secret-scan) at PR #17's head `91844d8`.
- touched (this session): README.md, roadmap.md, rituals/*, dev/BACKLOG.md, dev/STATE.md,
  dev/HARVEST-SOURCE-MAP.md, dev/HARVEST-PLAN.md, dev/WORK-QUEUE.md

## Next Action
**Pause ended — project thawed 2026-09-29.** The ❄ pause (2026-07-05, link-only evidence during the
HIPAAPath distribution push to Oct 1) is over: StrictLock resumed after the HIPAAPath push. History kept:
during the pause the one exception (2026-07-15) was the authorizations record, which **shipped 2026-07-15 as
PR #11** (one unified, hash-chained `plan-gate-decisions.log`, allow+deny, all surfaces, full-by-default, +
log-dir self-protection; spec [AUTHORIZATIONS-RECORD-SPEC.md](AUTHORIZATIONS-RECORD-SPEC.md)). That closed
the `paper-§6-reconcile` thread.

**Current state (2026-10-16):** the claims-truth pass ([CLAIMS-FIX-SPEC.md](CLAIMS-FIX-SPEC.md), decisions
made 2026-07-07) is **merged**: **PR #15** (docs-only rewords, merge `720a24e`) and **PR #17** (Stage 2 code +
strong wording restored where code and a test back it, merge `0e5746a`). PR #17's first Windows runs found two
real bugs, both fixed before merge: scope-lease sent git its stdin in text mode (CRLF broke every lease
transaction on Windows), and the externalized-memory projection CLI wrote cp1252/CRLF to Windows pipes (now
UTF-8/LF on every OS). What was and was not built is in CLAIMS-FIX-SPEC §8; leftovers are in BACKLOG "Open
after Stage 2". **Wave 4 remainder (the `rituals/` prose ports) merged as PR #14 (`1cd53ca`).** Next harvest: Wave 2
(marquee, last). Open-work inventory and its close-out: [WORK-QUEUE.md](WORK-QUEUE.md). Merge to public main
stays with Tim.

1. **Wave 3 — DONE + LIVE** (PR #6, `d17e1a9`, 2026-06-25): scope-lease + sink-resolver + liveness-scan.
2. **Wave 4 — 4 of 4 built:** the **test-protection co-commit guard** (PR #7, 2026-06-30) plus the three prose
   items — session-ritual checklists (kickoff + close-out), WP-interaction analysis, pre-WP currency check —
   shipped as `rituals/` in **PR #14 (merged, `1cd53ca`)**. Faithful ports of the maintainer's staged `start-` /
   `close-` skills (+ learnings) and two private design docs, genericized; two Opus review lenses
   (public-safety, fidelity) → 10 blocking findings fixed. The **harvest source map** also landed (PR #8).
3. **Next:** Wave 2 (marquee IP, last) — design-heavy; needs Tim's direction before building.
4. **(Done)** `eslint-plugin-strictlock` PUBLISHED to npm — `0.1.0` (2026-06-25) + `0.1.1` (2026-07-05),
   acct `downsmullen`: <https://www.npmjs.com/package/eslint-plugin-strictlock>.
   2026-10-16: PR #19 merged two new rules (`no-tautological-assertions`, `no-unasserted-result`; `warn` in
   `recommended`), version `0.2.0`. PR #20 (open, not merged) adds the CommonJS entry so the legacy
   `.eslintrc` config works on ESLint 8. npm publish of `0.2.0` waits on PR #20 — Tim's step.

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
- eslint-plugin-publish — **DONE — published to npm** — eslint-plugin-strictlock `0.1.0` (2026-06-25) +
  `0.1.1` (2026-07-05) are live on npm (acct `downsmullen`):
  <https://www.npmjs.com/package/eslint-plugin-strictlock>. Resolves the prior "pending (Tim's release call)".
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
- paper-§6-reconcile — **DONE 2026-07-15 (PR #11)** — built, not reworded: the authorizations record
  (allow+deny, hash-chained, verifier, log self-protection) makes the paper's §6 claims code-backed. Still
  open from that family at the time: the memory-cap decision-log mirror and the scope-lease / sink-resolver log
  gap-fills (CLAIMS-FIX-SPEC §1, Q3) — built in PR 2 2026-10-16, apart from the leftovers in BACKLOG.
- claims-truth-pass — **DONE 2026-10-16** — PR #15 (merge `720a24e`) + PR #17 (merge `0e5746a`);
  [CLAIMS-FIX-SPEC.md](CLAIMS-FIX-SPEC.md) §6, ledger in §7, dispositions in §8.
- harvest-wave-4 — **4 of 4 built** — test-protection-guard (PR #7) + `rituals/` (PR #14, merged `1cd53ca`).
- ci-os-matrix — **DONE 2026-10-16** (PR #17) — 13 CI jobs green on Windows, macOS and Ubuntu; the first
  Windows runs found and fixed two real bugs (scope-lease stdin CRLF, projection CLI encoding).
- plans-into-repo — pending — import the historical plan files as worked examples (off-machine).
- false-positive-analysis — pending — quantify read-only compound denies from real deny-log data (off-machine).
- reversibility-tiers / auto-commit — exploring (later) — need a shared 2–3 month evidence window.

## Decisions
- 2026-10-16 — **Claims-truth PR 1 + PR 2 merged by Tim** (#15 → `720a24e`, #17 → `0e5746a`). CI 13/13 green
  across Windows/macOS/Ubuntu before merge. **Wave 4 rituals (PR #14) rebased onto that main** with the Wave 4
  dev-doc close-out; merge stays with Tim.
- 2026-10-16 — **Claims-truth PR 2 (Stage 2) integrated** on `feat/claims-truth-code` (four piece branches
  merged `--no-ff`: plan-gate, state-modules, hook-modules, ci; no conflicts). Strong wording restored in
  README, COMPLIANCE and module docs where code + a test back it; interim wording kept for what was not built
  (PowerShell compound parsing, scope-lease denied-acquire row, sink-resolver no-conflict/dry-run rows,
  NotebookEdit total caps). Not pushed; merge stays with Tim.
- 2026-10-16 — **Claims-truth PR 1 (docs-only) built** on `docs/claims-truth-reword`: every public claim the
  code does not yet back is reworded to interim honest wording (each logged in CLAIMS-FIX-SPEC §7 so PR 2 can
  restore the strong form once code backs it); permanent rewords (Q6 coverage sentence, Q7 "single-use by
  convention", Q8a tree-vs-pushed-commit) applied; STATE/BACKLOG refreshed; staged-import machine paths
  genericized. Direction per the 2026-07-07 walkthrough (CLAIMS-FIX-SPEC §5); merge stays with Tim.
- 2026-09-29 — **Project thawed** — the ❄ pause (2026-07-05) ended after the HIPAAPath push; StrictLock resumed.
- 2026-06-25 — **Wave 3 COMPLETE — `sink-resolver` + `liveness-scan` shipped to LOCAL main** (`189285c`,
  **not pushed**). Built solo (drafting/file-ops per the charter), each adversarially verified via a
  perspective-diverse workflow fan-out (3 lenses for the correctness-critical sink-resolver where silent
  clobber = data loss; 2 for the report-only liveness-scan), fixes applied, then guard-landed (clean-main
  pre-flight → `merge --no-ff` → full-suite green → no push). The verify earned its keep: it caught a **proven
  data-loss defect** in sink-resolver's `check` and a **silent-clobber coverage gap**, both fixed before
  landing. Coverage guard added as a genuine upgrade to the core safety claim (the tool now PROVES every
  conflicted sink was regenerated, vs trusting operator CHECK_CMD coverage). liveness-scan came back
  zero-blocking. Registered in README + COMPLIANCE §K/§L + CI + roadmap, then **PUSHED + MERGED to origin/main
  via PR #6 (Tim's go-ahead 2026-06-25; CI green, 8 jobs).** Remaining harvest: Wave 4 (rituals/gates), then
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
