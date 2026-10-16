---
name: add-logout-button
status: active
allowed_paths:
  - src/components/Header.tsx
  - src/auth/session.ts
  - src/components/Header.test.tsx
allowed_commands:
  - npm test
  - npm run lint
  - git add
  - git commit
---

# add-logout-button

Add a logout button to the site header that clears the auth session and redirects
to the login page.

## Notes on the frontmatter (this is what the gate reads)

- `status: active` — exactly one plan in the plans directory may be active at a time
  (scoped per git worktree). The gate denies everything if zero are active.
- `allowed_paths` — the agent may write **only** these exact files. Listing a directory
  authorizes nothing inside it; enumerate each file. Entries may be absolute or
  repo-root-relative. **Use absolute paths for real work.** The relative entries above are
  for readability: inside a git worktree, a plan claims that worktree only through an
  absolute entry under it, so a plan with only relative paths denies every write and every
  non-read-only command there. (Example: `/absolute/path/to/project/src/components/Header.tsx`.)
- `status: active` is, today, simply what makes the plan live; nothing records who set it.
- `allowed_commands` — a Bash command is allowed only if **every segment** of it (split on
  `&&`, `||`, `;`, `|`, `&`, newlines, and including `$(...)` bodies) **starts with** one of
  these prefixes; a PowerShell command must start with one. Read-only commands like
  `git status`, `ls`, `cat` are always allowed. A `>` redirection into a file is a file write
  and needs that file in `allowed_paths`.

Everything below the frontmatter is for humans; the gate ignores it.
