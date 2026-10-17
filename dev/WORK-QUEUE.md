# StrictLock — open-work queue (inventory 2026-10-16)

Every open item, checked against `main` @ 81cad04 on 2026-10-16. Sources: the claims-fix spec
(`dev/CLAIMS-FIX-SPEC.md`, previously only on an unmerged branch), `dev/BACKLOG.md`, `dev/STATE.md`,
and the maintainer's private project notes. Draft for Tim's review — order and grouping are re-arguable.

## Close-out (2026-10-16)
All three stages were run as one PR each; merging stayed with Tim.

| Stage | PR | Disposition |
|---|---|---|
| 1 — make the public repo read true (docs) | #15 | **merged** (`720a24e`) — spec on main at `dev/CLAIMS-FIX-SPEC.md`, per-claim ledger §7 |
| 2 — code that makes the stronger claims true | #17 | **merged** (`0e5746a`) — dispositions in CLAIMS-FIX-SPEC §8; CI 13/13 green on Windows/macOS/Ubuntu; two real Windows bugs fixed on the way |
| 3 — Wave 4 remainder (three prose ports) | #14 | **merged** (`1cd53ca`) — `rituals/`, with the Wave 4 dev-doc close-out |

Not built in Stage 2 (each kept its interim wording; tracked in BACKLOG "Open after Stage 2"): PowerShell
compound/redirect handling, scope-lease denied-acquire rows, sink-resolver no-conflict/dry-run rows,
memory-cap total caps for `NotebookEdit`, and a wider adversarial bypass-hunt pass. Design calls the
2026-07-07 decisions did not cover are listed in PR #17's description for Tim.

The tables below are the original 2026-10-16 inventory, kept as written.

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
| Crash posture | plan-gate denies on its own crash, bypass still honored | not built — confirmed fail-open today (exit 1, non-blocking); deny-on-crash is spec Q5 |
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
- **Hygiene note:** the unmerged branch that held the only copy of the claims-fix spec is now
  safe to reap: Stage 1 item 1 landed with PR #15 (reaping is Tim's call).

## Standing rules for whoever runs this
- Merging to public main stays with Tim (flagship + public claims). Agents open PRs only.
- Build → adversarial verify → land, per piece. Fix B gets the heaviest verify (3 lenses).
- Builds happen in isolated worktrees; nothing touches the main checkout.
