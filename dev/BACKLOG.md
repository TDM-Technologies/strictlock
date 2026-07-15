# StrictLock — backlog

Unfinished work and open design questions, one entry each. The wave-sequenced **harvest map**
(HIPAAPath/vault "old process" → StrictLock modules) is in [`HARVEST-PLAN.md`](HARVEST-PLAN.md)
and is authoritative for per-item detail and dispositions; the public *module* roadmap is in
[`../roadmap.md`](../roadmap.md); this file is the finer-grained "what's left and why." Each
entry is marked:

- **Ready** — scoped; could be picked up now.
- **Planned** — committed; mostly prose/templates or a clean extraction; ships soon.
- **Exploring** — a design direction under consideration; discuss before building.
- **Pending import** — blocked on an artifact that lives off-machine (see
  [`STATE.md`](STATE.md) → Constraints).

---

## Harvest waves (forward map)

[`HARVEST-PLAN.md`](HARVEST-PLAN.md) holds per-item detail, the two-axis scoring, and the §4
vault-sourcing decision. The waves as entries:

### Wave 0 — finish/fix the shipped suite — *shipped 2026-06-25*
memory-cap changed-region fix · plan-authoring guide · compliance-mapping — all three built,
adversarially verified, and merged to main (the pilot that proved the harvest workflow harness).

