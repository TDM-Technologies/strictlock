# StrictLock — decision record (authorship & design provenance)

This is the ordered, dated log of the **design and architecture decisions** that shape
StrictLock. Its purpose is twofold:

1. **Engineering provenance** — why the suite is shaped the way it is, what was considered,
   and what was rejected, so a future session (or maintainer) can re-derive intent.
2. **Authorship evidence** — StrictLock is built with AI assistance. Under US Copyright
   Office guidance, an AI-assisted work is protectable to the extent of the **human's**
   creative contribution: the selection, arrangement, and modification a human directs. Each
   entry below names the human decider (Tim Downs Mullen / TDM Technologies LLC), the options
   weighed, the decision, and the rationale — i.e. the creative/expressive choices that a
   human authored. The AI implements under that direction.

> Not legal advice. The copyrightability of AI-assisted work is evolving; treat this log as
> contemporaneous evidence of human-directed design, and confirm the IP strategy with counsel.

**Conventions.** Entries are numbered `D-NNN`, chronological (most recent last). "Decided by"
is the human author of record. "Implementation" notes who carried out the mechanical build
under that direction. Entries D-001…D-007 below the dual-mode line were **reconstructed** from
contemporaneous records (`STATE.md` `## Decisions`, `HARVEST-PLAN.md`, and the project memory)
when this log was established on 2026-06-26 — their dates are as recorded in those sources, not
invented here. Every decision from D-008 forward is recorded at the time it is made.

---

## D-001 · Dogfood `externalized-memory`; keep dev-tracking public — 2026-06-22
- **Decided by:** Tim Downs Mullen (TDM Technologies LLC)
- **Context:** StrictLock needed a way to track its own multi-session development.
- **Options considered:** a private/gitignored dev log; an external tracker; **dogfooding the
  product's own `externalized-memory` pattern in-repo, public.**
- **Decision:** track development in-repo, **public** (`dev/` files), using the shared-blackboard
  pattern StrictLock itself ships. `dev/` stays public-safe — no machine paths, usernames, or
  unrelated project internals.
- **Rationale:** the suite should run on its own primitives (credibility), and a public,
  dated trail doubles as priority/provenance evidence.

## D-002 · Adopt the harvest plan — 2026-06-24
- **Decided by:** Tim Downs Mullen
- **Context:** a body of "old-process" agent-governance machinery existed inside HIPAAPath and
  the vault; the question was what, if anything, to extract into the public StrictLock suite.
- **Options considered:** ship nothing / port wholesale / **score each mechanism on two axes
  (dev-usefulness, street-cred) and sequence the winners across waves 0–4.**
- **Decision:** adopt the two-axis-scored, waved harvest plan (`HARVEST-PLAN.md`); **decompose**
  Conductor into its primitives rather than harvesting it whole.
- **Rationale:** lead with cred (marquee IP) and stock the shelves with workhorses; only lift
  pieces a stranger could actually adopt.

## D-003 · Source the concurrency primitives from the vault; scope-lease = Option A — 2026-06-24
- **Decided by:** Tim Downs Mullen
- **Context:** both HIPAAPath and the vault held lease/resolver implementations; they were not
  the same idea twice.
- **Options considered:** harvest the HIPAAPath advisory-mtime versions; harvest the vault's
  git-native CAS versions; for the lease's input source — hard-couple to plan-gate (B) vs. a
  **decoupled path-source primitive with plan-gate as the default adapter (A).**
- **Decision:** source from the **vault** (git-native, 160-test-proven); ship `scope-lease` as
  **Option A** — a standalone path+lock-id primitive, plan-gate plan as the headline default
  adapter, `--paths`/stdin/own-frontmatter as standalone fallbacks.
- **Rationale:** preserves the suite's "every module standalone" promise *and* the
  flagship-extension story (enumerated → enumerated **+ exclusive**); the AI-agnostic layer is
  the pure-git primitive once lifted off the EA-OS glue.

