# projection bundle — record schema & guarantees

The [projection bundle](projection.py) turns externalized-memory from *a blackboard* into
*a blackboard with an auditable, reproducible status projection*. Instead of hand-merging a
summary file (a last-writer-wins race that silently drops lines), you write one small record
per work unit and let the generator derive the summary. This document pins the **record
shape** and the **five guarantees** the bundle makes.

## The record

One record per work unit, decision, or thread: a file `<id>.md` inside the records directory.
A record is a fenced frontmatter block followed by a **required** free-text body.

```markdown
---
id: 01HZX-build-renderer
status: active
summary: build the deterministic render
updated: 2026-06-25T14:30:00Z
---

The body is required. It carries the actual content the projection is *about* —
the notes, the decision, the rationale. A record with an empty body is rejected.
```

### Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | **yes** | Stable, unique record id. Also the filename stem (`<id>.md`). Determines render order (sorted explicitly — never filesystem order). |
| `status` | no | Free short status (`active` / `done` / whatever your workflow uses). Rendered as a column. |
| `summary` | no | One-line human summary. Rendered as a column. |
| `updated` | **yes** | Last-update instant, **canonical UTC** `YYYY-MM-DDTHH:MM:SSZ` (see guard below). |
| *(body)* | **yes** | Free text after the closing fence. Must be non-empty. |

Field order on disk is fixed (`id, status, summary, updated`) so the on-disk schema is a
single source of truth and a re-render is stable. The reader is a minimal line-based parser
for these controlled writes — not a general YAML parser — so an embedded `:` in a value is
safe and needs no quoting; values are single-line.

## The five guarantees

### 1. Record schema
The shape above. `build_record(...)` is the only sanctioned writer and runs the full
validation chain, so a record can never be persisted in an invalid state.

### 2. Deterministic render — git-free and clock-free
`render_projection(records_dir)` is a **pure function of the record bytes**. It never reads
the system clock and never shells out to git. Inputs are sorted **explicitly by `id`**
(filesystem listing order is unstable — the #1 cause of non-determinism), output is LF-only,
and table cells are escaped. Same records ⇒ byte-identical output, run after run, machine
after machine.

### 3. Fenced-region splicing
The render is spliced **only between two marker lines** in a target file; every byte outside
that region is preserved exactly. The generated view can live inside a hand-maintained
document without the generator ever clobbering the surrounding prose. If a marker is missing
the splice **refuses** — it never guesses where the region belongs and never appends a second
copy.

### 4. Canonical-UTC timestamp guard
The thing that makes guarantee #2 survive human-entered timestamps. `2026-06-25T12:00:00+02:00`
and `2026-06-25T10:00:00Z` are the same instant but different bytes — a render that normalized
them would have to know about offsets/DST and would stop being pure. So the guard runs at
**write time**: a timestamp must already be canonical UTC `YYYY-MM-DDTHH:MM:SSZ` (trailing `Z`,
no offset, no fractional seconds, no lowercase `t`/`z`, no space separator). Non-canonical input
is **rejected, not normalized**, so by render time every timestamp on disk is already the one
canonical spelling. The check is purely lexical + a calendar range check — it does **not** read
the clock, so two machines in different timezones validate identically.

### 5. Required-body validation
A record with an empty body is **rejected** at write time. The projection's payload is the
body; a bodiless record is malformed, not an edge case to paper over.

## Fail-closed posture

Every validation path **rejects loudly** with a non-zero exit and a reason on stderr — never a
silent pass, never a silently-coerced value:

- non-canonical timestamp → rejected (exit 3),
- missing/blank body → rejected (exit 3),
- missing required field → rejected (exit 3),
- splicing into a file without the configured fence markers → rejected (exit 1),
- the projection write is **atomic** (temp file in the same dir → `os.replace`), so a crash
  mid-write can never leave a torn projection that still looks authoritative.

## Configuration

All bindings come from the environment (or an overriding flag) — no machine paths, no project
names baked in. See the [README CONFIG section](README.md#projection-bundle-configuration).

| Variable | Meaning | Default |
|---|---|---|
| `EM_PROJECTION_RECORDS_DIR` | Directory holding the `<id>.md` records. | *(required)* |
| `EM_PROJECTION_TARGET` | File the projection is spliced into. | *(required for splice/check)* |
| `EM_PROJECTION_BEGIN` | Begin fence marker line. | `<!-- BEGIN externalized-memory projection -->` |
| `EM_PROJECTION_END` | End fence marker line. | `<!-- END externalized-memory projection -->` |
| `EM_PROJECTION_TITLE` | Heading at the top of the projection region. | `Status projection` |
