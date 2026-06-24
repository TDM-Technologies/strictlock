# StrictLock — Harvest Plan

_Harvesting the "old process" machinery built inside **HIPAAPath** into **StrictLock**'s
public, env-configured module suite — plus the followup marketing._

Derived 2026-06-24 from a value-investigation pass: 11 read-only agents deep-read every
`docs/design/*` doc, `.claude/scripts/*`, and the relevant `CLAUDE.md` sections in
HIPAAPath; a 12th profiled the existing downsmullen.com article voice. Nothing was
executed. Each mechanism was scored on the two axes that decide scope here:

- **dev-usefulness** — would the broader dev community actually adopt/copy it?
- **street-cred** — does shipping/writing about it strengthen the consulting positioning
  (novel, defensible, demoable, real "I built and run this")?

44 mechanisms were assessed; deduped to ~30 distinct items below.

---

## 1. Where the value is (the answer to "what gives me cred AND helps devs")

The corpus splits cleanly into **two kinds of value**. The tiered umbrella should lead
with the first and stock the shelves with the second.

### Marquee IP — street-cred, consulting-differentiated (novel, defensible, demoable)
The pieces that say *"I think about the meta-layer of agent governance, not just 'don't
let it delete files'"* — a genuine gap in the market:

1. **Rule-archaeology / "Cleanup Day"** — the reverse-audit that asks *"do my own agent-
   control rules still earn their keep?"* with the load-bearing **inversion** (zero-fire
   telemetry = the guard is *working*, not dead) and the **trip-test** (prove a guard
   still fires on its original failure; blocks CVE-2024-6387-class reintroduction). The
   prior-art sweep found only LaunchDarkly's permanent-flag carve-out as precedent. This
   is the single highest-cred item in the corpus.
2. **Binary-sink auto-resolver** — deterministic auto-resolution of generated-file merge
   conflicts (`--ours` + regenerate from a pure function, byte-oracle verify). Novel claim:
   *safe because the generator is pure and inputs are conflict-free by construction.*
3. **Blast-radius review-depth trigger** — objective, data-driven "does this change need a
   deep security review?" scoring; replaces reviewer judgment-drift with git-history + path
   patterns. (High cred; needs genericization work — see §4.)
4. **ROI / carrying-cost gate + the harvest ritual** — the *forward* governance gate
   ("does this new structure earn its build + carrying cost?") that pairs with Cleanup Day
   (the reverse audit) into one story: *how to keep a governance system from out-massing
   what it ships.*

### Workhorses — broad dev-community adoption (copyable, low coupling, immediate value)
The pieces a stranger can drop into their repo today:

5. **Smoke-only assertion detector** (ESLint rule) — flags false-safety-net tests
   (`toBeDefined`, bare `expect(x)`, `toContain`). Zero coupling, applies to any JS/TS
   project. Clean standalone win.
6. **Generated-sink integrity gates** (manifest-freshness + pre-push) — fail-closed,
   layered, byte-exact "don't ship a stale generated artifact" gate. Applies to any repo
   with a checked-in manifest/index/schema/swagger/README.
7. **The externalized-memory projection bundle** — the deterministic, **git-free +
   clock-free** rendering layer (record schema + region renderer + fenced-region splicing +
   canonical-UTC timestamp guard + required-body validation). Turns the shipped
   externalized-memory module from "a blackboard" into "a blackboard with an auditable,
   reproducible status projection."
8. **The docs** — plan-authoring discipline guide, compliance-mapping (SOC 2 / ISO 42001 /
   ISO 9001), and the memory-cap self-wedge fix. Highest value-per-effort; content largely
   already exists.

