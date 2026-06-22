# StrictLock — backlog

Unfinished work and open design questions, one entry each. The forward-looking
*module* roadmap is in [`../roadmap.md`](../roadmap.md); this is the finer-grained
"what's left and why." Each entry is marked:

- **Ready** — scoped; could be picked up now.
- **Exploring** — a design direction under consideration; discuss before building.
- **Pending import** — blocked on an artifact that lives off-machine (see
  [`STATE.md`](STATE.md) → Constraints).

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

### authoring & discipline guide — *ready*
`SCHEMA.md` (frontmatter) and `CONFIG.md` (env vars) exist, but there's no single guide
to the authoring *discipline*: the `allowed_paths` / `allowed_commands` rules of thumb
(exact match, no globs; directory entries authorize nothing; narrow destructive-capable
prefixes so a bare `git checkout` entry can't also authorize `git checkout -- .`),
`worktree_bypass` semantics, the active-plan rule, and the lifecycle (flip `status` to
`executed` as the *last* gated action, or the gate stops authorizing the plan's own
close-out). A project-agnostic "plan-authoring" standard already exists privately and
can be genericized into the repo (likely `plan-gate/AUTHORING.md`).

### reversibility-tiered authorization — *exploring*
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

### changed-region scan — *ready*
The shipped cap scans the whole file, which can **self-wedge**: once any over-cap `- `
line exists, every later compliant write that doesn't also delete it is denied — so the
index can then only be written through non-enforcing paths, and violations accumulate.
Fix (validated in a private build): scan only the `- ` lines a write *introduces or
modifies* — the delta between the write's result and the file's pre-existing set
(rstrip-normalized multiset) — never pre-existing untouched lines. Preserves the good
property (no new over-cap entry) and removes the wedge. Optionally add an opt-in JSONL
decision log, env-gated (mirroring `PLAN_GATE_LOG_DIR`).

## cross-cutting

### auto-commit / drift automation — *exploring*
A `PostToolUse` hook that commits trivially-reversible (Tier 0) writes immediately, so
working-tree state stays durable and auditable instead of accruing uncommitted drift.
Enabling prerequisite for the Tier-0 "auditable via git" guarantee above. Open
questions: which paths qualify, the message convention, and the interaction with
`commit-msg-gate`. Discuss and sequence before building.

---

## Done
- **2026-06-22** — dev scaffold stood up (`dev/` — STATE + BACKLOG, dogfooding `externalized-memory`).
- **2026-06-01** — v1: plan-gate, commit-msg-gate, memory-cap, externalized-memory shipped.
