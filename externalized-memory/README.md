# externalized-memory

**The shared blackboard: one on-disk state file as the source of truth across
stateless agent sessions.**

A [StrictLock](../README.md) module. An LLM agent is functionally stateless across
sessions — its working memory is the context window: ephemeral, bounded, and gone
the moment the session ends or crashes. If the only record of *what was approved*
and *what has been done* lives in that window, then your project state is as durable
as a chat log. In regulated work that isn't a productivity problem, it's a
governance one: you cannot audit a vibe.

`externalized-memory` moves that state **out of the model and onto disk** — a single
state file per project that any session reads first and writes last, updated with an
atomic write so a crash can never corrupt it. Same posture as the rest of StrictLock:
plain files, fail closed, the audit trail falls out as a byproduct.

This module ships the **pattern**, not a hook: templates you copy into your project,
one small reference script, and the design write-up behind every rule. Adopt the
template alone, or pair it with [`plan-gate`](../plan-gate/) — they're better
together, because each makes the other auditable.

## What it is

- **The shared blackboard.** One state file per project is the single source of
  truth for cross-session, cross-agent state. Any agent picking up the work reads it
  to learn where things stand; any agent making progress writes back to it. The
  close-out commit is the heartbeat — no commit, no "done".
- **One job per file.** The state file is not a mailbox and not an archive. A
  bidirectional *mailbox* carries one in-flight work package between sessions; an
  *archive* holds completed history. Mixing those concerns turns the blackboard into
  a junk drawer and it stops being readable at a glance — which, for a file whose
  whole job is to be readable at a glance by an amnesiac, is fatal.
- **Git is the ground truth.** The file records the git ref it was written against,
  so every session can open with a staleness tripwire and distrust a cached or stale
  read before acting on it.

## What ships

| Path | What it is |
|---|---|
| [`templates/STATE.template.md`](templates/STATE.template.md) | The shared blackboard: **Meta / Next Action / Active Threads / Decisions / Constraints**. |
| [`templates/MAILBOX.template.md`](templates/MAILBOX.template.md) | A bidirectional work-package mailbox, cleared to a sentinel line after use. |
| [`templates/ARCHIVE.template.md`](templates/ARCHIVE.template.md) | Append-only completed history moved off the state file. |
| [`atomic-write.py`](atomic-write.py) | Reference implementation of the atomic write-temp-then-rename write. Plain script, no daemon, standard library only. |
| [`projection.py`](projection.py) | The **projection bundle**: turn a directory of per-record files into a deterministic, git-free/clock-free status projection spliced into a fenced region of a target file. Validates each record (canonical-UTC timestamps, required body). |
| [`PROJECTION-SCHEMA.md`](PROJECTION-SCHEMA.md) | The record shape and the five projection guarantees. |
| [`DESIGN.md`](DESIGN.md) | The why behind every rule — each one is a scar. |
| [`examples/`](examples/) | Fictional, populated artifacts and a step-by-step walkthrough — including the [projection-bundle walkthrough](examples/PROJECTION.README.md). |

All templates and examples are **structure and prose only** — never real handoff
data.

## How to adopt

### The template alone

Copy the templates into a state directory in your project and fill them in:

```bash
mkdir -p ~/project/.state
cp templates/STATE.template.md   ~/project/.state/STATE.md
cp templates/MAILBOX.template.md ~/project/.state/MAILBOX.md
cp templates/ARCHIVE.template.md ~/project/.state/ARCHIVE.md
```

Then make it a habit: **read the state file first, write it last, commit the
close-out.** Update it with the atomic writer so a half-write can never land:

```bash
your_state_generator | python atomic-write.py ~/project/.state/STATE.md
```

See [`examples/README.md`](examples/README.md) for the full loop, including the
session-opening staleness tripwire.

### With the gate

List your state files in a [`plan-gate`](../plan-gate/) plan's `allowed_paths` so
only an approved session may write them, and let the gate log every write. Now each
change to the blackboard is an authorized, audited event — state becomes evidence,
not testimony. [`DESIGN.md`](DESIGN.md) makes that argument in full.

