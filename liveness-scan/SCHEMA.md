# liveness-scan schema

Three things have a defined shape: the **classification model** (the six statuses and the rules
that produce them), the **two activity signals**, and the **attribution rule** (which plan owns a
worktree).

## The classification model

`liveness-scan` classifies each git worktree into exactly one status. The decision is
deterministic and depends on three inputs per worktree: whether an active/executed plan **owns**
it, the **age** of its activity signal, and (when no plan owns it) whether its branch is **ahead
of base**.

```
is the worktree main / master / detached?
   └─ yes ──────────────────────────────────────────────► clean   (not a session worktree)
   └─ no:
      does an ACTIVE plan own this worktree?
         └─ yes:
              no activity signal at all ──────────────────► ambiguous  [ESCALATE]
              signal age > CEILING_MIN ───────────────────► ambiguous  [ESCALATE]
              signal age > the signal's stale window ─────► stalled    (likely slow; watch)
              otherwise ──────────────────────────────────► running
         └─ no:
              an EXECUTED plan owns it, OR branch ahead of base ─► done-unmerged  (pending merge)
              otherwise ──────────────────────────────────────► idle
```

- **`clean` is decided first**, before any plan claim — so an active plan that happens to carry
  main-repo-absolute paths can never mis-mark the `main` worktree as a running session.
- **`ambiguous` is the only escalating status.** It means "crashed or just slow — a human must
  decide." It carries a non-destructive recovery command. The scanner **never** turns ambiguous
  into a reap.
- The **stale window** is the heartbeat timeout when a heartbeat exists, else the (coarser) commit
  window — see signals below. The **ceiling** is a separate, larger bound on the whole unit of
  work; crossing it escalates even if the signal looks fresh.

## The two activity signals

| Signal | Source | Stale window | When used |
|---|---|---|---|
| `heartbeat` | mtime of `LIVENESS_SCAN_HEARTBEAT_RELPATH` under the worktree (existence + mtime only) | `LIVENESS_SCAN_HEARTBEAT_TIMEOUT_MIN` (default 10) | whenever the file exists |
| `commit-mtime` | the worktree HEAD commit's committer time | `LIVENESS_SCAN_COMMIT_STALE_MIN` (default 90) | fallback when no heartbeat |

The heartbeat is the sharp signal; the commit-mtime fallback is coarse (a long-thinking agent
that hasn't committed reads older than it is) and is **flagged in the reason** so the weaker
signal is never silently trusted. Either way, the **ceiling** (`LIVENESS_SCAN_CEILING_MIN`,
default 480) is the outer bound that escalates.

## The attribution rule

A plan **specifically owns** a worktree iff at least one of its `allowed_paths` is an **absolute**
path that resolves under that worktree's root.

- **Absolute paths only.** A relative `allowed_paths` entry (e.g. `src/app.py`) is not anchored to
  any particular worktree, so it claims none — attribution needs an absolute anchor.
- **`worktree_bypass: true` is excluded.** A bypass plan has authority across many worktrees but
  is the *session* of none. Attributing it per-worktree would mis-mark every worktree as that
  plan's session, so attribution ignores it. (Its known cost: a genuinely-running bypass session
  with no absolute path under its own worktree is invisible and classifies idle/done-unmerged — a
  safe false negative for a report-only tool, which under-claims liveness but never over-claims
  death.)
- Plans are read with the **same byte-0 frontmatter discipline** as plan-gate / scope-lease: a
  `status: active` only counts inside a real `---` block at the start of the file; one in prose or
  a fenced ```yaml example is inert.

## The structured (`--json`) digest

```jsonc
{
  "base": "origin/main",
  "counts": { "running": 1, "ambiguous": 1, "idle": 2, "clean": 1 },
  "active_plans": 1,
  "sessions": [
    { "path": "...", "branch": "feat-x", "head": "<sha>",
      "status": "ambiguous", "reason": "...", "escalate": true,
      "recovery": "cd '...' && git status && ...", "signal": "commit-mtime", "age_min": 612.0 }
  ],
  "escalations": 1
}
```

The JSONL log line (`liveness-scan.log`) is the same shape, condensed to the timestamp, base,
counts, active-plan count, and the escalation list — one line per scan, append-only.