> The marquees are what get written about; the workhorses are what get starred and forked.
> Ship both, but sequence the workhorses first (they're nearer-ready) and use the marquees
> as the marketing centerpieces.

---

## 2. The tiered umbrella (families)

StrictLock stays headlined by its sharp thesis ("fail-closed action authorization"), with
the rest landing as **labeled sibling families** so the headline doesn't blur:

| Family | What it is | Members (✓ = shipped) |
|---|---|---|
| **Gates** | Fail-closed / proposal-side authorization at the tool boundary | plan-gate ✓, commit-msg-gate ✓, memory-cap ✓, blast-radius, smoke-only ESLint, generated-sink gates, binary-sink resolver, test-protection co-commit, pre-WP currency, reversibility-tiers |
| **Hygiene** | State-file / context / worktree drift control | externalized-memory ✓ (+ projection bundle), memory-cap fix, autohygiene, read-discipline KPI |
| **Rituals** | Kickoff / close-out / plan-authoring discipline | session-ritual checklists, WP-interaction analysis, skill-learnings siblings |
| **Meta-process** | Governance-of-governance (the marquee layer) | rule-archaeology/Cleanup-Day, ROI gate, harvest ritual, liveness scanner, escalation substrate, auto-commit |
| **Docs** | Reference material that lowers adoption friction | plan-authoring guide, compliance-mapping, plans-into-repo examples |
| **Concurrency** | Multi-session coordination primitives | scope-lease (+ resolver/scanner straddle Gates/Meta) |

---

## 3. Disposition table (deduped)

Dispositions: **harvest-now** (clean, ready) · **planned** (ship soon; mostly prose/templates) ·
**exploring** (needs design/evidence first) · **keep-private** (too HIPAAPath-coupled to transfer) ·
`import` = blocked on an off-machine artifact.

| Mechanism | Family | dev | cred | Disposition | Wave |
|---|---|:--:|:--:|---|:--:|
| memory-cap changed-region fix (self-wedge) | Hygiene | H | H | harvest-now (fixes shipped defect) | 0 |
| plan-authoring discipline guide | Docs | H | H | harvest-now (private standard exists) | 0 |
| compliance-mapping (SOC2/ISO) | Docs | H | H | harvest-now (content in paper §6) | 0 |
| smoke-only assertion ESLint rule | Gates | H | H | harvest-now | 1 |
| generated-sink gates (manifest-freshness + prepush) | Gates | H | H | harvest-now ⚠ (roadmap says "Exploring"; deep-read says near-ready) | 1 |
| session-memo record schema | Hygiene | H | H | harvest-now | 1 |
| deterministic render (git-free/clock-free) | Hygiene | H | H | harvest-now | 1 |
| fenced-region splicing | Hygiene | H | M | harvest-now | 1 |
| canonical-UTC timestamp guard | Hygiene | H | H | harvest-now | 1 |
| required-body validation | Hygiene | H | M | harvest-now | 1 |
| rule-archaeology / Cleanup Day (+inversion, trip-test) | Meta | H | H | harvest-now ★marquee | 2 |
| rule-corpus.json schema | Meta | H | M | harvest-now (with above) | 2 |
| ROI / carrying-cost gate rubric | Meta | H | H | planned (doc) | 2 |
| harvest ritual (light pass A–C) | Rituals | M | H | planned (some coupling) | 2 |
| blast-radius review-depth trigger | Gates | M | H | planned (needs pattern-config genericization) | 2 |
| **git-ref CAS collision lease (vault Feature C)** | Concurrency | H | H | **harvest-now ★ — Concurrency flagship; source from vault; retarget to plan-gate `allowed_paths`** | 3 |
| binary-sink auto-resolver | Gates/Conc | M | H | harvest-now (source vault git-native shape; precondition: pure regenerator) | 3 |
| liveness scanner (conductor-scan) | Meta/Conc | H | M | harvest-now | 3 |
| ~~scope-lease (HIPAAPath mtime hint)~~ | Concurrency | L | L | superseded by vault lease above | — |
| escalation-briefing substrate (conductor-escalate) | Meta | M | M | exploring (needs LLM "Layer 2") | later |
| WP-interaction analysis | Rituals | H | H | harvest-now (discipline/checklist, no code) | 4 |
| pre-WP currency check | Gates/Rituals | H | M | planned (prose rubric) | 4 |
| test-protection co-commit guard | Gates | M | M | planned (clean extraction) | 4 |
| session-ritual checklists (kickoff/close) | Rituals | H | M | planned | 4 |
| reversibility-tiered authorization | Gates | M | M | exploring (needs auto-commit) | later |
| auto-commit / drift | Meta | M | L | exploring | later |
| autohygiene (worktree housekeeping) | Hygiene | M | M | exploring | later |
| read-discipline / cache-ratio KPI | Hygiene | M | L | exploring (ship as doc+telemetry, not a hook) | later |
| skill learnings.md siblings | Rituals | M | L | exploring (template, not code) | later |
| plans-into-repo worked examples | Docs | H | H | planned · `import` | later |
| read-only compound FP analysis | Meta | M | M | exploring · `import` | later |

**Keep-private** (HIPAAPath-coupled; copying them out gives a stranger no signal):
kickoff-refresh ritual · handoff-freshness hook · handoff-at-hygiene-lint · session-memo-lint ·
handoff-session-memo-lint · session-memo-presence kickoff step · conductor-merge-gate ·
conductor-ci-context feeder.

---

## 4. Conductor — the verdict (decompose, don't harvest whole)

Conductor is not one thing; its pieces have opposite verdicts. Your "primitives only"
instinct was right — the data just refines *which* primitives:

| Conductor piece | Verdict | Why |
|---|---|---|
| binary-sink auto-resolver | **harvest-now** | ~60% of Conductor's value; novel; low/medium effort |
| liveness scanner (`conductor-scan`) | **harvest-now** | low coupling; portable YAML-frontmatter + mtime scan |
| scope-lease (HIPAAPath) | **keep-private / supersede** | advisory mtime hint only; the vault has a far stronger one (below) |
| escalation substrate (`conductor-escalate`) | exploring | needs the deferred LLM "Layer 2" to tell a full story |
| merge-gate (`conductor-merge-gate`) | **keep-private** | only sound atop the bespoke smoke-check + blast-radius stack |
| ci-context feeder | **keep-private** | pure integration glue between two HIPAAPath systems |

### ✅ RESOLVED (2026-06-24): source the primitives from the VAULT, not HIPAAPath
Compared both implementations directly. They are **not** "the same idea harvested twice":

**Lease — NOT the same; the vault wins decisively.**
- HIPAAPath `conductor-lease.py` (154 lines) = a file-mtime **advisory liveness hint**
  (`.claude/.conductor-lease.json`, read by existence+mtime, "report-only", "NEVER reaped").
  It does **not** prevent two agents editing the same file. A deliberate Slice-2 placeholder.
- Vault Feature C (in `work-registry.py`) = a real **git-native atomic collision lease**:
  `git update-ref` CAS on `refs/locks/*` (off-branch), transactional all-or-nothing acquire
  over a record's `allowed_paths`, repo-relative normalization as the primary same-file guard,
  monotonic fencing token as the zombie/reclaim backstop, fail-loud single-machine boundary.
  9-agent design panel + skeptic; built; 160 tests green. **Genuinely novel IP.**
- → **Harvest the vault lease.** Decouple its git-ref core from the EA-OS `start`/work-record
  glue and retarget it to claim a **plan-gate plan's `allowed_paths`**. This makes it the
  **multi-agent extension of the flagship**: plan-gate says "only these paths"; the lease adds
  "and you hold them *exclusively* against other agents." Enumerated → enumerated **+ exclusive**.

**Resolver — same recipe, pick the vault's shape as default.**
- Vault Feature B = git-native: `merge=binary` on the generated index + a `post-merge` hook
  that auto-regenerates + `work-registry.py resolve` (~47 lines: regenerate from the
  already-merged records, byte-check, `git add`; escalate on any non-sink conflict) + a CI
  `check` backstop. Clean, idiomatic git. Caveat: `merge=binary`/hooks are **inert on
  GitHub web-UI merges** → the CI job covers that.
- HIPAAPath `conductor-resolve.py` (627 lines) = a **pre-merge worktree script** with circuit
  breakers / identity-keyed restart ceiling / dry-run default. Heavier, but **workflow-agnostic**
  (runs regardless of how the final merge happens) because it deliberately rejected the
  merge-driver approach for HIPAAPath's web-UI-merge flow.
- → **Source the git-native shape from the vault as the default**; document HIPAAPath's
  pre-merge variant as the **web-UI-merge option**; ship behind a loud precondition: *only
  sound if your generated sinks have a pure, deterministic, `--check`-able regenerator.*

**On "locked into the Anthropic ecosphere":** the EA-OS *process* (SessionStart hooks, skills,
memory) is Claude-Code-coupled — but the B/C *primitives* are **pure git** (`update-ref` CAS,
`merge=binary`, a `post-merge` hook), the most harness-agnostic layer there is. The vault
deliberately rebuilt them "git-native, zero-service" (the panel rejected a daemon). So the
**AI-agnostic one IS the vault primitive** — once lifted off the `start`/work-record glue.
StrictLock's whole job is to ship the primitive minus the process.

**No duplication:** StrictLock gets the genericized git-core; the vault keeps its
EA-OS-integrated copy. One primitive, two consumers. Source of truth for the harvest = the
vault (`work-registry.py`, newer + 160 tests); HIPAAPath `conductor-resolve.py` is reference
only (for the web-UI-merge placement variant + circuit-breaker maturity if needed at scale).

---

## 5. Sequenced waves

- **Wave 0 — finish/fix the shipped suite (lowest effort, do first).** memory-cap
  self-wedge fix (defect in a live module) · plan-authoring guide (`plan-gate/AUTHORING.md`)
  · compliance-mapping (`COMPLIANCE.md`). Mostly porting prose that already exists.
- **Wave 1 — workhorse modules (clean extraction, broad adoption).** smoke-only ESLint
  rule (new `gates` member) · generated-sink gates (rename manifest-freshness/prepush →
  `generated-sink-commit-gate` / `-prepush-gate`) · the externalized-memory **projection
  bundle** (schema + render + splicing + UTC guard + body-validation) as an add-on to the
  shipped module.
- **Wave 2 — marquee IP (street-cred centerpieces; more design-export work).** rule-
  archaeology / Cleanup-Day module + rule-corpus schema **+ a design paper** on the
  inversion/trip-test (the high-cred export) · the ROI gate + harvest ritual as the paired
  governance narrative (`GOVERNANCE.md`) · blast-radius (after pattern-config
  genericization: env/YAML registry for security markers, drop the hardcoded paths,
  ship the plan-gate parser-pinning test with it).
- **Wave 3 — concurrency primitives (sourced from the vault; full spec in §6).**
  `scope-lease` (the flagship git-ref lock, retargeted to plan-gate `allowed_paths`) ·
  `sink-resolver` (behind its pure-generator precondition) · liveness scanner ·
  escalation substrate as exploring.
- **Wave 4 — rituals + remaining gates.** session-ritual checklist templates · WP-
  interaction analysis · pre-WP currency check · test-protection co-commit guard.
- **Later / experimental.** reversibility-tiers + auto-commit (need a 2–3 month evidence
  window together) · autohygiene · read-discipline doc · skill-learnings template ·
  plans-into-repo + compound-FP analysis (both blocked on off-machine import).

---

## 6. Wave-3 module spec — `scope-lease` (flagship) + `sink-resolver`

The concrete realization of the §4 decision. Both sourced from the vault `work-registry.py`,
decoupled from its EA-OS `start`/work-record glue. House format each: standalone dir with
README + script + SCHEMA + CONFIG + examples + tests, env-configured.

### `scope-lease` — git-native exclusive claim over a plan's paths (Concurrency flagship)

**Thesis.** plan-gate enumerates the paths a unit of work *may* touch; `scope-lease` makes
that hold **exclusive** across concurrent agents. A git-native, zero-service lock so N
autonomous agents never edit the same source file at once — closing the two failures human
supervision used to cover: **deadlock-on-crash** and **stale-holder-write**.

**Composition (the product story).** It reads `allowed_paths` from the **same plan-gate plan
file** plan-gate already parses, and claims one lock per path. Adopters already running
plan-gate get the lease against their existing plans for free. plan-gate says "only these
paths"; scope-lease adds "…and you hold them exclusively." Enumerated → enumerated **+
exclusive.** This is the single strongest framing in the harvest: the lease is the multi-agent
extension of the flagship, not a standalone curiosity.

**Mechanism (verbatim from vault Feature C — pure git, no daemon).**
- One ref per claimed path: `refs/locks/<sha1(repo-relative-path)>` → a blob
  `{plan_id, owner, token, deadline, host, git_dir}`. Refs live off every branch → never
  re-enter the merge path.
- `acquire`: normalize each `allowed_paths` entry to repo-relative (resolve vs
  `git rev-parse --show-toplevel`; collapse `.`/`..`; forward-slash; dedupe) → build one lease
  blob → **transactional** `git update-ref --stdin` creating all path-refs CAS-from-absent
  (all-or-nothing) → expiry-reclaim past-deadline holders (two-signal: blob deadline + any
  heartbeat-file mtime) → **win**: stamp deadline; **lose**: structured DENY naming the
  conflicting path + holder + its deadline, exit ≠0, write nothing.
- `fence-check` (at the merge gate): each path's ref must exist and `blob.plan_id == this
  plan's id`; missing or foreign (→ reclaimed away) → **exit 4, write nothing**.
- `release`: delete only the refs this plan owns; **idempotent**.

**Decoupling work (vs the vault — this is the whole job).**

| Vault (EA-OS) | StrictLock `scope-lease` |
|---|---|
| `allowed_paths` from `work/<ULID>.md` record | from a **plan-gate plan file** (YAML frontmatter) |
| `record_id` = ULID | `plan_id` = the plan's id / stable slug |
| owner via `derive_owner` off work-branch | `SCOPE_LEASE_OWNER` env (default: git branch / `user.email`) |
| `lease` subcommands on `work-registry.py` | standalone `scope-lease.py` (acquire / fence-check / release) |
| `start` integration calls acquire | adopter calls `acquire` at session start (any harness) |

**Safety invariants (binding — keep from vault).** `refs/locks/*` coordinates **agents only**;
it must **never** wall a human's manual merge or edits (a `worktree_bypass` equivalent;
`fence-check` exit-4 governs the agent-merge path only). **Fail-loud single-machine boundary:**
`acquire` stamps `host`/`git_dir`; invoked from a non-shared ref store it **fails loud** rather
than silently degrading exclusion (cross-machine push-CAS-to-origin is a later phase).
**Surface, don't auto-reap:** reclaim is logged, never a background reaper.

