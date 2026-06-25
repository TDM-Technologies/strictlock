# externalized-memory examples

Concrete, fictional artifacts showing the pattern end-to-end:

- [`STATE.example.md`](STATE.example.md) — a populated shared blackboard for a toy
  `todo-api` project.
- [`MAILBOX.example.md`](MAILBOX.example.md) — one work package in flight, plus the
  sentinel line the file is cleared to afterward.
- [`PROJECTION.README.md`](PROJECTION.README.md) — the **projection bundle** walkthrough:
  a [`projection-records/`](projection-records/) directory rendered deterministically into
  the fenced region of [`PROJECTION.example.md`](PROJECTION.example.md).

Everything below uses plain files and one short script. No daemon, no service.

## 1. Adopt the templates

Pick a state directory inside your project and copy the templates in, dropping the
`.template` marker:

```bash
mkdir -p ~/project/.state
cp ../templates/STATE.template.md   ~/project/.state/STATE.md
cp ../templates/MAILBOX.template.md ~/project/.state/MAILBOX.md
cp ../templates/ARCHIVE.template.md ~/project/.state/ARCHIVE.md
```

Fill `STATE.md` in as you work. Keep it to the shape in
[`STATE.example.md`](STATE.example.md): short rows, present tense, completed items
removed (they move to `ARCHIVE.md`).

## 2. Start every session by distrusting your own memory

The state file records the `git_ref` it was written against. Open each session by
comparing that ref to what git actually shows — the **staleness tripwire**:

```bash
git log --oneline -3            # what git says is true now
grep git_ref ~/project/.state/STATE.md   # what the blackboard last recorded
```

If they disagree, the file may be stale (or a read was cached); re-verify before
trusting anything it says. Git is the ground truth, not the file.

## 3. Update the state file atomically

Never rewrite the blackboard in place — a crash mid-write leaves a half-file that
still looks authoritative. Stage the new content and let
[`atomic-write.py`](../atomic-write.py) do the temp-then-rename:

```bash
# Build the new STATE.md however you like, then commit it atomically:
your_state_generator | python ../atomic-write.py ~/project/.state/STATE.md
```

The write either lands completely or fails loudly with a non-zero exit — there is
no torso, and a failure never litters a half-written sibling.

## 4. Pass a work package through the mailbox

The mailbox carries exactly one package between two sessions. The sender writes a
Work Package; the receiver acts and writes its Result; then the file is cleared to
the sentinel line so the stale package can't be re-run:

```bash
# After both sides are done with the package:
printf 'MAILBOX EMPTY — no work package in flight.\n' \
  | python ../atomic-write.py ~/project/.state/MAILBOX.md
```

## Better together: pair it with the gate

The template stands alone — adopt it without any other StrictLock module. But it is
stronger paired with [`plan-gate`](../../plan-gate/): list your state files in the
plan's `allowed_paths` so only an approved session may write them, and let the gate
log every write. Now the blackboard isn't just durable — each change to it is an
authorized, audited event. See [`../DESIGN.md`](../DESIGN.md) for why that turns
state into evidence rather than testimony.
