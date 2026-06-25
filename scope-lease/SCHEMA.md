# scope-lease schema

Three things have a defined shape: the **lock-ref blob** stored in git, the **path sources**
that supply the path set + lock id, and the **lock-id contract** that ties an acquire to its
later fence-check and release.

## The lock ref

Each claimed path becomes one git ref:

```
refs/locks/<sha1(canonical-repo-relative-path)>  ->  a blob
```

- The path is **hashed**, not embedded, so any path string yields a valid fixed-width ref name.
- The ref lives under `refs/locks/*` — **off every branch** (`refs/heads/*`), so a lock never
  re-enters the merge path and never shows up in a `git log`.
- The ref points at a JSON blob (deterministic — sorted keys, no whitespace):

| Field | Type | Meaning |
|---|---|---|
| `lock_id` | string | The identity holding the scope. Acquire stamps it; fence-check and release compare against it. |
| `owner` | string | Human/agent label (branch, email, CI runner id). Informational — used in DENY/reclaim messages, not in the ownership decision. |
| `token` | int | Monotonic per-path fencing token. Starts at 1; `+1` on every reclaim/extend. The replay-safety backstop for reclaim CAS. |
| `deadline` | string | ISO-8601 expiry. Past the deadline the lock is **reclaimable**. The only liveness signal. |
| `host` | string | The hostname that minted the lock. |
| `git_dir` | string | The resolved git-common-dir that minted it. `host` + `git_dir` together identify the ref store. |

`host` + `git_dir` are the **fail-loud single-machine boundary**: a lock read from a ref store
whose host/git-dir differs from this process's is a hard stop (exit 6), never a silent
acquire-over.

## Path normalization (the same-file guard)

Before a path becomes a ref, it is folded to a single **canonical repo-relative POSIX key** so
that every spelling of the same file maps to **one** ref. This is the load-bearing correctness
piece — two keys for one file means two agents both "cleanly" acquire and git silently merges
their edits.

The fold:

1. Resolve absolute ↔ relative against the git top-level; collapse `.` / `..`; forward-slash;
   drop a trailing slash.
2. Then, **gated on the repo filesystem's own identity semantics** (read from git's
   auto-detected `core.ignorecase` and `core.precomposeunicode`):
   - if `core.precomposeunicode` is true (macOS/APFS) → Unicode-NFC-fold the key, so an NFD and
     an NFC spelling of `café.py` name one lock;
   - if `core.ignorecase` is true (case-insensitive FS) → case-fold the key, so `Handler.py`
     and `handler.py` name one lock.

The fold is **FS-aware on purpose**: on a case-sensitive filesystem (typical Linux)
`Handler.py` and `handler.py` are genuinely different files, and the key keeps them distinct —
no false collision. The key agrees with git's *own* notion of "the same file" on this FS.

## Path sources

`SCOPE_LEASE_SOURCE` selects where the path set + lock id come from:

| Source | Path set | Lock id |
|---|---|---|
| `plan-gate` (default) | `allowed_paths` from the single `status: active` plan in `SCOPE_LEASE_PLANS_DIR` / `PLAN_GATE_PLANS_DIR` | the plan's `name`, slugified |
| `paths` | `--paths`, else `SCOPE_LEASE_PATHS`, else stdin (one per line) | `--lock-id` / `SCOPE_LEASE_LOCK_ID` (required) |
| `file` | `allowed_paths` from `--file` / `SCOPE_LEASE_PATHS_FILE` (own-frontmatter) | the file's `name`, slugified |

### The plan-gate / own-frontmatter file format

Both the `plan-gate` and `file` sources read the **same minimal YAML frontmatter** plan-gate
parses — top-level scalars and a list of strings under `allowed_paths`:

```yaml
---
name: add-logout-button
status: active            # the plan-gate source requires exactly one active plan
allowed_paths:
  - src/components/Header.tsx
  - src/auth/session.ts
---
```

- `allowed_paths` entries may be absolute or repo-root-relative — normalization (above) folds
  them to canonical keys either way.
- `name` becomes the lock id (slugified: lowercased, non-`[a-z0-9._-]` runs → `-`). Override it
  with `--lock-id` / `SCOPE_LEASE_LOCK_ID`.
- The `file` source ignores `status` (it's a single explicit file, not a directory scan); the
  `plan-gate` source requires **exactly one** `status: active` plan in the directory and errors
  on zero or many — the same single-active invariant plan-gate enforces.

## The lock-id contract

The lock id is the identity that **holds** the scope. Acquire stamps it into every blob;
fence-check and release act only on refs whose blob `lock_id` matches.

- It must be **stable** across the acquire → fence-check → release lifecycle of one unit of
  work. With the `plan-gate` / `file` sources it's derived from the plan `name`, so it's stable
  as long as the plan is. With `--source paths` you supply it (`--lock-id` /
  `SCOPE_LEASE_LOCK_ID`) and are responsible for reusing the same value.
- `owner` is **not** the identity check — two sessions could share an owner label; only
  `lock_id` decides ownership. `owner` is there to make a DENY or a reclaim message name a
  human-legible holder.

## Empty path set

A resolved path set with zero usable paths is a **no-op success** for `acquire` and `release`
(nothing to claim / drop) — it never errors and never writes a ref. (`acquire` still requires a
resolvable lock id, so a misconfigured source surfaces rather than silently leasing nothing
under a blank id.)
