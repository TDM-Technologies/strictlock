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
| [`DESIGN.md`](DESIGN.md) | The why behind every rule — each one is a scar. |
| [`examples/`](examples/) | Fictional, populated artifacts and a step-by-step walkthrough. |

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

## Requirements

Python 3.8+ (standard library only). The templates are plain Markdown.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
