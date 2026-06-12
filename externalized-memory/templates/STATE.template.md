# <project> — state (the shared blackboard)

<!--
  STATE template — externalized-memory module, StrictLock.

  One per project. The single source of truth for cross-session state. An agent
  picking up the work READS this first; an agent finishing work WRITES it last,
  atomically (see ../atomic-write.py), and the close-out commit is the heartbeat —
  no commit, no "done".

  Keep it readable at a glance by an amnesiac: short rows, present tense. Completed
  items are REMOVED, not checked off — history lives in ARCHIVE.template.md, this
  file describes the present. This is NOT a mailbox and NOT an archive; keep those
  concerns in their own files or this becomes a junk drawer and stops being
  scannable.

  Replace every <bracketed> field. Structure and prose only — never paste real
  handoff data into a published copy of this template.
-->

## Meta
- last_session: <ISO-8601 timestamp the last session ended>
- agent: <which agent / role ran it>
- git_ref: <the commit ref this file was written against — the staleness tripwire>
- touched: <files the last session changed>

## Next Action
<The single first thing the next session does. Not the backlog — the next action.>

## Active Threads
<Open work items, one line each. Remove a row the moment its work is done — it
moves to ARCHIVE, it does not earn a checkmark here.>

- <thread-id> — <status> — <one-line summary>
- <thread-id> — <status> — <one-line summary>

## Decisions
<What was decided, when, and why — so no future session relitigates it. One line
each; the rationale is the point.>

- <YYYY-MM-DD> — <decision> — <one-line rationale>
- <YYYY-MM-DD> — <decision> — <one-line rationale>

## Constraints
<Known sharp edges, so no future session rediscovers them the hard way. One line
each.>

- <constraint / gotcha, and how to stay clear of it>
- <constraint / gotcha, and how to stay clear of it>
