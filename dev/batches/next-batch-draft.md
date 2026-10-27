# Next batch — DRAFT (lanes queued, not planned or launched)

Lanes queued for the next StrictLock batch. When that batch is planned, its plan file takes these
rows, adds whatever else is due, and gets Tim's go as usual. Nothing here has a go yet.

## Lane: plan-gate deny-regression tests (queued 2026-10-27, Tim)

**Why.** The 2026-10-27 bypass hunt (lane D) never ran: a safety check stopped the agent before any
input was tried. Three input families have never been exercised against plan-gate, and the
2026-10-27 hardening added new parsing surface. Interest/challenge hook: this is the evidence the
paper's "refuses anything the plan does not list" claim most lacks.

**Framing.** Regression tests for plan-gate's deny behaviour, in StrictLock's own suite: each test
feeds the gate a crafted hook call (JSON on stdin, as the existing tests do) and asserts it is
refused or left undecided — never allowed. Nothing is executed against the real system.

| lane | what | touches | read-only? | size |
|---|---|---|---|---|
| E deny-regression tests | Tests for the families below; any call the gate wrongly admits becomes an `expectedFailure` test plus a written finding — the fix is a separate lane | `plan-gate/tests/test_plan_gate_adversarial.py` (new file only) | no | M |

Families, in priority order:
1. **git settings that run programs** — an allowed `git diff` / `git log` reaching an external diff
   or textconv driver, `core.pager`, `core.fsmonitor`, `core.hooksPath`, `alias.*`, filter drivers,
   `include.path`, `GIT_*` environment variables, `git -c` spellings.
2. **Unicode** — homoglyphs, fullwidth letters, zero-width and RTL-override characters, NFC vs NFD
   path spellings (macOS), Unicode whitespace as separators.
3. **Very long input** — huge commands and paths: the gate stays fast, no regex blow-up, and any
   limit it hits refuses rather than allows.
4. **2026-10-27 surfaces** — the cp/mv/install/tee/sed/dd destination parser (option spellings, GNU
   vs BSD sed) and the Windows "`..` after a link" rule.

**Rules for the lane.** Touches only its new test file (plan-gate.py is the busy file; a fix goes
to a separate lane after Tim sees the findings). CI must stay green: a confirmed gap lands as
`expectedFailure` with the finding in its docstring. Reviewer: Opus. Estimated cost ~0.6–1M tokens.

**If the safety check stops it again:** record "not run" again; do not reword the task to get past
the check. Tim's fallback: run it himself in his own session.
