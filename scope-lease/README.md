# scope-lease

**A git-native, zero-service exclusive lock over a path set. Agents that call `acquire` cannot
hold the same file at the same time, and plan-gate can refuse a gated agent's write to a file
someone else holds.**

The Concurrency flagship of the [StrictLock](../README.md) suite. [`plan-gate`](../plan-gate/)
enumerates the paths a unit of work *may* touch; `scope-lease` lets agents claim those paths
**exclusively** against each other. plan-gate says "only these paths"; scope-lease adds "…and
no other agent holds them while you do."

**Two levels of enforcement.**

1. **Lease-aware gate.** With `PLAN_GATE_SCOPE_LEASE` set to the path of `scope-lease.py`,
   plan-gate denies a gated agent's write to a file another agent holds a lease on. This covers
   the write surfaces plan-gate parses: Edit/Write/NotebookEdit, Bash redirects, writer scripts
   such as `atomic-write.py`, and MCP path arguments. It checks both the spelled path and the
   real path (symlinks). An agent is identified by `SCOPE_LEASE_OWNER` if set, else by the git
   branch of its session's worktree; agents sharing a branch count as one. A gated agent cannot
   pick its own identity with the direct routes (`--owner`, `--lock-id`, setting the variables,
   editing `refs/locks`), which plan-gate refuses.
2. **Strict mode.** With `PLAN_GATE_REQUIRE_LEASE=on` as well, every such write must hold a
   lease, so an agent that never called `acquire` is blocked.

