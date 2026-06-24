# dev/ — StrictLock's own development state

StrictLock tracks its own development with the [`externalized-memory`](../externalized-memory/)
module it ships — dogfooding the shared-blackboard pattern.

- [`STATE.md`](STATE.md) — the shared blackboard: where the project stands right now,
  the single next action, open threads, decisions, and constraints. Read this first.
- [`BACKLOG.md`](BACKLOG.md) — the backlog: design questions and unfinished work, one
  entry each. The forward-looking *module* roadmap lives in [`../roadmap.md`](../roadmap.md);
  this is the finer-grained "what's left and why."

Both are written against the templates in
[`../externalized-memory/templates/`](../externalized-memory/templates/). When `STATE.md`'s
history grows long, split the closed items into an `ARCHIVE.md` from the same templates.
