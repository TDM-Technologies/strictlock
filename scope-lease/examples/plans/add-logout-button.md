---
name: add-logout-button
status: active
allowed_paths:
  - src/components/Header.tsx
  - src/auth/session.ts
  - src/components/Header.test.tsx
allowed_commands:
  - npm test
  - git add
  - git commit
---

# add-logout-button

A plan-gate plan file. `scope-lease`'s default adapter reads the same frontmatter plan-gate
does:

- `allowed_paths` becomes the **path set** scope-lease claims exclusively — so plan-gate's
  "the agent may only write these files" gains "…and no other agent holds them at the same
  time."
- `name` (slugified) becomes the **lock id** — the identity that holds the scope across
  acquire / fence-check / release. Here: `add-logout-button`.

Point scope-lease at this directory:

```bash
export SCOPE_LEASE=on
export SCOPE_LEASE_PLANS_DIR="$(pwd)/examples/plans"
python3 scope-lease.py acquire
```

Everything below the frontmatter is for humans; the lock reads only the frontmatter.
