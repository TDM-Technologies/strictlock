# Harvest Source Map — what's left in HIPAAPath, where, and is it safe

**Purpose:** one place to check *before* cleaning up HIPAAPath. Answers the only
question that matters when "shit happens": *is this thing already saved somewhere
durable, or would deleting it lose something expensive to rebuild?*

**Durability snapshot (2026-06-30):** every source file listed below is **tracked and
pushed** to HIPAAPath's own remote (`github.com/downsmullen/hipaapath`, `origin`
fully synced at the time of writing). That means git history retains all of it even
if a future cleanup deletes the files from the working tree — recover with
`git log --all -- <path>` / `git show`. The realistic loss modes are *deliberate*:
force-rewriting HIPAAPath history, or deleting the repo/remote outright. Ordinary
file cleanup is **recoverable**, not loss.

So the value of this file is **findability**, not backup: it tells future-you what
each leftover is and whether it still needs harvesting, so nothing gets either
needlessly resurrected or accidentally orphaned.

> Paths are HIPAAPath-relative. This file is public-safe: pointers + verdicts only,
> no machine paths, no HIPAAPath proprietary content.

---

## Legend
- **DONE** — already harvested into StrictLock and live; the HIPAAPath copy is now
  just historical. Safe to leave or delete; nothing lost either way.
- **TODO** — genuinely un-harvested; this is the source-of-truth until harvested.
  Don't lose track of it. (Recoverable from git history, but you'd have to *know* to look.)
- **KEEP-PRIVATE** — intentionally NOT going into the public product; too
  HIPAAPath-coupled to transfer. Stays in HIPAAPath; don't harvest, don't delete carelessly.
- **OFF-MACHINE** — source-of-truth is not on this Mac at all (Windows workstation);
  noted so you don't assume HIPAAPath holds it.

---

## TODO — un-harvested, still source-of-truth

### Wave 2 (marquee — deferred to last by choice; highest-cred work)
| Item | HIPAAPath source | Verdict |
|---|---|---|
| Cleanup-Day / rule-archaeology | `.claude/scripts/rule-archaeology-scan.py` · `.claude/scripts/_run_rule_archaeology_scan_tests.py` · `docs/design/cleanup-day-phase-d.md` · `docs/manifest/decisions/cleanup-day-phase-d-7b3f9e02.md` | harvest-now ★marquee — the reverse-audit ("do my own rules still earn their place"). |
| Blast-radius review-depth trigger | `.claude/scripts/blast-radius.py` · `.claude/scripts/_run_blast_radius_tests.py` · `docs/design/blast-radius-score.md` | planned — needs pattern-config genericization before it ports (currently HIPAAPath-pattern-coupled). |
| ROI / harvest governance narrative | `docs/manifest/sessions/wp-roi-gate-meta-layer-9b2c7d10.md` · `docs/manifest/ats/roi-gate-meta-layer-3a7f1e90.md` | harvest-now — becomes a `GOVERNANCE.md` narrative, not code. |

### Wave 4 (rituals + remaining gates — in progress)
| Item | HIPAAPath source | Verdict |
|---|---|---|
| test-protection co-commit guard | (already extracted) | **DONE** — landed in StrictLock `test-protection-guard/` (PR #7). |
| WP-interaction analysis | `docs/design/wp-interaction-analysis.md` (+ `docs/manifest/ats/wp-interaction-*`) | harvest-now — discipline/checklist, no code. |
| pre-WP currency check | `docs/design/pre-wp-currency-check.md` | planned — prose rubric. |
| session-ritual checklists (kickoff/close) | HIPAAPath `CLAUDE.md` sections **+ the `*-hipaapath` skills (OFF-MACHINE — see below)** | planned — partly reconstructable from CLAUDE.md. |

### Exploring (later — needs design/evidence first)
| Item | HIPAAPath source | Verdict |
|---|---|---|
| Escalation-briefing substrate | `.claude/scripts/conductor-escalate.py` | exploring — needs the deferred LLM "Layer 2" to tell a full story. |

---

## DONE — already harvested and live in StrictLock (HIPAAPath copy is historical)
| StrictLock module | Harvested from (HIPAAPath) |
|---|---|
| `liveness-scan/` | `.claude/scripts/conductor-scan.py` |
| `sink-resolver/` | `.claude/scripts/conductor-resolve.py` (documented as the web-UI pre-merge variant; git-native shape sourced from the vault) |
| `scope-lease/` | supersedes `.claude/scripts/conductor-lease.py` (advisory mtime hint; replaced by the vault's git-native CAS lease) |
| `generated-sink-commit-gate/`, `generated-sink-prepush-gate/` | HIPAAPath manifest-freshness machinery |
| `eslint-plugin-strictlock` | HIPAAPath `no-smoke-only-assertions` rule |

---

## KEEP-PRIVATE — stays in HIPAAPath, do NOT harvest to the public product
Too HIPAAPath-coupled to give a stranger any signal; copying them out would leak
project internals for no benefit. They are still real machinery — don't delete them
casually, just don't migrate them.
- `.claude/scripts/conductor-merge-gate.py` — only sound atop the bespoke smoke-check + blast-radius stack.
- `.claude/scripts/conductor-ci-context.py` — pure integration glue between two HIPAAPath systems.
- `.claude/scripts/conductor-render.py` — HIPAAPath-specific rendering.
- handoff-session-memo-lint · session-memo-presence kickoff step (live in HIPAAPath `CLAUDE.md` / scripts).

---

## OFF-MACHINE — not on this Mac; don't assume HIPAAPath holds these
- The `*-hipaapath` skills (`start-` / `close-` / `harvest-` / `cleanup-hipaapath`) —
  source-of-truth is the Windows dev workstation, not this repo. Partial reconstruction
  is possible from HIPAAPath `CLAUDE.md`.
- Historical plan files + the runtime deny log — Windows-only (see `BACKLOG.md`
  `plans-into-repo` / `false-positive-analysis`, both marked `pending import`).

---

## Bottom line for a HIPAAPath cleanup
- **DONE / KEEP-PRIVATE / OFF-MACHINE rows**: cleaning up around them loses nothing
  that isn't already safe elsewhere (or never lived here).
- **TODO rows**: these are the only HIPAAPath files where the working-tree copy is
  the convenient source-of-truth. They're still in git history if deleted, but if you
  want zero-friction recovery, harvest them (Wave 2 / Wave 4) *before* a big cleanup —
  or just keep this map handy so you know what to `git show` for.
