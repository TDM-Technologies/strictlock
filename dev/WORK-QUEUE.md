# StrictLock — open-work queue (inventory 2026-10-16)

Every open item, checked against `main` @ 81cad04 on 2026-10-16. Sources: the claims-fix spec
(on branch `claude/nifty-germain-0b159b`, not yet on main), `dev/BACKLOG.md`, `dev/STATE.md`,
and the vault portfolio card. Draft for Tim's review — order and grouping are re-arguable.

**Status 2026-10-16:** Stage 1 (items 1–3 below) is carried by PR 1 on branch
`docs/claims-truth-reword` (in review): the claims-fix spec now lives at `dev/CLAIMS-FIX-SPEC.md`
with a per-claim ledger (§7) that Stage 2 uses to restore the strong wording as code lands.

## Stage 1 — make the public repo read true (no code; all decisions already made 2026-07-07)
1. **Land the claims-fix spec on main** — it exists only on an unmerged branch.
2. **Reword pass** — ~15 public sentences that still say more than the code does, e.g.
   "structurally impossible" (sink gate), "blocks *every* tool call" (COMPLIANCE.md),
   "self-correct *this turn*" (test-protection-guard), "exact commands" (plan-gate),
   "single-use" bypasses. Plus stale STATE/BACKLOG figures.
3. **Housekeeping** — STATE.md meta (still points at a July commit), BACKLOG done-moves, the
   two new backlog items the spec asks for (`allowed_tools:` and `worktree:` schema wave).

## Stage 2 — code that makes the stronger claims true (decided 2026-07-07)
| Item | What | Status on main |
|---|---|---|
| Fix A — decision log | hash-chained allow+deny log | **done** (PR #11) |
| Fix A — memory-cap mirror | same log for memory-cap | not built |
| Fix B — compound commands | `pytest && rm -rf /` currently passes if `pytest` is allowed; check every segment, treat `>` as a write | not built (only `cd …` compounds are checked) — the flagship security fix |
| Crash posture | plan-gate denies on its own crash, bypass still honored | not confirmed — verify in build |
| CI on 3 OSes | add macOS + Windows runners, fix what breaks | not built (all jobs ubuntu) |
| Nine small bugs | listed in the spec §3 C2 | mostly not built; verify each |
| Small features | approved-plan check for commit messages, memory-cap total size, liveness sample plan | not built |
| Prepush CI check / WP-age / worktree-guard message | spec §5 Q8a–c | not built |

## Stage 3 — Wave 4 remainder (three prose ports)
Session-ritual checklists · WP-interaction analysis · pre-WP currency check. Sources staged in
the maintainer's local staging import. Public repo → strip machine paths and HIPAAPath
internals on the way in.

## Not in the queue (and why)
- **Wave 2 — marquee IP** (Cleanup-Day, ROI governance, blast-radius): sequenced last by Tim
  2026-06-25; design-heavy.
- **Blocked on the Windows machine:** plans-into-repo, false-positive analysis.
- **Exploring, needs discussion first:** reversibility tiers, auto-commit, per-project tiering.
- **Ready but unqueued:** secret / non-public spill gate.
- **Hygiene sweep correction:** the portfolio card says branch `claude/nifty-germain-0b159b`
  has zero commits — it holds the only copy of the claims-fix spec. Safe to reap only after
  Stage 1 item 1 lands.

## Standing rules for whoever runs this
- Merging to public main stays with Tim (flagship + public claims). Agents open PRs only.
- Build → adversarial verify → land, per piece. Fix B gets the heaviest verify (3 lenses).
- Builds happen in isolated worktrees; nothing touches the main checkout.
