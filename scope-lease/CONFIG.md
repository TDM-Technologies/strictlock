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
| `SCOPE_LEASE_OWNER` | no | The owner label stamped in the blob. Resolution order: `SCOPE_LEASE_OWNER` → the git branch of the worktree (no `user.email` fallback). Errors if both are unavailable, e.g. a detached HEAD (an unresolvable owner is routed back to you, never guessed). `--owner` overrides. Owner is part of lock identity: with a live lock, `acquire` / extend / `release` by the same lock id but a different owner is refused (exit `5`). Two agents on one branch are one owner; give each its own `SCOPE_LEASE_OWNER`. |
| `SCOPE_LEASE_LOG_DIR` | no | Directory for the append-only decision log (`scope-lease.log`). Each successful acquire appends a JSON line (UTC timestamp, lock id, owner, leased/reclaimed paths), and each release that drops at least one scope appends one (UTC timestamp, lock id, the lock's owner as stamped at acquire, `released_by` = the releasing process's resolved owner, released paths). A reclaiming acquire row also carries a per-path `reclaimed_detail` (the evicted lock id, owner, deadline, and the new token). A denied acquire is not logged. If unset, no log is written. This is your concurrency audit trail. |

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
| `5` (`holder`) | At least one queried path is held by a live lock that is not mine. |
| `6` | **Machine-boundary fail-loud** — a lock from a non-shared ref store. Cross-machine exclusion is not real here; refusing rather than degrading silently. |

## `release --force` — for a human at a terminal (Q10, 2026-10-17)

```
python3 scope-lease.py release --force [--root DIR] [<source selectors>]
```

Releases whatever lock holds the resolved paths, whoever owns it and whatever its lock id, and
needs no identity of its own (it works on a detached HEAD). Each forced release is written to
the audit log as a `force_release` row (the previous lock id, owner and deadline per path, plus
`released_by`), and printed on stderr. Use it at close-out when the owner's identity no longer
exists (the agent's branch was switched or deleted, or the agent died). Without `--force`,
`release` still refuses a live lock held by a different owner. `plan-gate` always denies
`--force` to a gated agent. Set `SCOPE_LEASE_OWNER` per agent so a branch switch does not change
identity.

`fence-check` and `holder` need no owner: they work on a detached HEAD with no
`SCOPE_LEASE_OWNER`. Only `acquire` and a plain `release` derive one.

## `holder` — read-only lookup

```
python3 scope-lease.py holder [--root DIR] [--lock-id ID] [--owner OWNER | --owner-root DIR | --no-owner]
                              [--paths-from FILE|-] [--json] [PATH ...]
```

`--paths-from -` reads NUL-separated paths from stdin (no argv length limit, newlines in names
are fine); it adds to any `PATH` arguments. At least one path is required.

Reports, per path, whether it is `FREE`, `MINE` or `HELD` by someone else, and writes
nothing. Exit `0` when every path is free or mine, `5` when at least one is held by someone
else (the holder, the lease age and the deadline are printed), `1`/`6` on errors. It uses the
same path canonicalization as `acquire` (so case/Unicode folding agrees), looks in the repo at
`--root` (default: the current directory), and ignores the `SCOPE_LEASE` on/off switch.

*Mine* means the lock's `lock_id` equals `--lock-id` / `SCOPE_LEASE_LOCK_ID`, **or** the lock's
`owner` equals `--owner` / `SCOPE_LEASE_OWNER` (else the git branch of `--owner-root`, default
`--root`: the checkout of the *agent*, which need not be the repo the path lives in). With no
derivable identity (detached HEAD, no variable) or `--no-owner`, nothing is mine by owner. An
expired lock counts as free. `--json` prints one JSON object per path
(`path`, `key`, `status`, and for a lock: `lock_id`, `owner`, `token`, `deadline`, `acquired`,
`age_seconds`, `expired`). `plan-gate` calls this when `PLAN_GATE_SCOPE_LEASE` is set.

## Reclaim is surfaced, never silent

When `acquire` reclaims an expired holder's scope, it prints a `RECLAIMED` line to **stderr**
naming who was evicted, their old deadline, and the new token/deadline. There is no background
reaper — a scope only ever changes hands as a visible side effect of someone actively acquiring
it.

## Decision log

When `SCOPE_LEASE_LOG_DIR` is set, every successful acquire and every release that drops at
least one scope appends a JSON line to `scope-lease.log` with a UTC timestamp, the event, the
lock id, the owner, and the leased / reclaimed / released paths (a reclaiming acquire row also
carries `reclaimed_detail`: evicted lock id / owner / deadline and the new token per path). A denied acquire writes no row. That append-only trail is the concurrency-side audit record, written by the lock as it
acts and only when you set the log directory, the same posture as `plan-gate`'s decision
log. Logging is best-effort: a log-write failure never changes a lock outcome.
