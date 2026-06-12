# todo-api — mailbox (work-package handoff)

<!--
  A FICTIONAL, in-flight mailbox: the planner has left one work package for the
  executor, and the executor has written its result back. Every value is invented —
  structure and prose only. Compare with ../templates/MAILBOX.template.md.
-->

## Work Package  (planner -> executor)
- id: pkg-2026-05-18-delete-handler
- from: planner
- to: executor
- intent: wire the DELETE /todos/{id} handler against the existing soft-delete column

### Plan
1. In src/handlers/todos.py add `delete_todo(id)`: set deleted_at = now(), return 204.
2. Exclude rows with a non-null deleted_at from GET /todos and GET /todos/{id}.
3. Run: pytest tests/test_todos.py -k delete

### Acceptance
tests/test_todos.py::test_delete_soft_deletes passes and no row is physically removed.

## Result  (executor -> planner)
- status: done
- ref: a1b2c3d
- notes: handler wired, delete tests green; GET filtering split out as thread t1 follow-up

---

Once both sides have consumed the package above, the executor **clears** this file
to exactly the sentinel line — an empty mailbox is the resting state:

```text
MAILBOX EMPTY — no work package in flight.
```
