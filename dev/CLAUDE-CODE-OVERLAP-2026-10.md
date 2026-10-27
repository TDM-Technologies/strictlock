# StrictLock vs Claude Code out of the box (2026-10-27)

A snapshot comparison, re-arguable. Claude Code facts come from its docs and CHANGELOG as read on
2026-10-27 (latest release 2.1.311; the maintainer's install was 2.1.280). Checked directly on
2026-10-27: `onFailure` shipped in 2.1.295 (CHANGELOG); the 200-line / 25KB MEMORY.md load limit and
the write-still-succeeds behaviour (memory docs); sandbox scope, platforms and settings (sandboxing
docs). Also tested: Claude Code 2.1.280 accepts a hook entry carrying `onFailure` and still runs
the hook, so adding the field does not break older installs.

## Verdict per module

| Module | What Claude Code now does natively | Verdict |
|---|---|---|
| plan-gate | Permission rules (`Bash(prefix *)`, `Edit(path)`), compound-command splitting, `dontAsk` mode, an auto-mode classifier, plan mode. But the docs themselves say a Bash deny rule "isn't a security boundary", plan mode scopes nothing after approval, and teammates' plans are auto-approved. | **Still needed.** Nothing native does per-unit-of-work, exact-path, deterministic, human-approved scoping. |
| plan-gate decision log | OpenTelemetry `tool_decision` events (accept/reject, source, mode). No hash chain, does not name the rule, content redacted by default. | **Still needed** for tamper-evident evidence. OTel is a useful second source. |
| plan-gate "hook that can't start doesn't gate" caveat | `"onFailure": "block"` on a hook (v2.1.295+) makes a missing interpreter or crash fail closed. | **Caveat now fixable.** Adopt in examples and docs. |
| plan-gate Bash write-target parsing (cp/mv/tee/sed/dd) | Bash sandbox (macOS Seatbelt, Linux bubblewrap) confines Bash writes to the working dir + temp at OS level. Off by default; no native Windows. Directory-level, not file-level. | **Complementary.** The sandbox closes the gaps plan-gate admits (scripts, build tools, hardlinks). |
| scope-lease | Nothing at path level. Agent-teams docs say two teammates editing one file overwrite each other; split by ownership manually. | **Still needed.** |
| liveness-scan | Claude Code places a `git worktree lock` on running agents' worktrees and sweeps stale ones. No report. | **Still useful.** The lock could become a free liveness signal. |
| memory-cap | MEMORY.md loads only the first 200 lines / 25KB; Claude gets a reminder near the limit. The write still succeeds; entries past the limit are silently not loaded. | **Partly overlapped.** The per-entry cap is still unique; the total caps should match the native limits. |
| externalized-memory | Auto memory is per-user and per-machine, not in the repo. | **Still needed** (shared, versioned, atomic). |
| spill-gate, commit-msg-gate, generated-sink gates, test-protection-guard, eslint plugin | Nothing native: no secret scanning, commit checks, generated-file checks or test-tamper detection. | **Still needed.** These are git-side and harness-independent. |
| rituals | Only SessionStart/End/PreCompact hooks. | **Still needed** (prose by design). |

## Efficiency options

1. **Built 2026-10-27 (uncommitted at time of writing):** `onFailure: "block"` in
   `plan-gate/examples/settings.json`; README, plan-gate README, paper and memory-cap install
   text updated. Left out of `test-protection-guard/examples/settings.json` on purpose: that
   guard is advisory ("always allows"), and blocking on its failure would contradict that.
2. **Built 2026-10-27:** sandbox recommended as the layer under plan-gate (README "What is and
   isn't enforced today", plan-gate README "Pair it with Claude Code's sandbox"). The cp/sed/dd
   parser is unchanged; the direction is to stop growing it.
3. **Built 2026-10-27:** memory-cap whole-file caps `MEMORY_CAP_MAX_LINES` (default 200) and
   `MEMORY_CAP_MAX_BYTES` (default 25000), on by default, no-wedge, `off` to disable; 6 new tests.
   Behaviour change for existing users: an index over 200 lines can no longer grow.
4. **liveness-scan reads Claude Code's worktree lock** as a running signal, alongside the
   heartbeat (needs the lock-reason format checked on a live install).
5. **COMPLIANCE.md:** name OTel `tool_decision` as a complementary, non-tamper-evident source,
   and auto mode as probabilistic in contrast to plan-gate's deterministic check.
6. **Paper/README positioning:** quote the native docs' own "not a security boundary" line and
   the teammates-auto-approve-plans behaviour. Both are strong third-party support for the thesis.

Runtime cost is not a target: plan-gate measured ~45 ms per tool call (bare Python start ~17 ms).