## The atomic write

`atomic-write.py` reads new file content on stdin and writes it to the target path:
it stages the bytes in a temp file in the **same directory**, fsyncs, then
`os.replace`s it over the target. The rename either happens or it doesn't — there is
no half. A missing parent directory or any IO error is a **loud, non-zero-exit
failure**, never a silent no-op, and a failed write never leaves a partial sibling
behind. Run it by hand:

```bash
printf '%s' "$new_content" | python atomic-write.py path/to/STATE.md
```

## The projection bundle

The blackboard above is hand-written — the right shape for a single human handoff. The
**projection bundle** ([`projection.py`](projection.py)) covers the other case: a *machine-derived*
status view over many small records. Instead of hand-merging a summary file — a last-writer-wins
race where two sessions appending can silently drop a line — you write one record per work unit
and let the generator derive the view. Each session writes only its **own** record; the
projection is derived, so "whoever renders last emits the complete view" is the correct
semantics and transient staleness self-heals on the next render. That turns the blackboard from
*a place state lives* into *a place state lives plus an auditable, reproducible projection of
it*.

It has five parts, all proven by [`tests/test_projection.py`](tests/test_projection.py):

1. **Record schema** — one `<id>.md` per record: a fenced frontmatter block + a **required**
   body. See [`PROJECTION-SCHEMA.md`](PROJECTION-SCHEMA.md).
2. **Deterministic render** — **git-free and clock-free**. Inputs sorted explicitly by `id`,
   no system-clock read, no git invocation, LF-only output: same records ⇒ byte-identical bytes.
3. **Fenced-region splicing** — the render is written only **between** two marker lines in a
   target file; every byte outside the region is preserved exactly. A missing marker is a hard
   refusal, never a silent append.
4. **Canonical-UTC timestamp guard** — timestamps must already be canonical UTC
   `YYYY-MM-DDTHH:MM:SSZ`. Non-canonical input is **rejected at write time, not normalized at
   render time**, which is what keeps the render reproducible despite human-entered timestamps.
5. **Required-body validation** — a record with an empty body is **rejected**.

```bash
export EM_PROJECTION_RECORDS_DIR=~/project/.state/records
export EM_PROJECTION_TARGET=~/project/STATUS.md       # must already contain the fence markers

python projection.py validate --file "$EM_PROJECTION_RECORDS_DIR/01-foo.md"  # reject a bad record
python projection.py render                                                  # pure, to stdout
python projection.py splice                                                  # into the fenced region
python projection.py check                                                   # oracle: fail on drift (CI)
```

### Projection bundle configuration

Every binding comes from the environment (or an overriding flag) — no machine paths, no project
names baked in. Variables are consistently `EM_PROJECTION_`-prefixed.

| Variable | Meaning | Default |
|---|---|---|
| `EM_PROJECTION_RECORDS_DIR` | Directory holding the per-record `<id>.md` files. | *(required for render/splice/check)* |
| `EM_PROJECTION_TARGET` | The file the projection is spliced into (must already contain the fence markers). | *(required for splice/check)* |
| `EM_PROJECTION_BEGIN` | The begin fence marker line. | `<!-- BEGIN externalized-memory projection -->` |
| `EM_PROJECTION_END` | The end fence marker line. | `<!-- END externalized-memory projection -->` |
| `EM_PROJECTION_TITLE` | Heading rendered at the top of the projection region. | `Status projection` |

The worked example is in [`examples/PROJECTION.README.md`](examples/PROJECTION.README.md). Pair it
with [`plan-gate`](../plan-gate/) by listing the target in a plan's `allowed_paths` and wiring
`projection.py check` into CI: now the generated view can never silently drift from the records,
and every write to it is an authorized, audited event.

## Requirements

Python 3.8+ (standard library only). The templates are plain Markdown.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