**What is not covered.** Commands that `allowed_commands` admits and that write files without
plan-gate parsing a target (`cp`, `mv`, `tee`, `sed -i`, `git checkout`, build tools), hardlinks,
files in nested repos or submodules leased through the outer repo, and code the agent controls
(a plan that lets it run code it can change can edit lock refs). Agents that are not gated by
plan-gate are only excluded if they call `acquire` themselves, and a human's own edits and
merges are never blocked. `fence-check` is a command you run at your own merge step; no merge
gate ships with the suite. Details: [`../plan-gate/CONFIG.md`](../plan-gate/CONFIG.md#scope-lease-honored).

It exists to close the two failures human supervision used to cover when you run one agent at a
time and let two agents run at once:

- **deadlock-on-crash** — an agent dies mid-work still "holding" its files, and nothing frees
  them. scope-lease leases expire by deadline and the next agent reclaims them (logged).
- **stale-holder-write** — an agent loses its claim (its lease was reclaimed while it slept)
  and then wakes up and writes anyway. A `fence-check` run at your merge step detects this and
  refuses the write.

There's no daemon, no server, no lock service to run. The only coordination point is git: a
lock is an atomic `git update-ref` compare-and-swap on a ref that lives **off** every branch,
so it never re-enters the merge path.

## Why the merge is not the collision point

The instinct is "two agents both edited a file, git will flag the conflict." It won't. Git
**silently auto-merges** two edits to the same file on *different lines* — no conflict, no
marker, no signal. Two agents that each "cleanly" hold the same file produce a corrupt blend
that no review of either diff would catch. The only reliable fix is to **stop both agents from
holding the file at once**. That is what acquire-time exclusion does, for agents that use it.
The fence check is the backstop for the narrow window where a holder lost its lease but kept
writing.

## What it does

- **`acquire`** — normalizes each path to a canonical repo-relative key, then claims one
  `refs/locks/<sha1(path)>` ref per path in **one transactional** `git update-ref --stdin`
  (all-or-nothing). A path held by a **live** holder → a structured **DENY** naming the
  conflicting path and holder (exit 5, writes nothing — first-mover wins). A path held by an
  **expired** holder → reclaimed with `token + 1`, **logged to stderr** (never a silent reaper).
- **`fence-check`** — run at your merge step (the suite does not ship a merge gate): asserts this lock still holds every one of its
  path-refs. A missing or foreign ref means it was reclaimed away → **exit 4, write nothing**.
- **`release`** — drops only the locks this lock id owns. Idempotent.

The path set and the lock id come from a **path source** (see below): a plan-gate plan by
default, or a standalone fallback.

## What it prevents vs. detects vs. can't address

Same honesty the rest of StrictLock ships with — know exactly what guarantee you're buying:

- **Prevents** (at `acquire`): two agents *that each call `acquire`* concurrently holding the
  same file on **one machine sharing one `.git`**. This is the load-bearing guarantee; on its
  own it does not reach an agent that never calls `acquire` (plan-gate's lease-aware mode, above,
  covers a gated agent). With correct repo-relative
  normalization, the same file under any spelling — absolute vs. relative, `./` prefix, a
  trailing slash, and (on a case-insensitive or unicode-normalizing filesystem) a casing or
  NFD/NFC difference — collapses to one ref key, so the second claim is denied, not silently
  granted.
- **Detects** (at `fence-check`): the **stale-holder-write** — an agent whose lease was
  reclaimed (it ran past its deadline) and then tries to finalize a write. The fence refuses it
  at your merge step. It does not *prevent* the agent from having edited its working tree; it
  prevents that stale edit from landing.
- **Can't address**: (1) **cross-machine** exclusion. Acquire-time exclusion is only real when
  all worktrees share one `.git` ref store. A lock minted by a foreign store is a hard
  **fail-loud** (exit 6) on `acquire`, `fence-check` and `release`, never a silent downgrade — the real multi-machine fix (push-CAS to a
  shared origin) is a later phase. (2) A holder that writes *within* its valid lease window —
  that's authorized by construction; scope-lease guarantees only that no one else holds the
  file at the same time. (3) Anything a **human** does — `refs/locks/*` coordinates agents
  only; it never walls a person out of a manual merge or their own edits.

## Quickstart (the plan-gate adapter — the default)

If you already run [`plan-gate`](../plan-gate/), you already have the path set: scope-lease
reads the same active plan's `allowed_paths` for free.

```bash
# 1. Turn it on and point it at your plans directory (or reuse PLAN_GATE_PLANS_DIR).
export SCOPE_LEASE=on
export SCOPE_LEASE_PLANS_DIR="$HOME/.agent/plans"   # or leave unset if PLAN_GATE_PLANS_DIR is set

# 2. At session start, claim the active plan's paths exclusively.
python3 scope-lease.py acquire
# -> LEASED: lock 'add-logout-button' (owner work-branch) holds 2 scope(s) until ...

# 3. Just before you let the work merge, fence-check that you still hold them.
python3 scope-lease.py fence-check    # exit 0 = still yours; exit 4 = reclaimed away, stop.

# 4. At close-out, release.
python3 scope-lease.py release
```

## Standalone (no plan-gate)

A fleet that doesn't run plan-gate hands the path set in directly. The lock id is the identity
that holds the scope — it must be **stable** across acquire / fence-check / release.

```bash
export SCOPE_LEASE=on
python3 scope-lease.py acquire \
  --source paths --lock-id session-7 \
  --paths src/app.py src/util.py
```

The path set can also come from `SCOPE_LEASE_PATHS`, from stdin (one path per line), or from a
small own-frontmatter file (`--source file --file scope.md`). See [SCHEMA.md](SCHEMA.md).

## Targets & portability

The mechanism is **pure git** — `update-ref` CAS on an off-branch ref, a JSON blob, a
hash-object pin. There's nothing Claude-specific or harness-specific about it: any agent
runtime that can shell out at session start and at your merge step can adopt it. Configured
entirely by environment variables; no machine-specific defaults are baked in.

## Files

| File | Purpose |
|---|---|
| [`scope-lease.py`](scope-lease.py) | The lock. One file, standard library only. |
| [`CONFIG.md`](CONFIG.md) | Every `SCOPE_LEASE_*` environment variable. |
| [`SCHEMA.md`](SCHEMA.md) | The lock-ref blob, the path sources, and the lock-id contract. |
| [`examples/`](examples/) | A plan-gate plan, a standalone frontmatter file, and a walkthrough. |
| [`tests/`](tests/) | A standalone suite (stdlib `unittest`) that builds real git repos. |

## Requirements

Python 3.8+ (standard library only — no pip install) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
