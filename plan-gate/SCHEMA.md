# plan-file schema

A *plan* is a Markdown file in `PLAN_GATE_PLANS_DIR` whose **YAML frontmatter** declares the
authorization envelope for a unit of work. The body below the frontmatter is for humans; the
gate ignores it. Exactly one plan may be `status: active` at a time (scoped per git worktree).

## Minimal example

```yaml
---
name: add-logout-button
status: active
allowed_paths:
  - src/components/Header.tsx
  - src/auth/session.ts
allowed_commands:
  - npm test
  - git add
  - git commit
---

# add-logout-button
Free-form description for humans goes here.
```

## Fields

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Human label for the plan. Not load-bearing for authorization. |
| `status` | string | `active` arms the plan. Any other value (e.g. `draft`, `archived`) makes it inert. **Exactly one** active plan per worktree. |
| `allowed_paths` | list of strings | The exact files the agent may write. **Exact match only** — a directory entry authorizes *nothing* inside it. Absolute or repo-root-relative. |
| `allowed_commands` | list of strings | Command **prefixes** the agent may run. A command is allowed if it *starts with* one of these (plus the always-allowed read-only commands). |
| `worktree_bypass` | bool | `true` opts out of the worktree hard-guard for deliberately cross-tree work (absolute `allowed_paths`). Omit otherwise. |

## Authorization semantics (read these — they bite)

- **Exact-path matching.** `allowed_paths` matches the target file by exact (normalized)
  path. Listing `src/` does **not** authorize `src/foo.ts`. Enumerate every file. The gate
  prints a warning to stderr if it sees a directory in `allowed_paths`.
- **Prefix matching for commands.** `allowed_commands: [git add]` authorizes
  `git add -A .`. Keep prefixes specific — a loose prefix is a loose grant.
- **Read-only commands are always allowed**, regardless of plan: `git status`, `git diff`,
  `git log`, `ls`, `pwd`, `cat`, `rg`, `grep`, `head`, `tail`, `which`, and friends, plus
  the `git -C <path> <read-only-subcommand>` shape. Bare `cd <path>` navigation is allowed;
  a `cd <path> && <something>` compound is checked segment-by-segment.
- **Path anchoring.** Relative entries anchor at the session's worktree root (or repo root).
  Absolute entries are used as-is. See [CONFIG.md](CONFIG.md) for the worktree hard-guard.

## ⚠️ Frontmatter parser limitations

The gate ships a deliberately tiny YAML subset parser (top-level scalars and lists of
strings). Two consequences worth committing to memory:

- **No inline comments.** `worktree_bypass: true  # cross-tree` is read as the literal value
  `true  # cross-tree`, which is **not** `true`, so the flag silently doesn't take. Put
  comments on their **own line** (a line starting with `#` is skipped). Own-line comments
  inside a list are fine.
- **Strings and simple lists only.** No nested maps, anchors, flow syntax, or multi-line
  scalars. Keep frontmatter flat.

## Lifecycle

1. **Author** a plan describing exactly the files and commands the work needs; set
   `status: active`.
2. **Work** — the gate confines the agent to that envelope.
3. **Retire** — set `status: archived` (or move/delete the file) at close-out, so the next
   unit of work starts from a clean, deny-by-default state.
