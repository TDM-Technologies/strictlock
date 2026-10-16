---
name: add-logout-button
status: active
allowed_paths:
  - /path/to/your/worktree/src/components/Header.tsx
  - /path/to/your/worktree/src/auth/session.ts
allowed_commands:
  - npm test
  - git add
  - git commit
---

# add-logout-button

A sample plan for liveness-scan. Copy it into the directory named by `LIVENESS_SCAN_PLANS_DIR`
(or `PLAN_GATE_PLANS_DIR`) and replace `/path/to/your/worktree` with the **absolute** path of the
worktree this session runs in — for example the path `git worktree list` prints for it.

How liveness-scan reads this file:

- `status: active` marks the plan as owned by a running session (`executed` marks finished work
  that is not yet merged; anything else is ignored). Only the frontmatter block at the very top of
  the file counts; this prose is never parsed.
- A worktree is **attributed** to this plan because at least one `allowed_paths` entry is an
  absolute path under that worktree's root. A relative entry such as `src/a.py` claims nothing.
- A plan with `worktree_bypass: true` is never attributed to a worktree (it has authority in many
  worktrees and is the session of none), even if it lists an absolute path under one.
- While the plan is `active`, the worktree is `running` while its activity signal is fresh,
  `stalled` once the signal goes quiet, and `ambiguous` (escalated to a human) when the signal is
  older than `LIVENESS_SCAN_CEILING_MIN` or the work has been open longer than that since its
  earliest branch commit.