### Wave 1 — workhorse modules — *COMPLETE 2026-06-25 (3 of 3; auto-merged via the guard)*
**SHIPPED** (main @ e436a72, pushed, CI wired): generated-sink integrity gates (manifest-freshness +
pre-push → `generated-sink-commit-gate` / `generated-sink-prepush-gate`) · the externalized-memory
**projection bundle** (record schema + git-free/clock-free render + fenced-region splicing +
UTC-timestamp guard + required-body validation) · **`eslint-plugin-strictlock`** (the smoke-only assertion
rule as a standalone npm package — the suite's first JS / Gates-family sibling). All verify-clean; landed by
the fail-closed merge guard.

### eslint-plugin-publish — *DONE — published to npm* (see [Done](#done))
`eslint-plugin-strictlock` is **live on npm**:
[`0.1.0`](https://www.npmjs.com/package/eslint-plugin-strictlock) (2026-06-25) and
[`0.1.1`](https://www.npmjs.com/package/eslint-plugin-strictlock) (2026-07-05), acct `downsmullen`. Built,
RuleTester-green, and CI-tested in-repo. (Genericization done: the HIPAAPath baseline.json was replaced by
ESLint rule options — `baseline` / `marker` / `extraSmokeMatchers`, default strict.)

### Wave 3 — concurrency primitives — *COMPLETE + MERGED to origin/main 2026-06-25 (3 of 3; PR #6, CI green)* — see [## concurrency](#concurrency)
**SHIPPED:** `scope-lease` (Option A git-ref CAS exclusive path-lock; 37 tests; 3 lenses clean) · `sink-resolver`
(binary-sink auto-resolver; 29 tests; 3 lenses → 1 proven blocking + 1 structural gap fixed) · `liveness-scan`
(read-only fleet reporter; 31 tests; 2 lenses clean). Full suite **185 tests / 9 modules** green. Registered in
README + COMPLIANCE §K/§L + CI + roadmap, then **PUSHED + MERGED to origin/main via PR #6 (`d17e1a9`, all 9 CI
jobs green incl python 3.8–3.12).** Next harvest wave: Wave 4 (rituals/gates), then Wave 2 (marquee, last).

### Wave 2 — marquee IP — *DEFERRED to end of queue (Tim, 2026-06-25)*
Run LAST (after Waves 3 + 4), by choice — still the highest-cred work, just sequenced last now the salvage
deadline is gone. Cleanup-Day / rule-archaeology module + rule-corpus schema **+ a design paper** on the
inversion (zero-fire telemetry = the guard working, not dead) and the trip-test · ROI / carrying-cost gate
+ harvest ritual as one governance narrative (`GOVERNANCE.md`) · blast-radius review-depth trigger
(after pattern-config genericization).

### Wave 4 — rituals + remaining gates — *planned*
session-ritual checklist templates · WP-interaction analysis · pre-WP currency check ·
test-protection co-commit guard.

### Later / experimental
reversibility-tiers + auto-commit (entries below; need a shared evidence window) · autohygiene ·
read-discipline doc · skill-learnings template · plans-into-repo + compound-FP analysis (both
*pending import*, entries below).

---

## plan-gate

### plans-into-repo — *pending import*
Bring the historical plan files that drove the gate's development into the repo as
worked examples. They carry real `allowed_paths` / `allowed_commands` allowlists and
document, plan by plan, *why* each gate refinement happened (worktree-anchored
resolution, exact-path matching, the cd-compound tightening). The files currently
live only on the original development workstation — see the maintainer's local import
checklist. Lands a `plan-gate/examples/` expansion.

### read-only compound false-positive analysis — *pending import*
A read-only command *compound* (`a && b`, `a; b`, `a || b`) can be denied even when
every segment is independently read-only — the worktree guard or the command check
fires on the whole line. Quantify the real impact from deny-log data (share of denies
that are legitimate-pattern false positives vs genuine scope violations, and the extra
round-trips each forces), then decide: broaden compound handling, or document
"no change — friction is low vs. risk." Any permissive fix must **not** reopen the
`cd <path> && <arbitrary>` bypass that the cd-compound tightening deliberately closed.
Findings-first; any gate change is separate work. Needs the runtime deny log (off-machine).

### authoring & discipline guide — *shipped 2026-06-25* (Wave 0)
`plan-gate/AUTHORING.md` merged, every rule grounded against the live gate. Code-grounded
correction baked in: the gate keys solely on `status == active` and treats *every* non-active
value as inert, so the load-bearing rule is the **ordering** (flip status away from active as the
last gated action), not an `executed` keyword. Original scoping below.

`SCHEMA.md` (frontmatter) and `CONFIG.md` (env vars) exist, but there's no single guide
to the authoring *discipline*: the `allowed_paths` / `allowed_commands` rules of thumb
(exact match, no globs; directory entries authorize nothing; narrow destructive-capable
prefixes so a bare `git checkout` entry can't also authorize `git checkout -- .`),
`worktree_bypass` semantics, the active-plan rule, and the lifecycle (flip `status` to
`executed` as the *last* gated action, or the gate stops authorizing the plan's own
close-out). A project-agnostic "plan-authoring" standard already exists privately and
can be genericized into the repo (likely `plan-gate/AUTHORING.md`).

### paper.md §6 ↔ shipped-log reconcile — *RESOLVED (built, 2026-07-15)*
Ruled BUILD by the maintainer: shipped the **authorizations record** — one unified, hash-chained
`plan-gate-decisions.log` (allow *and* deny, all gated surfaces, full-by-default with
`PLAN_GATE_LOG_DECISIONS=deny` opt-down), a `verify-log` mode, and log-dir self-protection —
replacing the three per-surface logs. §5/§6, `CONFIG.md`, `COMPLIANCE.md` §B, and the module
README were reworded to exactly the shipped behavior, with honest tamper-evidence scope
(integrity-without-secret; host-level adversary caveats stated, not implied away). Spec:
[AUTHORIZATIONS-RECORD-SPEC.md](AUTHORIZATIONS-RECORD-SPEC.md).

### reversibility-tiered authorization — *exploring* (harvest: later)
Shift from pure prevention toward "prevent where reversibility runs out, account for
where it's abundant":
- **Tier 0** (in-repo, git-tracked, additive / trivially reversible): auto-allow +
  auto-commit + log.
- **Tier 1** (reversible but high drift-blast — shared state files, source): keep the allowlist.
- **Tier 2** (irreversible / outward-facing — history rewrite, `rm` outside the repo,
  external API/email/calendar sends, push-to-prod): prevent / confirm.

Caveat: accountability only substitutes for prevention if the audit is *actually
reviewed*; where it won't be, keep prevention. Depends on auto-commit (below). Discuss
and sequence before building.

## memory-cap

### changed-region scan — *shipped 2026-06-25* (Wave 0)
Merged: introduced/modified `- `-line multiset delta, CRLF/LF-agnostic, +4 tests (wedge-removed ·
good-property-kept · CRLF · Edit-introduces-deny). The optional env-gated JSONL decision log stays
**deferred** (ties to the CC7 "allow and deny" log — see `paper.md §6 ↔ shipped-log reconcile`).
Original scoping below.

The shipped cap scans the whole file, which can **self-wedge**: once any over-cap `- `
line exists, every later compliant write that doesn't also delete it is denied — so the
index can then only be written through non-enforcing paths, and violations accumulate.
Fix (validated in a private build): scan only the `- ` lines a write *introduces or
modifies* — the delta between the write's result and the file's pre-existing set
(rstrip-normalized multiset) — never pre-existing untouched lines. Preserves the good
property (no new over-cap entry) and removes the wedge. Optionally add an opt-in JSONL
decision log, env-gated (mirroring `PLAN_GATE_LOG_DIR`).

## concurrency

> **Wave 3 is COMPLETE + MERGED to origin/main (2026-06-25, PR #6, CI green).** `scope-lease`,
> `sink-resolver`, and `liveness-scan` are live on the public main; the entries below are retained for
> provenance. See the [Done](#done) section for the close-out.

### scope-lease — git-native exclusive path lock — *SHIPPED 2026-06-25* (Wave 3, flagship)
Decouple the vault's git-ref CAS collision lease (`work-registry.py` Feature C — `refs/locks/*`
`update-ref` compare-and-swap, transactional all-or-nothing acquire over a path set, monotonic
fencing token as the zombie/reclaim backstop, fail-loud single-machine boundary; built + 160
tests) from its EA-OS work-record glue and ship it standalone. **Decision (2026-06-24):
Option A** — the primitive takes a **path list + lock id**; plan-gate's active plan file is the
*default, headlined* source (claims your existing `allowed_paths` for free), with a
`--paths` / stdin / own-frontmatter fallback so a fleet not running plan-gate can still adopt
it. Keeps "every module standalone" intact while keeping the "enumerated → enumerated **+
exclusive**" flagship-extension story. Safety invariants (keep from the vault): `refs/locks/*`
coordinates agents only — never wall a human's manual merge; surface reclaims, never
auto-reap. Full spec: [`HARVEST-PLAN.md`](HARVEST-PLAN.md) §6.

### sink-resolver — deterministic generated-file merge-conflict resolution — *SHIPPED 2026-06-25* (Wave 3)
`merge=binary` on the sink + a `resolve` subcommand (regenerate from the merged sources, byte-oracle,
escalate on any non-sink conflict, finalize) + a `check` CI backstop for web-UI merges + a **coverage
guard** (a conflicted sink the generator left byte-unchanged escalates, so a generator blind to a configured
sink can't silently keep 'ours'). Loud precondition on the tin (pure, deterministic, `--check`-able render).
Default = the vault's git-native shape; `conductor-resolve.py` documented as the pre-merge web-UI variant.
29 tests; 3 adversarial lenses (1 proven blocking + 1 structural gap, both fixed pre-land). §6.

### liveness-scan — read-only fleet-liveness reporter — *SHIPPED 2026-06-25* (Wave 3)
Ported from HIPAAPath `conductor-scan.py`, decoupled from its machine-specific defaults. Classifies each git
worktree (running/stalled/done-unmerged/ambiguous/idle/clean) on a heartbeat-or-commit-mtime signal,
attributing worktrees to plan-gate sessions (absolute `allowed_paths`, excludes `worktree_bypass`). Report-only:
never reaps, exits 0 always; ambiguous stalls escalate with a non-destructive recovery command. env-configured
(`LIVENESS_SCAN_*`), no machine paths. 31 tests; 2 adversarial lenses clean (zero blocking; 4 safe-direction
polish items folded in). Closes the concurrency family (plan-gate enumerates → scope-lease makes exclusive →
sink-resolver heals generated-merge → liveness-scan watches the fleet).

## docs

### compliance-mapping (SOC 2 / ISO 42001 / ISO 9001) — *shipped 2026-06-25* (Wave 0)
Root `COMPLIANCE.md` merged — suite-wide (all four modules), non-certification stance preserved,
control IDs framed as pointers into published criteria (not claims of satisfaction). Discoverability
follow-up below (`COMPLIANCE.md discoverability`). Original scoping below.

Expand `plan-gate/paper.md` §6 ("Compliance mapping (condensed)") into a standalone
`COMPLIANCE.md`: which control each gate mechanism maps to (exact-path authorization → SOC 2
CC6; append-only decision log → CC7; PLAN→CONFIRM→EXECUTE → CC8 / ISO 9001 §8.5; the approved
plan as a documented artifact → ISO 9001 §7.5) and what an auditor can trace. Content largely
exists in the paper; this is the genericized, reusable reference. Also on the public roadmap.

### COMPLIANCE.md discoverability — *ready*
`COMPLIANCE.md` (root, shipped 2026-06-25) is reachable only by knowing the filename — add a
one-line cross-link from the root `README.md` and `roadmap.md` before public launch.

## cross-cutting

### auto-commit / drift automation — *exploring* (harvest: later)
A `PostToolUse` hook that commits trivially-reversible (Tier 0) writes immediately, so
working-tree state stays durable and auditable instead of accruing uncommitted drift.
Enabling prerequisite for the Tier-0 "auditable via git" guarantee above. Open
questions: which paths qualify, the message convention, and the interaction with
`commit-msg-gate`. Discuss and sequence before building.

### per-project gate tiering (DAL A–E) — *exploring*
Make gate strictness configurable per project by an assurance level (borrowing DO-178C's DAL A–E:
A = highest criticality, E = lowest). A high-assurance project runs the full strict gate set; a
low-criticality one runs a lighter profile. Ship as named "gate profiles" / a tier env var each
module reads, so one knob sets the posture across the suite instead of wiring each gate per
project. Composes with reversibility-tiers (that tiers by action reversibility; this by project
criticality).

### secret / non-public spill gate — *ready* (dogfood on StrictLock)
A fail-closed pre-commit/pre-push gate that refuses to commit private keys, tokens, stray UUIDs,
machine paths, or other non-public content — catching a leak at the BOUNDARY, not just in CI after
it's pushed (the CI already greps for secrets/UUIDs post-hoc; this makes it structural and
pre-commit). Generalize the CI scan's patterns into a standalone gate and dogfood it on StrictLock
so non-public material can't reach the public repo. Honest limit: a regex gate catches
keys/tokens/UUIDs/paths and obvious PII, but "is this personal/strategic content that shouldn't be
public" stays partly a judgment call — pair the mechanical gate with that caveat (reduces, not
eliminates, spill risk).

## ci

### OS test matrix — *ready*
CI (`.github/workflows/ci.yml`) runs ubuntu-only. Add `windows-latest` + `macos-latest` to the
Python matrix so the suite's OS-agnostic claim is **proven**, not just reviewed (the memory-cap
CRLF test then runs on a real Windows box). Low effort; land with/just before any OS-sensitive change.
(Action refresh — the `actions/checkout` / `setup-python` / `setup-node` Node-20 deprecation — was done
2026-07-05: bumped to `checkout@v7` / `setup-python@v6` / `setup-node@v6`.)

## harvest-harness

### workflow agents must not mutate the shared main checkout — *DONE 2026-06-25 (proven in Wave 1)*
Closed structurally by the fail-closed merge guard (`harvest-merge-guard.sh`): harvest branches are
merged ONLY by that script, never by a free-form agent. It refuses on a dirty/mid-merge main
(catches a stray agent write), `--abort`s on conflict, rolls back any module whose tests fail, and
never pushes. Proven end-to-end landing both Wave-1 modules — no agent touched the shared main
checkout; build agents work only in pre-made isolated worktrees. (Origin: a Wave-0 verify agent
left a stray uncommitted `git merge` in main — zero loss, aborted.)

---

## Done
- **2026-07-05** — **`eslint-plugin-strictlock` published to npm** — `0.1.0` (2026-06-25) then `0.1.1`
  (2026-07-05), acct `downsmullen`: <https://www.npmjs.com/package/eslint-plugin-strictlock>. Closes the
  `eslint-plugin-publish` item (the last open thread from Wave 1). The action refresh (`checkout@v7` /
  `setup-python@v6` / `setup-node@v6`) landed alongside; the OS matrix stays open above.
- **2026-06-25** — **Wave 3 COMPLETE + MERGED to origin/main** (`d17e1a9`, PR #6, all 9 CI jobs green).
  `sink-resolver` (binary-sink auto-resolver; 29 tests; 3 lenses → a proven `check`-data-loss blocking + a
  silent-clobber coverage gap, both fixed pre-land; coverage guard added as a core-safety upgrade) and
  `liveness-scan` (read-only fleet reporter; 31 tests; 2 lenses clean + 4 safe-direction polish items). Built
  solo, adversarially verified by workflow fan-out, guard-landed (clean-main pre-flight → `merge --no-ff` →
  full-suite green → no push), then pushed as PR #6 → CI-verified clean → fast-forward-merged to origin/main
  on Tim's go-ahead. Full suite **185 tests / 9 modules**. Registered in README + COMPLIANCE §K/§L + CI + roadmap.
- **2026-06-25** — **Wave 3 `scope-lease` flagship SHIPPED** (main @ a6054e8): git-native zero-service
  exclusive lock over a path set (`refs/locks/*` CAS), Option A — faithful port of vault Feature C,
  retargeted to a path-source seam (plan-gate adapter default + standalone fallbacks). Built + verified by
  build + 3 perspective-diverse adversaries (all clean); integrator added a commit-race abort regression
  (37 tests); registered in README/COMPLIANCE §J/CI; roadmap.md reconciled. Queue reordered: marquee → end.
- **2026-06-25** — **Harvest Wave 1 COMPLETE (3 of 3)** (main @ e436a72, pushed): generated-sink
  commit-gate + prepush-gate + the externalized-memory projection bundle + `eslint-plugin-strictlock`
  (smoke-only-assertions rule, own npm package). Built by the fan-out harness (build →
  adversarial-verify → consistency; the plugin via a 2-agent build→verify run), landed by the
  fail-closed merge guard, registered in README/COMPLIANCE (§I) + CI (python/shell/node jobs). Also
  proved the harness-hardening guard in use. Open: `eslint-plugin-publish` (npm release call).
- **2026-06-25** — **Harvest Wave 0 shipped** (merged to main): memory-cap self-wedge fix
  (changed-region scan, CRLF-safe, +4 tests) · `plan-gate/AUTHORING.md` · suite-wide
  `COMPLIANCE.md`. Built + adversarially verified by the harvest workflow harness — the pilot that
  proved the verify-gate.
- **2026-06-22** — dev scaffold stood up (`dev/` — STATE + BACKLOG, dogfooding `externalized-memory`).
- **2026-06-01** — v1: plan-gate, commit-msg-gate, memory-cap, externalized-memory shipped.