**Config (env).** `SCOPE_LEASE=on` · `SCOPE_LEASE_PLANS_DIR` (reuse `PLAN_GATE_PLANS_DIR`) ·
`SCOPE_LEASE_TTL` (deadline) · `SCOPE_LEASE_OWNER` · `SCOPE_LEASE_LOG_DIR` (mirror
`PLAN_GATE_LOG_DIR`).

**Acceptance tests (port vault (a)–(h)).** overlapping scopes → exactly one HELD, the other a
structured DENY · back-dated deadline reclaimable with `token+1` · reclaimed-away →
`fence-check` exit 4 · `release` idempotent · **normalization crux**: two spellings of the same
file (absolute-vs-relative, dir-prefix-vs-file) collide on one key · txn atomicity (no
half-claim orphan ref) · GC race (ref-pin the blob before any prune window). Plus the vault's
post-build fix: an FS-aware key fold gated on git's `core.ignorecase`/`core.precomposeunicode`
(closes the case-insensitive/unicode-FS double-grant).

**Effort: medium.** The mechanism is built + 160-test-proven in the vault; the work is the
retarget (plan-file source, env owner, standalone script), re-porting the test suite, and the
README/SCHEMA/CONFIG. No new research — the hard design (9-agent panel + skeptic) is done.

