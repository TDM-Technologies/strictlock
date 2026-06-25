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

### Wave 1 — workhorse modules — *ready* (current Next Action; auto-merges the mechanical modules per the 2026-06-25 verify-gate decision)
smoke-only assertion ESLint rule (flags `toBeDefined` / bare `expect(x)` / `toContain`) ·
generated-sink integrity gates (manifest-freshness + pre-push → `generated-sink-commit-gate` /
`-prepush-gate`) · the externalized-memory **projection bundle** (record schema + git-free/
clock-free render + fenced-region splicing + UTC-timestamp guard + required-body validation).

### Wave 2 — marquee IP — *planned*
Cleanup-Day / rule-archaeology module + rule-corpus schema **+ a design paper** on the inversion
(zero-fire telemetry = the guard working, not dead) and the trip-test · ROI / carrying-cost gate
+ harvest ritual as one governance narrative (`GOVERNANCE.md`) · blast-radius review-depth trigger
(after pattern-config genericization).

### Wave 3 — concurrency primitives — see [## concurrency](#concurrency)
`scope-lease` (decided: Option A) · `sink-resolver` (behind its pure-generator precondition) ·
liveness scanner. Sourced from the vault `work-registry.py`.

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

### paper.md §6 ↔ shipped-log reconcile — *ready*
`paper.md` §6 states the decision log records "every gate decision (allow *and* deny)" and is
"tamper-evident". The shipped gate logs **denials by default** (`plan-gate-denies.log`); `CONFIG.md`
and the new `COMPLIANCE.md` describe this accurately — so §6 is the lone overclaimer (surfaced by
the Wave 0 verify-gate). Either reword §6 to match the shipped denials-by-default behavior, or build
the opt-in allow/deny JSONL log to make "allow and deny" true (ties to the deferred memory-cap log
and SOC 2 CC7). Docs-honesty item; do before §6 goes more public.

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

### scope-lease — git-native exclusive path lock — *planned · decided Option A* (Wave 3, flagship)
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

### sink-resolver — deterministic generated-file merge-conflict resolution — *planned* (Wave 3)
`merge=binary` on the sink + a `post-merge` hook that regenerates + a `resolve` subcommand
(regenerate from the merged sources, byte-check, escalate on any non-sink conflict) + a CI
`check` backstop for web-UI merges. **Loud precondition (on the tin):** only sound if the sink
is a pure, deterministic, `--check`-able render of its sources — otherwise `--ours`/regenerate
silently clobbers peer edits. Default = the vault's git-native shape; documented alternative =
HIPAAPath's pre-merge worktree script (`conductor-resolve.py`, circuit breakers) for web-UI
flows where `merge=binary`/hooks are inert. Pairs with the Wave-1 externalized-memory projection
bundle (the kind of pure renderer it requires). §6.

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

## ci

### OS test matrix + action refresh — *ready*
CI (`.github/workflows/ci.yml`) runs ubuntu-only. Add `windows-latest` + `macos-latest` to the
Python matrix so the suite's OS-agnostic claim is **proven**, not just reviewed (the memory-cap
CRLF test then runs on a real Windows box). While in `ci.yml`, also bump `actions/checkout` and
`actions/setup-python` to current majors — the runners now force them onto Node 24 and warn on
every run (Node-20 deprecation, observed 2026-06-25). Low effort; land with/just before any
OS-sensitive change.

## harvest-harness

### workflow agents must not mutate the shared main checkout — *ready* (do before Wave 1 auto-merge)
In the Wave 0 run a verify/consistency agent left a stray uncommitted `git merge` in the shared
`main` checkout (zero loss; aborted). Build agents were told to stay in their worktree; the
verify/consistency prompts were not. Harden: mergeability checks use `git merge-tree` (pure
dry-run) or always `--abort`; no agent touches the shared main checkout. Under Wave 1 auto-merge a
stray half-merge in main is a silent hazard.

---

## Done
- **2026-06-25** — **Harvest Wave 0 shipped** (merged to main): memory-cap self-wedge fix
  (changed-region scan, CRLF-safe, +4 tests) · `plan-gate/AUTHORING.md` · suite-wide
  `COMPLIANCE.md`. Built + adversarially verified by the harvest workflow harness — the pilot that
  proved the verify-gate.
- **2026-06-22** — dev scaffold stood up (`dev/` — STATE + BACKLOG, dogfooding `externalized-memory`).
- **2026-06-01** — v1: plan-gate, commit-msg-gate, memory-cap, externalized-memory shipped.
