# Authorizations record — unified plan-gate decision log (spec)

**Status:** ruled + spec'd 2026-07-15 (maintainer's calls recorded below) · build pending
**Owner module:** `plan-gate/`
**Why:** `paper.md` §5–§6 publicly claim an append-only, tamper-evident log of *every* gate
decision (allow and deny). The shipped code logs denials on every surface but allows only for
PowerShell commands — file-write allows, Bash-command allows, and the emergency bypass leave no
trace. This build makes the claim true instead of rewording it down.

## Ruled decisions (maintainer, 2026-07-15)

1. **One unified log, replacing the three per-surface files.** A single
   `plan-gate-decisions.log` (JSONL) carries every decision with a `surface` field.
   `plan-gate-denies.log`, `powershell-calls.log`, and `mcp-file-write-calls.log` are removed.
   Logs are git-ignored exhaust at v0.x; no compatibility shim.
2. **Hash-chained + self-protecting.** Every row carries `prev` (previous row's hash) and `h`
   (its own). The gate unconditionally denies gated file-writes targeting the log directory,
   even when a plan enumerates it — a gated agent can never be *authorized* to touch its own
   audit trail.
3. **Full record by default.** `PLAN_GATE_LOG_DIR` set ⇒ allow *and* deny rows. Opt-down:
   `PLAN_GATE_LOG_DECISIONS=deny` records denials + emergency-bypass rows only.

## Row schema (JSONL, one object per decision)

| field | content |
|---|---|
| `ts` | UTC `YYYY-MM-DDTHH:MM:SSZ` (existing convention) |
| `decision` | `allow` \| `deny` |
| `surface` | `file` \| `bash` \| `ps` \| `mcp` \| `gate` (gate-level events, e.g. emergency bypass) |
| `tool` | tool name as received (`Edit`, `Bash`, MCP tool name, …); may be `""` for gate-level rows |
| `target` | file path for file-surface rows, else `""` |
| `command` | command text, truncated to 500 chars (existing convention), else `""` |
| `class` | decision class — see coverage map (e.g. `allowed_paths`, `allowed_command`, `read_only_bypass`, `always_writable`, `navigation`, `emergency_bypass`, `log_self_protect`, `denied`, `empty_input`, `no_active_plan`, `multiple_active_plans`, `silent_drift`, `disjoint_paths`) |
| `reason` | the human-readable reason string passed to `allow()`/`deny()` |
| `plan` | active plan filename or `""` (pre-plan-check rows) |
| `cwd` | process cwd, best-effort |
| `extra` | optional object — MCP field summary (existing `title`/`parentId`/… prefix-truncated form); absent otherwise |
| `prev` | hex sha256 of the previous row (`"0"*64` for the first row) |
| `h` | hex sha256 over `prev + "\n" + canonical_json(row_without_h)`, canonical = `json.dumps(sort_keys=True, separators=(",",":"))` |

**Privacy invariant (carried over):** never log file contents or full payloads — paths,
commands (truncated), and short field prefixes only.

## Coverage map — every decision site in `plan-gate.py` main()

Logged (with class):
- Emergency bypass `PLAN_GATE_BYPASS=on` → `allow` / `gate` / `emergency_bypass`. Fires before
  stdin parse; a best-effort payload parse (inside try/except) enriches the row with
  tool/target when possible, but parse failure NEVER blocks the bypass (fail-open preserved).
  Logged even in `deny` opt-down mode — bypass usage is exception-class evidence.
- Always-writable dir allow (file surface) → `always_writable`.
- Read-only Bash bypass, `git -C` read-only form, bare `cd` → `read_only_bypass` / `navigation`.
- Read-only PS bypass → `read_only_bypass` (class name unified; was `read_only`).
- `allowed_paths` exact match (file) → `allowed_paths` — the flagship allow, currently unlogged.
- `allowed_command` prefix match (bash, ps, cd-compound) → `allowed_command`.
- **NEW deny — log self-protection:** file-surface target under `PLAN_GATE_LOG_DIR` →
  `deny` / `log_self_protect`. Runs BEFORE the always-writable bypass and before
  `allowed_paths` matching, so neither can shadow it.
- Every existing deny (`gated_deny` sites): no-active-plan, multiple-active, silent-drift,
  disjoint-paths, not-in-allowed-paths, command-not-allowed, MCP default-deny, MCP
  empty-input, empty-command — each mapped to a stable `class`.

Deliberately NOT logged (documented limitations, mirrored in paper wording):
- `PLAN_GATE != on` (gate disabled): the gate is inert; no witnessing claim while off.
- Tool outside the gated set: a pass-through, not an authorization decision; logging it would
  add a row per Read/Glob/etc. MCP-evasion coverage comes from configuring
  `PLAN_GATE_MCP_WRITE_TOOLS`, whose members ARE gated and logged.

## Mechanics

- Loggers `_log_deny` / `_log_ps_call` / `_log_mcp_file_write_call` collapse into one
  `_log_decision(...)`; `gated_deny` keeps its shape; a sibling `gated_allow(class_, reason,
  **ctx)` closure wraps the raw `allow()` calls inside `main()`. Both remain **fail-open**: a
  logging failure never changes a gate decision (existing invariant, now tested for allows).
- **Append protocol:** open `O_APPEND`; take an exclusive advisory lock (`fcntl.flock` on
  POSIX, `msvcrt.locking` on Windows, both best-effort) around read-tail → compute `prev` →
  write row + newline as a single write. Concurrent sessions share one log safely.
- **Tail recovery:** if the last line is unparseable (partial write/corruption), `prev` =
  sha256 of the raw last-line bytes — the chain continues verifiably past the damage and the
  verifier pinpoints the damaged row.
- **Verifier:** `plan-gate.py verify-log [path]` (argv mode; hook mode stays stdin/no-args).
  Exit 0 = chain intact; exit 2 + first-bad-row report otherwise. Zero-dep, stdlib only.

## Honest tamper-evidence scope (paper must say this, not more)

The chain makes **edits and deletions within the record detectable** by anyone re-verifying
it. It is integrity-without-secret: it does NOT stop a capable adversary with filesystem
access from truncating the tail or re-forging the whole chain (no HMAC key, no external
anchor — v1 non-goals; an operator can anchor the latest `h` externally per session for a
stronger guarantee, documented as practice). What the gate itself guarantees: a **gated**
agent can never be authorized to write the log via the file surface (`log_self_protect`);
plans whose `allowed_commands` grant arbitrary shell writes remain governed by plan review —
documented blind spot, unchanged from today.

## Docs riding the same PR

- `paper.md` §5 exhaust paragraph + §6 CC7 row: reworded to exactly the shipped behavior
  (allow+deny on gated surfaces, hash-chained, self-protected, scope-honest tamper wording).
  **Maintainer reviews this wording at the PR — it is the public claim.**
- `plan-gate/CONFIG.md`: `PLAN_GATE_LOG_DECISIONS`, unified schema, verifier usage.
- `plan-gate/README.md` + root `README.md` + `COMPLIANCE.md` CC7 row: refreshed.
- Stale docstrings from the removed loggers deleted with them.

## Tests (added to `plan-gate/tests/test_plan_gate.py`)

Allow-row per class (paths/command/bypass/always-writable/navigation) · deny rows carry class ·
emergency-bypass row written and allow unblocked on unwritable log · opt-down mode (denies +
bypass only) · chain verifies clean · single-row edit breaks verify at the right line · tail
recovery past a corrupt last line · self-protect denies a plan-enumerated log path AND an
always-writable-shadowed log path · fail-open (unwritable LOG_DIR: decisions unchanged) ·
concurrent appends from two processes keep the chain intact · privacy (no content fields).

## Build + landing plan

Build solo on `feat/authorizations-record` (this worktree), then adversarial verify via ~3
read-only perspective-diverse agents (Wave-3-scale fan-out): (a) gate-semantics regression —
no decision changes except the new `log_self_protect` deny; (b) chain/lock/concurrency
correctness; (c) fail-open + privacy. Fix, land as a PR, CI green. **Merge is the
maintainer's** — this touches the flagship module and public paper claims (marquee tier, not
auto-merge). Out of scope: log rotation; `allowed_mcp_tools` opt-in (v2 note in code stands);
shell-level log-write detection.