### `sink-resolver` — deterministic auto-resolution of generated-file merge conflicts (secondary)

**What.** `merge=binary` on the generated sink(s) + a `post-merge` hook that regenerates + a
`resolve` subcommand for the conflicted case (regenerate from the merged sources, byte-check,
escalate on any non-sink conflict) + a CI `check` job (the web-UI-merge backstop).

**Loud precondition (on the tin, not a footnote).** *Only sound if your sink is a pure,
deterministic, `--check`-able render of its sources.* Without that, `--ours`/regenerate
silently clobbers peer edits — the one failure that makes this dangerous if mis-adopted.

**Two placement variants.** Default = the vault's git-native shape (local merges, ~47-line
`resolve`). Documented alternative = HIPAAPath's pre-merge worktree script
(`conductor-resolve.py`, with circuit breakers) for adopters who merge via a web UI where
`merge=binary`/hooks are inert.

**Config (env).** `SINK_RESOLVER_GENERATOR_CMD` (the regenerate + `--check` command) ·
`SINK_RESOLVER_SINKS` (paths placed under `merge=binary`).

**Sequencing.** Ship `scope-lease` first — it has no preconditions and tells the flagship-
extension story. `sink-resolver` follows, behind its precondition; it pairs naturally with the
Wave-1 `externalized-memory` projection bundle, which *is* the kind of pure renderer it requires.

