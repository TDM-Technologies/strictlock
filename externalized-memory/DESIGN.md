# externalized-memory — design notes

The why behind every rule. The pattern is simple; keeping it *trustworthy* was not.
Each rule below earns its place by being the fix to a specific failure — every one
is a scar. This document is the design write-up the [field
report](https://downsmullen.com/externalized-memory.html) and the StrictLock paper's
[§2.1](../plan-gate/paper.md) point at.

## The problem: a memory of a memory

An LLM agent is functionally stateless across sessions. Its working memory is the
context window — ephemeral, bounded, and gone the moment the session ends or
crashes. The next session is a different agent that knows *nothing*: not "needs a
refresher," nothing.

Most people patch this with vibes — paste yesterday's chat into today's prompt,
trust the agent's summary of what it did, or keep one heroic never-ending
conversation alive until it degrades into soup. All three fail the same way: if the
only record of *what was approved* and *what has been done* lives in a context
window, your project state is as durable as a chat log. An approval that existed in
a since-evicted window is a memory of a memory. You cannot audit that.

## The pattern: the shared blackboard

The fix is old enough to have a name from 1980s AI research — the **shared
blackboard**. One on-disk state file per project, the single source of truth for
cross-session state. Every project gets exactly one. It carries five things, and no
more:

- **Meta** — when the last session ran, which agent ran it, the git ref it left
  behind, and the files it touched.
- **Next Action** — what the next session does *first*. Not the backlog; the next
  action.
- **Active Threads** — open work items, each one line.
- **Decisions** — what was decided, when, and the rationale, so no future session
  relitigates it.
- **Constraints** — the known sharp edges, so no future session rediscovers them.

Any agent picking up the work reads the blackboard first. Any agent finishing work
writes it last, and **the close-out commit is the heartbeat** — no commit, no
"done."

## Rule: one job per file

The state file is **not** a mailbox and **not** an archive. Those are separate files
with one job each: a *mailbox* carries one in-flight work package between sessions
and is cleared to a sentinel line after use; an *archive* holds completed history.

The scar: the first time those concerns shared one file, the state file became a
junk drawer and stopped being readable at a glance — which, for a file whose entire
job is to be readable at a glance by an amnesiac, is death. Three files, three jobs.

## Rule: git is the ground truth — open with a staleness tripwire

The state file is the source of truth for *state*, but it is not the source of truth
for *reality*. **Git is.**

The scar: a runtime that read files through a virtualization layer had a cache that
drifted. For weeks there were sessions where the state file said one thing and
`git show HEAD` said another — stale reads serving content overwritten days earlier,
and phantom writes that reported success while putting nothing on disk.

So every session opens with a **staleness tripwire**: read what git actually shows
(`git log --oneline -3`) and compare it to the git ref recorded in the state file's
own Meta block. If they disagree, every cached read this session is suspect and gets
re-verified. The blackboard carries its own freshness check, because "I just read
the file" is not evidence the file is what's on disk.

## Rule: completed items are removed, not checked off

The scar: early on, completed items got a satisfying checkmark and stayed put.
Within weeks the state file was mostly a graveyard of past wins that every new
session had to wade through to find the live work.

The rule now: **completed items are removed, not checked off.** History lives in the
archive; the blackboard describes the present. Long-lived items had a subtler
version of the same disease — status rows quietly accreting addenda until single
rows ran to paragraphs — and the structural fix is that closed records are
**immutable**: you append to the archive, you never edit a closed record. You can't
accrete addenda onto a record that physically refuses edits.

## Rule: atomic writes (write-temp-then-rename)

A blackboard is only as trustworthy as its worst write. A crash halfway through
rewriting the state file in place leaves you with half a state file — which is worse
than a stale one, because it still looks authoritative.

So writes are atomic: **write to a temp file in the same directory, fsync, then
rename over the target.** The rename either happens or it doesn't; there is no half.
A reader sees either the complete old file or the complete new one. This is the one
rule with code attached — [`atomic-write.py`](atomic-write.py) is the reference, and
its tests pin the behavior: full replacement, exact bytes, no leftover temp, and a
loud failure when the write can't complete.

## Rule: single-writer claims

The scar: once sessions ran concurrently in parallel git worktrees, two would both
want to update the same state file at close-out.

The fix is a **single-writer convention**: each unit of work declares a claim on the
state file up front, a validator denies any second concurrent claim, and first mover
wins. The losing session doesn't fight — it folds its update into the next session's
reconcile step. Boring, deterministic, no merge archaeology. (This is the same
disjoint-claim posture [`plan-gate`](../plan-gate/) takes on `allowed_paths` across
worktrees — list the state file in exactly one active plan at a time.)

## Rule: no silent failure

The favorite scar. A crashed process left behind a zero-byte lock file. The hourly
job that kept local state fresh respected the lock — and silently no-op'd for
sixteen hours straight. Fifteen consecutive runs, each reporting nothing, because
technically nothing was wrong.

Two rules came out of that. First, **self-heal the known case**: a lock file older
than a threshold gets *moved aside* to a lost-and-found, never deleted — the recovery
mechanism must not be capable of destroying evidence. Second, for everything else,
**no silent failure**: when the work can't proceed, it drops a reason-tagged sentinel
that the next session must surface verbatim. The system is allowed to be stuck. It is
not allowed to be *quietly* stuck. (This is why `atomic-write.py` refuses a missing
parent directory with a non-zero exit instead of silently creating one — a surprising
success is a silent failure wearing a smile.)

## Why this is governance, not housekeeping

Everything above sounds like ops hygiene. Here's why it's the load-bearing wall.

State that lives outside the model can be inspected, version-controlled, diffed, and
replayed. An approval recorded on the blackboard and committed is an **artifact** —
evidence with a timestamp and a hash. An approval that lived in a context window is
**testimony from a witness who no longer exists.**

StrictLock moves agent governance out of prose and into a fail-closed gate at the
tool boundary; the gate checks every consequential action against an approved plan.
But "approved" only means something because the plan, the state, and the close-out
all live on disk, in git, where an auditor — or just you, three weeks later — can
trace what was authorized, what ran, and what changed. The audit trail isn't a
feature you build; it's exhaust from the pattern working. Externalized memory is what
the gate stands on.

## The takeaway

Treat agent memory as infrastructure, because that's what it is. Externalize the
state: one file per project, one job per file. Make git the ground truth and make
every session start by distrusting its own memory. Write atomically, claim
exclusively, and when something wedges, make it wedge **loudly**.

The agent is stateless. The work doesn't have to be.
