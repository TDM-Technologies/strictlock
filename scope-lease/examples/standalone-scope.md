---
name: nightly-refactor
allowed_paths:
  - src/db/pool.py
  - src/db/migrations/
  - tests/db/test_pool.py
---

# nightly-refactor (standalone own-frontmatter scope)

The `file` source for fleets that don't run plan-gate. Same minimal frontmatter, read by
`--source file`:

```bash
export SCOPE_LEASE=on
python3 scope-lease.py acquire --source file --file examples/standalone-scope.md
```

- `allowed_paths` is the path set; `name` (`nightly-refactor`) is the lock id.
- `status` is ignored for the `file` source — it's a single explicit file, not a directory
  scan, so there's no single-active invariant to enforce here.

The directory entry `src/db/migrations/` and the file `tests/db/test_pool.py` become **two
distinct** lock keys — a directory and a file are different scopes, never folded together.
