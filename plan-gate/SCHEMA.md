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
| `status` | string | `active` arms the plan. Any other value (e.g. `draft`, `archived`) makes it inert. **Exactly one** active plan per worktree. When approval is on (whenever `PLAN_GATE_LOG_DIR` is set, see [CONFIG.md](CONFIG.md#plan-approval)), `status: active` also needs a recorded human approval of the plan's exact bytes, or the plan is inert. |
| `allowed_paths` | list of strings | The exact files the agent may write. **Exact match only** — a directory entry authorizes *nothing* inside it. Absolute or repo-root-relative. |
| `allowed_commands` | list of strings | Command **prefixes** the agent may run. For Bash, every *segment* of a command (see below) must *start with* one of these, or be an always-allowed read-only command or a bare `cd`. |
| `worktree_bypass` | bool | `true` opts out of the worktree guard for deliberately cross-tree work (absolute `allowed_paths`). Omit otherwise. |

## Authorization semantics (read these — they bite)

- **Exact-path matching.** `allowed_paths` matches the target file by exact (normalized)
  path. Listing `src/` does **not** authorize `src/foo.ts`. Enumerate every file. The gate
  prints a warning to stderr if it sees a directory in `allowed_paths`.
- **`allowed_paths` limits tool-call writes, not what an allowed command writes.** A script run
  through Bash is checked as a command (prefix match against `allowed_commands`), not by the
  files it writes. `python3 atomic-write.py STATE.md` is matched by its prefix; its target file
  is not checked against `allowed_paths` today. Only shell redirections (`>`, `>>`, ...) in
  the command are treated as file writes.
- **Prefix matching for commands, per segment.** `allowed_commands: [git add]` authorizes
  `git add -A .`. Keep prefixes specific — a loose prefix is a loose grant. A Bash command is
  split into segments (on `&&`, `||`, `;`, `|`, `&`, newlines, and the bodies of `$(...)`,
  backticks and `<(...)`), and **each** segment must start with an allowed prefix (or be
  read-only / a bare `cd`): `<allowed> && <other>` needs `<other>` granted too. One entry per
  command; an entry spelling out a whole compound does not authorize it.
- **Redirections that write are file writes.** `>`, `>>`, `>|`, `&>`, `N>` targets must be
  `allowed_paths` entries (exact match), like Edit/Write. `/dev/null` is always fine.
- **Read-only commands are always allowed**, regardless of plan: `git status`, `git diff`,
  `git log`, `ls`, `pwd`, `cat`, `rg`, `grep`, `head`, `tail`, `which`, and friends (in their
  non-mutating forms: `git branch` lists, `find` has no `-delete`/`-exec`, `git fetch` is not
  included), plus the `git -C <path> <read-only-subcommand>` shape. Bare `cd <path>`
  navigation is allowed. A compound of read-only segments is read-only; one non-read-only
  segment makes the whole command need a grant.
- **Path anchoring.** Relative entries anchor at the session's worktree root (or repo root).
  Absolute entries are used as-is. See [CONFIG.md](CONFIG.md) for the worktree guard.

## Frontmatter parser notes

The gate ships a deliberately tiny YAML subset parser (top-level scalars and lists of
strings). Two behaviors worth committing to memory:

- **Inline comments are stripped.** A trailing ` # comment` is removed from both scalars and
  list entries, so `worktree_bypass: true  # cross-tree` correctly reads as `true` and an
  `allowed_paths` entry with a trailing note still matches. The `#` must be preceded by
  whitespace (the YAML rule): a `#` embedded in a token — e.g. a path like `C:\data#1\x.ts` —
  is preserved, and a `#` inside quotes is kept. Own-line comments (a line starting with `#`)
  are skipped as before.
- **Strings and simple lists only.** No nested maps, anchors, flow syntax, or multi-line
  scalars. Keep frontmatter flat. Both `key: []` and a key with no entries beneath it are
  accepted as an empty list.

## Lifecycle

1. **Author** a plan describing exactly the files and commands the work needs; set
   `status: active`. (An agent may draft it; a draft does nothing until approved.)
1b. **Approve** — with `PLAN_GATE_LOG_DIR` set, a human runs, in a terminal,
   `python3 /abs/path/to/plan-gate.py approve /abs/path/to/plan.md --log-dir /abs/log/dir
   [--plans-dir /abs/plans/dir]` (the gate's deny message prints this exact command). Any later
   edit to the file (even one byte, including the `status` line) voids the approval; approve
   again.
2. **Work** — the gate confines the agent to that envelope.
3. **Retire** — at close-out, archive the file (set `status: archived`, or move/delete it),
   **then** a human runs `plan-gate.py revoke <plan.md> --log-dir ...` in a terminal. Revoking
   records that nothing under that plan name is active until it is approved again, so
   writing the old approved bytes back does not reactivate it. The next unit of work starts
   from a clean, deny-by-default state.
