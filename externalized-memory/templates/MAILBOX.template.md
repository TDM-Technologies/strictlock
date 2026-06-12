# <project> — mailbox (work-package handoff)

<!--
  MAILBOX template — externalized-memory module, StrictLock.

  A bidirectional channel for exactly ONE work package in flight between two
  sessions (for example, a planner that proposes and an executor that carries
  out). It is NOT the state file and NOT the archive: it holds one in-flight
  package, then is CLEARED back to the sentinel line the moment that package is
  consumed — so a stale package can never be silently re-run. Write it atomically
  (see ../atomic-write.py). Structure and prose only.

  Protocol:
    1. The sender replaces the sentinel line below with a Work Package and commits.
    2. The receiver reads it, acts, writes its Result back, and commits.
    3. Once both sides have taken what they need, the consumer CLEARS this file to
       exactly the sentinel line again. An empty mailbox is the resting state.

  The resting state of this file is exactly one sentinel line and nothing else:

      MAILBOX EMPTY — no work package in flight.

  Anything other than that single line means a package is in flight. Below is the
  shape that replaces the sentinel while one is.
-->

## Work Package  (sender -> receiver)
- id: <work-package id>
- from: <sender role / session>
- to: <receiver role / session>
- intent: <one line: what this package is for>

### Plan
<The exact steps / diffs / commands the receiver is asked to carry out. The
receiver executes what is written and improvises nothing.>

### Acceptance
<How the receiver knows the package is done.>

## Result  (receiver -> sender)
- status: <done | blocked | needs-decision>
- ref: <commit ref the work landed on>
- notes: <one-line outcome, or the blocking question that needs a human>