## D-004 · The smoke-only rule ships as a standalone npm package — 2026-06-25
- **Decided by:** Tim Downs Mullen
- **Context:** the `no-smoke-only-assertions` ESLint rule was the one JS member of an otherwise
  Python+shell, zero-dependency suite.
- **Options considered:** fold the JS toolchain into the main repo; **ship the rule as its own
  package** cross-linked as a Gates-family sibling.
- **Decision:** ship `eslint-plugin-strictlock` as a standalone package; published unscoped-public
  under the personal `downsmullen` npm account (transferable to an org later).
- **Rationale:** keeps the rest of the repo dependency-free; the rule still reads as a suite member.

## D-005 · Auto-merge mechanical modules on a clean adversarial verify — 2026-06-25
- **Decided by:** Tim Downs Mullen
- **Context:** the build→verify harness needed a landing policy.
- **Options considered:** every module on explicit human merge; **auto-merge mechanical modules
  once an independent adversarial-verify pass is clean**, marquee items on explicit merge.
- **Decision:** mechanical modules auto-merge on a clean verify; verify agents are **read-only**
  (agents never merge); the fail-closed guard discipline lands them; nothing is pushed without a nod.
- **Rationale:** the verify pilot proved it catches real defects rather than rubber-stamping
  (it found a data-loss bug in sink-resolver), so it can carry the mechanical merges.

## D-006 · Defer the marquee (Wave 2) to the end of the queue — 2026-06-25
- **Decided by:** Tim Downs Mullen
- **Context:** the salvage deadline was neutralized (a tag captured every harvest source), so
  run order became a free call.
- **Options considered:** keep numeric wave order (2 before 3/4); **reorder to ship adoptable
  primitives first and run the marquee last.**
- **Decision:** execution order **Wave 3 → Wave 4 → Wave 2**; wave numbers unchanged.
- **Rationale:** ship the portfolio/workhorse pieces first; the marquee stays the highest-cred
  work, just sequenced last by choice.

## D-007 · `test-protection-guard` ships **dual-mode** — 2026-06-26
- **Decided by:** Tim Downs Mullen
- **Context:** Wave 4's one real code gate. The HIPAAPath original is a Claude Code `PreToolUse`
  advisory hook that catches an agent rewriting a correctly-failing test to pass (co-commit
  coupling). Every other Gates member that touches commits ships as a universal git hook.
- **Options considered:**
  - **Faithful `PreToolUse`-only** — exact port; agentic-harness only; would be the lone Gates
    member requiring Claude Code; can never become a CI check.
  - **git `pre-commit`-only** — universal reach, matches sibling gates; drops the
    agent-tool-boundary framing and the inline-to-agent advisory.
  - **Dual-mode (chosen)** — one script, one shared coupling-analysis core, two interchangeable
    I/O shells: a `PreToolUse` door (JSON in → `allow` JSON + inline warning) and a git
    `pre-commit` door (in-tree → stderr warning + exit 0), auto-detected by whether stdin
    carries a tool-call payload, with an env override to force the mode.
- **Decision:** **dual-mode.**
- **Rationale:** faithful (keeps the exact `PreToolUse` behaviour and the inline agent nudge),
  universal (the git door reaches any committer/harness and matches the sibling gates' install
  story), durable (the contract-stable git half backstops the Claude-Code-coupled half), best
  cred (depth *and* breadth — "I run this against my own agents *and* it drops into any repo"),
  and the only path that extends naturally to a CI check on the diff — the real teeth, since the
  guard stays advisory (always allows) in both modes.
- **Implementation:** built by the AI assistant under this direction; genericized from the
  HIPAAPath original (git-resolved repo root; own `TEST_PROTECTION_GUARD` switch; env-configured
  test/source globs + assertion pattern; house `_LOG_DIR`), then independently adversarially
  verified before landing.
