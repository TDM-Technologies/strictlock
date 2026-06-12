# todo-api — state (the shared blackboard)

<!--
  A FICTIONAL, populated STATE file for a toy project, showing the shape of a real
  blackboard. Every value here is invented — structure and prose only. Compare with
  ../templates/STATE.template.md.
-->

## Meta
- last_session: 2026-05-18T16:40:00Z
- agent: executor
- git_ref: a1b2c3d
- touched: src/handlers/todos.py, tests/test_todos.py

## Next Action
Wire the DELETE /todos/{id} handler to the soft-delete column added last session;
its test is already written and currently failing.

## Active Threads
- t1 — in progress — soft-delete: column + migration landed, handler not wired yet
- t2 — blocked — rate-limiter: needs a decision on per-IP vs per-token buckets
- t3 — ready — paginate GET /todos (cursor-based), spec agreed

## Decisions
- 2026-05-12 — cursor pagination over offset — stable under concurrent inserts
- 2026-05-15 — soft-delete via a deleted_at column — keep an audit trail of removals

## Constraints
- the test DB resets between runs; never assume rows persist across test files
- IDs are ULIDs, not ints — compare them as strings, do not cast to integer
