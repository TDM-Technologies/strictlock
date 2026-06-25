# projection bundle — example

A worked example of the [projection bundle](../projection.py): a records directory rendered
into the fenced region of a hand-maintained status document, deterministically.

Files:

- [`projection-records/`](projection-records/) — three fictional records for a toy `todo-api`
  project, one file per work unit (`<id>.md`). Each has the required canonical-UTC `updated`
  field and a non-empty body.
- [`PROJECTION.example.md`](PROJECTION.example.md) — the target document. The prose outside the
  `BEGIN`/`END` markers is hand-maintained; only the region between them is generated.

## 1. Validate a record before you write it

The same gate the writer runs. Pipe a record in, or point at a file:

```bash
python3 ../projection.py validate --file projection-records/01HZX-build-renderer.md
# -> projection: OK — record '01HZX-build-renderer' is valid
```

A non-canonical timestamp or an empty body is rejected with exit 3 and a reason — the record
is never persisted in a bad state.

## 2. Render the projection (pure — no clock, no git)

```bash
python3 ../projection.py render --records-dir projection-records
```

Run it twice: the bytes are identical. The render sorts records explicitly by `id`, never
reads the system clock, and never shells out to git — so the output is a pure function of the
record bytes.

## 3. Splice it into the target's fenced region

```bash
export EM_PROJECTION_RECORDS_DIR=projection-records
export EM_PROJECTION_TARGET=PROJECTION.example.md
python3 ../projection.py splice --title "Status projection — todo-api"
```

Only the bytes **between** the two markers change. The heading, intro, and trailing notes in
`PROJECTION.example.md` are preserved exactly. The write is atomic, so a crash mid-splice can
never tear the document.

## 4. Check for drift (the oracle)

```bash
python3 ../projection.py check --title "Status projection — todo-api"
# exit 0 when the region is a fresh render; exit 1 (with a diff size) when it is stale
```

Wire `check` into CI the same way the rest of StrictLock does — a stale projection region
fails the build, so the generated view can never silently drift from the records.

## Why a projection at all?

A hand-merged summary file is a last-writer-wins race: two sessions appending to it can
silently drop a line. Make the summary a **generated view** over per-record files and that
race disappears — each session writes only its own record, the view is derived, and "whoever
renders last emits the complete view" is the correct semantics. The audit trail falls out as a
byproduct: the projection is reproducible from the records at any commit. See
[`../PROJECTION-SCHEMA.md`](../PROJECTION-SCHEMA.md) for the full contract.
