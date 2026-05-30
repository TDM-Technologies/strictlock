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
  repo-root-relative.
- `allowed_commands` — a Bash/PowerShell command is allowed only if it **starts with** one
  of these prefixes (read-only commands like `git status`, `ls`, `cat` are always allowed).

Everything below the frontmatter is for humans; the gate ignores it.