---

## 7. Marketing (followup) — per-module + cross-posts, planned-later

Decision: **per-module articles** (continuing the existing downsmullen.com pattern —
`plan-gate.html`, `externalized-memory.html`, `multi-agent-handoff-protocol.html`) **+
LinkedIn cross-posts.** Each harvested module gets a marketing slot below as a **labeled
stub** — content gets planned when the module ships, not now.

### The house style (profiled from the existing articles — reuse verbatim)
- **Voice:** direct, engineering-first, grounded in scar tissue. "I learned the hard way."
  Systems-engineering metaphors (fail-open vs fail-closed, audit trail as exhaust). Every
  sentence carries weight; no marketing language.
- **Per-article skeleton:** type label (Field Report / Case Study) → provocative H1 → stakes
  → 2–4 named failure modes (each with concrete impact) → optional prior-art detour → the
  mechanism → honest "what it prevents vs detects vs can't address" → 3–4 concrete takeaways
  → author bio. Sidebar "Article Details" box (Type, Date, Topics, Key Idea, Architecture,
  Reference Implementation link). 2–3 load-bearing blockquotes. ~2,500 words.
- **LinkedIn cross-post:** distill to a 1–2 sentence reframe ("Prose asks an agent to
  behave. Structural gates make it."), the scar that surprised you, one concrete move, link
  to the full article.

### Marketing stubs (plan each when its module ships)
- [ ] **Cleanup-Day / rule-archaeology** — _marketing planned here later._ (Angle: "Audit
      the auditors — are your agent-control rules still earning their keep?" — the marquee.)
- [ ] **scope-lease (git-ref collision lock)** — _planned later._ (Angle: the multi-agent
      extension of plan-gate — enumerated *and* exclusive; a zero-service git-native lock for
      autonomous agent fleets. The marquee concurrency piece.)
- [ ] **sink-resolver (binary-sink auto-resolver)** — _planned later._ (Angle: deterministic
      merge-conflict resolution that's safe because the generator is pure.)
- [ ] **Blast-radius review-depth trigger** — _planned later._ (Angle: replace reviewer
      judgment-drift with objective scoring.)
- [ ] **Generated-sink integrity gates** — _planned later._ (Angle: layered fail-closed
      gates so stale generated artifacts never ship.)
- [ ] **Smoke-only assertion detector** — _planned later._ (Angle: catch false-safety-net
      tests before they ship.)
- [ ] **externalized-memory projection bundle** — _planned later._ (Angle: byte-deterministic,
      git-free status projection for concurrent agents.)
- [ ] **Compliance-mapping** — _planned later._ (Angle: compliance by construction — gate
      mechanisms mapped to SOC 2 / ISO.)
- [ ] **ROI / carrying-cost + harvest ritual (governance narrative)** — _planned later._
      (Angle: keeping a governance system from out-massing what it ships.)

---

## 8. Notes, caveats, dependencies

- **Agent disagreements (reconciled above):** generated-sink gates were rated both
  "harvest-now/low-effort" (the deep-read cluster) and "exploring/high-coupling" (a
  shallower pass) — taken as **near-ready with a coupling caveat** (needs a generator
  command + worktree-root resolution mirroring plan-gate). Cleanup-Day was rated both
  "harvest-now" and "ship-as-design-not-code" — taken as **ship both a genericized module
  and a paper**, since the novelty is the pattern more than the script.
- **The `*-hipaapath` skills are not on this Mac** — start/close/harvest/cleanup-hipaapath
  lived in the old Windows `~/.claude/skills/` and didn't migrate. Their *design intent*
  survives in HIPAAPath's CLAUDE.md; the ritual harvest reconstructs from that, it doesn't
  copy skill bodies.
- **Off-machine imports** block two doc items (plans-into-repo worked examples; read-only
  compound false-positive analysis) — both need artifacts (plan files; runtime deny log)
  from the original dev workstation. Already tracked as `pending import` in `BACKLOG.md`.
- **Extraction pattern (house standard):** every harvested module = standalone dir with
  README + script + CONFIG/SCHEMA + examples + tests, configured entirely by env vars, no
  machine-specific defaults — matching the four shipped modules.

---

_This plan is the durable artifact for the harvest. The §4 vault-overlap decision is
**resolved** (source the concurrency primitives from the vault; §6 holds the module spec).
Next: wire these waves into `BACKLOG.md` + set `STATE.md`'s Next Action._
