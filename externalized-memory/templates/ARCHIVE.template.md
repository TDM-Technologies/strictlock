# <project> — archive (completed history)

<!--
  ARCHIVE template — externalized-memory module, StrictLock.

  Append-only history of work, decisions, and threads moved off the state file.
  The state file describes the PRESENT; the archive holds the PAST so the present
  stays readable at a glance. Newest first. Entries are immutable once written —
  you append, you do not edit a closed record (you cannot accrete addenda onto a
  record that refuses edits). Write atomically (see ../atomic-write.py). Structure
  and prose only.
-->

## <YYYY-MM-DD> — <completed thread or decision>
- ref: <commit ref where it landed>
- summary: <what was completed, one or two lines>
- outcome: <result, or a pointer to the artifact>

## <YYYY-MM-DD> — <completed thread or decision>
- ref: <commit ref where it landed>
- summary: <what was completed, one or two lines>
- outcome: <result, or a pointer to the artifact>
