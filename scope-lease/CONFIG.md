# scope-lease configuration

Everything is configured through **environment variables** — no machine-specific defaults are
baked into the script.

`scope-lease` is invoked **explicitly** by an adopter (at session start, at the merge gate, at
close-out), so unlike a `PreToolUse` gate it is *not* inert-by-default. `SCOPE_LEASE` is an
opt-out switch for scripts that want one toggle: set it to a falsey value to make every verb a
no-op. An explicit call with `SCOPE_LEASE` unset still runs.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `SCOPE_LEASE` | no | Set to `on` / `1` / `true` / `yes` to run (the default when unset). Any *other* value (e.g. `off`) makes every verb a no-op — nothing is claimed, checked, or released. |
| `SCOPE_LEASE_SOURCE` | no | Path source: `plan-gate` (default), `paths`, or `file`. See [SCHEMA.md](SCHEMA.md). |
| `SCOPE_LEASE_PLANS_DIR` | for `plan-gate` | Directory holding plan files; the single `status: active` plan supplies the path set + lock id. Falls back to `PLAN_GATE_PLANS_DIR` so a plan-gate adopter needs no extra config. |
| `SCOPE_LEASE_PATHS` | for `paths` | `os.pathsep`- or whitespace-separated path set for `--source paths` (when `--paths` and stdin are not used). |
| `SCOPE_LEASE_PATHS_FILE` | for `file` | An own-frontmatter file (`--source file`) whose `allowed_paths` is the path set and whose `name` is the lock id. |
| `SCOPE_LEASE_LOCK_ID` | for `paths` | The lock id for `--source paths` (the `plan-gate` / `file` sources derive it from the plan `name`). Overrides a derived id when set. The identity that holds the scope — keep it **stable** across acquire / fence-check / release. |
| `SCOPE_LEASE_TTL` | no | Lease lifetime in **seconds** (the deadline = now + TTL). Default `1800` (30 min): long enough to cover a work burst, short enough that a crashed holder's scope frees by deadline. `--ttl-seconds` overrides. |
| `SCOPE_LEASE_OWNER` | no | The owner label stamped in the blob. Resolution order: `SCOPE_LEASE_OWNER` → the worktree's git branch → `user.email`. Errors only if all three are unavailable (an unresolvable owner is routed back to you, never guessed). `--owner` overrides. |
| `SCOPE_LEASE_LOG_DIR` | no | Directory for the append-only decision log (`scope-lease.log`). Each acquire/release appends a JSON line (UTC timestamp, lock id, owner, leased/reclaimed paths). If unset, no log is written. This is your concurrency audit trail. |

`os.pathsep` is `;` on Windows and `:` on Linux/macOS — it applies to `SCOPE_LEASE_PATHS`.

## Per-OS examples

### Linux / macOS (bash/zsh) — the plan-gate adapter

```bash
export SCOPE_LEASE=on
export SCOPE_LEASE_PLANS_DIR="$HOME/.agent/plans"   # or reuse PLAN_GATE_PLANS_DIR
export SCOPE_LEASE_TTL=1800
export SCOPE_LEASE_LOG_DIR="$HOME/.agent/logs"

python3 scope-lease.py acquire        # claim the active plan's allowed_paths
python3 scope-lease.py fence-check     # at the merge gate: exit 0 holds, exit 4 lost
python3 scope-lease.py release         # at close-out
```

### Linux / macOS — standalone (no plan-gate)

```bash
export SCOPE_LEASE=on
export SCOPE_LEASE_SOURCE=paths
export SCOPE_LEASE_LOCK_ID=session-7
export SCOPE_LEASE_OWNER=ci-runner-7
export SCOPE_LEASE_PATHS="src/app.py:src/util.py"

python3 scope-lease.py acquire
```

### Windows (PowerShell)

```powershell
$env:SCOPE_LEASE = "on"
$env:SCOPE_LEASE_PLANS_DIR = "$env:USERPROFILE\.agent\plans"
$env:SCOPE_LEASE_LOG_DIR = "$env:USERPROFILE\.agent\logs"
python3 scope-lease.py acquire
```

## Exit codes

The contract a calling script branches on:

| Exit | Meaning |
|---|---|
| `0` | OK — acquired / fence holds / released (also: disabled no-op, empty path set). |
| `1` | Unexpected error — a git failure, an undeterminable owner, or an unresolvable path source. |
| `2` | Usage error (argparse). |
| `4` | `fence-check` **lost** — a held scope was reclaimed away. "A guard tripped: write nothing." |
| `5` | `acquire` **DENY** — a scope is held by a live holder. First-mover wins; nothing was written. |
| `6` | **Machine-boundary fail-loud** — a lock from a non-shared ref store. Cross-machine exclusion is not real here; refusing rather than degrading silently. |

## Reclaim is surfaced, never silent

When `acquire` reclaims an expired holder's scope, it prints a `RECLAIMED` line to **stderr**
naming who was evicted, their old deadline, and the new token/deadline. There is no background
reaper — a scope only ever changes hands as a visible side effect of someone actively acquiring
it.

## Decision log

When `SCOPE_LEASE_LOG_DIR` is set, every acquire and release appends a JSON line to
`scope-lease.log` with a UTC timestamp, the event, the lock id, the owner, and the leased /
reclaimed / released paths. That append-only trail is the concurrency-side audit exhaust —
produced as a byproduct of the lock doing its job, the same posture as `plan-gate`'s decision
log. Logging is best-effort: a log-write failure never changes a lock outcome.
