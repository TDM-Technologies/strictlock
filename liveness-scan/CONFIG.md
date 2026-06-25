# liveness-scan configuration

Everything is configured through **environment variables** — and every one is **optional**, with
sensible defaults and no machine-specific paths baked in. With zero configuration, the scanner
enumerates the current repo's worktrees, classifies them on commit-mtime, and prints the report.

`liveness-scan` is a read-only reporter; there is no enable/disable switch — running it *is* the
opt-in, and it only ever writes gitignored logs (and only when `LIVENESS_SCAN_LOG_DIR` is set).

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LIVENESS_SCAN_PLANS_DIR` | (none) | Directory of plan-gate plans, used to attribute a worktree to its session via the plan whose absolute `allowed_paths` resolve under that worktree. Falls back to `PLAN_GATE_PLANS_DIR`. Unset → no plan attribution (graceful degrade to ahead-of-base / idle). |
| `LIVENESS_SCAN_BASE` | `origin/main` | The base ref the "done-unmerged" check measures against (`git rev-list --count <base>..<head>` > 0 ⇒ ahead ⇒ pending merge). Set to your trunk (e.g. `main`, `origin/develop`). |
| `LIVENESS_SCAN_HEARTBEAT_RELPATH` | (none) | A per-worktree file (relative to each worktree root) your harness touches to signal liveness. Read by **existence + mtime only**. Unset / absent → the scanner falls back to commit mtime. |
| `LIVENESS_SCAN_HEARTBEAT_TIMEOUT_MIN` | `10` | A heartbeat older than this (assuming a ~30s touch cadence) reads as "quiet" → `stalled`. The lease-fresh window. |
| `LIVENESS_SCAN_COMMIT_STALE_MIN` | `90` | The commit-mtime fallback window: no heartbeat + a commit older than this reads as `stalled`. Coarser than a heartbeat on purpose. |
| `LIVENESS_SCAN_CEILING_MIN` | `480` | The whole-unit-of-work ceiling (8h). An owned worktree idle past this — even on a fresh-ish signal — is `ambiguous` and **escalates**. Distinct from the heartbeat window. |
| `LIVENESS_SCAN_LOG_DIR` | (none) | Directory for the gitignored outputs: a human report (`liveness-escalations.md`, overwritten each run) and an append-only JSONL trail (`liveness-scan.log`). Unset → print to stdout only, write nothing. |

A non-integer or `<= 0` threshold value falls back to its default (announced on stderr) — a
misconfigured threshold never silently disables the scan.

## Modes

| Flag | Effect |
|---|---|
| (none) | Print the human report to stdout; write the gitignored logs if `LIVENESS_SCAN_LOG_DIR` is set. |
| `--dry-run` | Compute + print the report; **write nothing**. |
| `--json` | Emit the structured digest as JSON (tooling / tests). |
| `--explain` | Add a maintainer breakdown (thresholds + counts) to stderr. |
| `--root DIR` | A path inside the repo whose worktrees to scan (default: cwd). The repo top-level is resolved so the whole fleet is enumerated even from a subdir. |

## Exit code

**Always `0`.** A pure reporter must never break a scheduled run or a kickoff. Even an unhandled
internal error is surfaced to stderr and swallowed to a 0 exit (`--help` / a bad flag still exits
non-zero via argparse, as usual). Do not branch CI on this tool's exit code — read the report or
the JSON digest instead.

## Per-OS examples

### Linux / macOS (bash/zsh) — scheduled fleet digest

```bash
export LIVENESS_SCAN_PLANS_DIR="$HOME/.agent/plans"
export LIVENESS_SCAN_HEARTBEAT_RELPATH=".agent/heartbeat"
export LIVENESS_SCAN_LOG_DIR="$HOME/.agent/logs"
python3 liveness-scan.py            # cron / idle-tick this
```

### A heartbeat your harness touches (composition with scope-lease)

```bash
# at session start, alongside the lease acquire, and periodically thereafter:
mkdir -p .agent && : > .agent/heartbeat        # or `touch .agent/heartbeat`
```

### Windows (PowerShell)

```powershell
$env:LIVENESS_SCAN_BASE = "main"
python liveness-scan.py --json
```

## The gitignored logs

When `LIVENESS_SCAN_LOG_DIR` is set, each run (re)writes `liveness-escalations.md` (the current
human report) and appends one JSON line to `liveness-scan.log` (a timestamped digest: counts,
active-plan count, and the escalations). These are **exhaust** — keep them gitignored (`*.log`,
the report dir). They are the fleet-supervision audit trail, produced as a byproduct of the scan.
