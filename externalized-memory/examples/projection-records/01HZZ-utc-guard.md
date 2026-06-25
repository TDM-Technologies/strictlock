---
id: 01HZZ-utc-guard
status: active
summary: reject non-canonical timestamps at write time
updated: 2026-06-24T22:00:00Z
---

The canonical-UTC guard runs when a record is written, not when the projection is
rendered. That is what keeps the render reproducible despite human-entered timestamps:
by render time, every timestamp on disk is already the one canonical spelling.
