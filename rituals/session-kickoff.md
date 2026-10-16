# Session kickoff checklist

**Open a unit of work the same way every time: verify the gates, reconcile the state file,
check the plan's premises, get the scope approved, then branch.**

A [rituals](README.md) checklist. It turns the start of a unit of work from "pattern-match from
memory" into an ordered walkthrough, so the state file doesn't accumulate drift across sessions
(the precedent: one kickoff had to reconcile *six* units of drift before it could plan anything).

Walk the steps **in order**. Don't skip any; if a step doesn't apply, say so out loud and move
on — "the inbox is empty" is a finding, "I didn't look" is a miss. Read the checklist in full at
the start rather than from memory, and read its [Lessons](#lessons) section too.

Conventions: `<state-file>` is your [externalized-memory](../externalized-memory/) blackboard;
`<plans-dir>` is your plan-gate plans directory; `<main>` is your trunk branch. Replace them
with your project's names.

A **work package (WP)** is one bounded unit of work with its own plan.

---

## Phase A — Session prep (read-only; no plan needed)

The gate permits read-only commands even with no active plan, so everything in this phase can
run *before* the plan exists. That ordering is the point: what you learn here decides the scope.

### 1. Read the state file

Open `<state-file>` and note:

- **Meta** — especially the `git_ref`. It is your reconciliation anchor for step 4.
- **Next Action** — what this session is supposed to open with.
- **Active Threads** — items that may have been closed by merges since the file was written
  (you resolve those in Phase B).
- **Carry-over** — anything still open from prior units.

- [ ] State file read in full, from the **right copy**.

> **Why this step exists.** The state file is the only memory a stateless session has.
> **Which copy** matters: when working in a git worktree, edit and read *the worktree's* copy,
> not the main checkout's — they can be on different branches and show wildly different
> content. A kickoff once diagnosed "eight units of drift" that was really one, because it read
> the main checkout's stale copy instead of its own worktree's. Note the worktree path from
> your environment at the start, and use worktree-rooted paths for every state-file read and
> edit. (The state file's staleness tripwire is described in
> [`externalized-memory/DESIGN.md`](../externalized-memory/DESIGN.md).)

### 2. Sweep the inbox / mailbox

If your setup has a mailbox for items dropped between sessions (see
[`externalized-memory/templates/MAILBOX.template.md`](../externalized-memory/templates/MAILBOX.template.md)):

- [ ] For each pending entry: note it and pick an unused thread id. The fold into Active
      Threads and the mailbox deletion happen in the kickoff commit (step 8), once a plan
      authorizes them (or now, if the state-file and mailbox directories are in
      `PLAN_GATE_ALWAYS_WRITABLE`).
- [ ] If it is empty, **say so explicitly** — that makes it clear the check wasn't skipped.

The kickoff commit (Phase B) lands both edits together.

### 3. Verify the gates are armed

- [ ] Every gate you rely on is **on**. In StrictLock each has its own switch: `PLAN_GATE=on`
      plus `PLAN_GATE_PLANS_DIR` ([`plan-gate/CONFIG.md`](../plan-gate/CONFIG.md)), and, if
      adopted, `GENERATED_SINK_COMMIT_GATE=on` / `GENERATED_SINK_PREPUSH_GATE=on`. Check each
      one; one switch does not cover the others. If your harness wraps them behind one switch,
      check that switch.
- [ ] If one is unset, that gate is inert and the protocol will not enforce. **Stop and ask the
      operator** before proceeding. Do not proceed on the assumption that "it's probably on."

> **Why this step exists.** An unarmed gate fails silently: nothing is denied, so nothing looks
> wrong. Know which hook a denial came from: the messages are tagged. Each integrity hook has
> its own uniquely named bypass, so one emergency escape hatch cannot silently disable the
> others.

### 4. Drift check — what shipped since the state file was written

- [ ] Compare the state file's `git_ref` to recent trunk history
      (`git log origin/<main> --oneline | head -20`). For each merge between that ref and HEAD:
      identify which unit shipped, what items it closed, and verify those closures are
      reflected in Active Threads and in the archive.
- [ ] **If you find 3+ units of drift, surface that first** — as the *first* thing the operator
      hears, before any plan-writing and not buried in a status report after the research is
      done. Drift compounds: each unresolved unit makes the next reconciliation harder. The
      operator can then decide whether to fix the pattern or muscle through the reconcile.
- [ ] **Local trunk sync and worktree count.** `git fetch origin <main>`, then compare your local
      `<main>` to `origin/<main>`. If local trunk is behind, note it. The non-destructive
      recovery (stash with a unique tag, check out trunk, `pull --ff-only`; never a
      `reset --hard`) needs those commands in the plan, so it happens in step 8. Glance at `git worktree list | wc -l`; if the count has crept into the
      dozens, flag a prune. **Surface; don't auto-prune** — deleting worktrees is the
      operator's call.
- [ ] **Stuck-sync sentinels.** If you run unattended hygiene that syncs trunk, and it writes a
      "sync refused" marker (parked on a feature branch; diverged; dirty tree; stale lock file),
      read the marker and surface it **verbatim** with the matching remedy. Don't auto-recover:
      the operator decides.
- [ ] **Closed items still presented as live.** Skim the state file's hand-written prose
      ("Next Action", carry-over lines) for any item id whose record says `closed`. That is the
      classic archive-move miss. *Propose* the fix as part of the kickoff reconcile in step 8;
      never edit beyond it. If you automate this as a reporter, treat "could not scan" as
      **not clean**.
- [ ] **Retrospective patterns.** Read the most recent sprint-end retro (see the cadence rule in
      [`session-close.md`](session-close.md) step 10), plus any per-unit retro the operator
      volunteered. A unit with no retro is expected, not a gap. Look for cross-unit patterns ("three of the last three units needed mid-flight `allowed_paths`
      additions" → scope harder this time) and surface them before planning.
- [ ] **Anchor estimates to prior units.** When you quote effort, name a specific similar unit
      that already shipped ("similar in shape to the one that shipped last Tuesday") and let the
      operator apply their own multiplier. Don't invent minutes and hours.

> **Why this step exists.** Drift is the recurring failure that costs the most cleanup time
> downstream, and nothing structural surfaces it until a human stumbles on it. The main checkout
> of one project rotted over a hundred commits behind before anyone noticed; worktree debt
> reached triple digits the same way. A read-only five-minute check at the start of every unit
> keeps recurrence visible while the cleanup is still cheap.

---

## Phase B — Planning checks, the plan, and the kickoff commit

The three checks below all fire **before the plan is locked**. They are the judgment-shaped
steps no gate can perform; each ends in a *finding with evidence* that goes into the approval
report (step 7).

### 5. Currency check on every item you're pulling in

For each backlog item in scope: overcome by events? Does the original driver still apply? Still
a good idea given recent state? Verdict: **still applies**, **partially absorbed** (re-scope),
or **OBE** (drop, and record why). Full rubric and template:
[`pre-wp-currency-check.md`](pre-wp-currency-check.md).

- [ ] Every scoped item has a verdict with evidence.

### 5b. Deferred-marker grep (continuation units only)

**Fires only if** this unit is step N of an arc, or continues a prior unit's code. Skip for
green-field units. Grep the *prior unit's shipped files* for `TODO`/`FIXME`/`XXX`, no-op
placeholder references, literal hand-off phrases, and this unit's own name. In-scope hit →
**widen the plan before locking**; out-of-scope hit → record as deferred; stale hit → ignore.
Full procedure: [`wp-interaction-analysis.md`](wp-interaction-analysis.md), lens 1.

- [ ] Run, or explicitly skipped as green-field.

> **Why this step exists.** The currency check catches "this item is stale." It cannot catch "the
> prior unit shipped code with a deferred-to-here obligation buried in a comment." A kickoff
> that missed exactly that — a placeholder reference left in the earlier step's code for the
> later step to consume — had to stop mid-unit and replan to widen `allowed_paths`.

### 5c. Peer-unit collision pass (grouped or concurrent units only)

When several units are scoped together or will run concurrently from sibling worktrees: shared
files, ordering constraints, mutual deferrals. Full procedure:
[`wp-interaction-analysis.md`](wp-interaction-analysis.md), lens 2. The shared-file case is also
backed structurally by plan-gate's cross-plan disjoint check; the other two are prose only.

- [ ] Run, or explicitly skipped (single unit, nothing else in flight).

### 6. Write the plan file

Author the plan in `<plans-dir>` with `allowed_paths` and `allowed_commands` frontmatter. The
mechanics — exact paths, narrow command prefixes, the worktree guard, the active-plan rule,
the flip-last lifecycle — live in [`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) and
[`SCHEMA.md`](../plan-gate/SCHEMA.md). **Read them; this checklist does not restate them.**
What follows is only the set of authoring lessons that earned their place at kickoff time:

- [ ] **Never list a plain `git push origin <main>`.** Production-bound merges go through
      your review/merge process, not the agent. An entry like `git push -u origin <branch>` with
      the literal branch is fine.
- [ ] **Keep every `git checkout` entry narrow.** Use `git checkout <main>` and
      `git checkout -b <branch>` — never bare `git checkout`, which, because the gate matches
      by prefix, also authorizes `git checkout -- .` (discard all uncommitted work).
- [ ] **A force-push entry must name the literal feature branch**, with the lease-and-includes
      form (`git push --force-with-lease --force-if-includes origin <branch>`), never a bare
      `git push --force`/`--force-with-lease` — by prefix match a bare entry would also
      authorize force-pushing trunk. Because a plan can't safely self-authorize a force-push,
      the human adds this line, or the plan author substitutes the concrete branch.
- [ ] **Don't list files that are already always-writable or that every unit touches.** Your
      agent-memory directory and plans directory can be exempted from gating with
      `PLAN_GATE_ALWAYS_WRITABLE`; listing a shared index file *as well* is unnecessary and
      guarantees a disjoint-paths collision with any peer plan that also lists it. Do list any
      state-file entry that only this unit writes (e.g. a per-unit record file).
- [ ] **If the state file is a single shared file that every unit's kickoff and close-out
      writes**, decide the single-writer convention up front. One pattern: list the state file
      **twice**, once worktree-anchored (authorizes your writes) and once as the main
      checkout's absolute path (a collision key: plan-gate's disjoint check compares other
      plans' absolute entries, so a second concurrent plan listing the same key is denied). If
      you are denied, either drop the key entry and skip the state-file refresh (a catch-up
      commit reconciles the batch later), or wait and run sequentially. Don't discover this at
      close-out.
- [ ] **The worktree anchor.** When working in a worktree, at least one `allowed_paths` entry
      must resolve under it; repo-root-relative paths do this naturally. Main-checkout absolute
      paths in a worktree session trip the silent-drift guard.
- [ ] **Keep gate-compared scalars bare.** plan-gate strips trailing ` # comments`
      from scalars and list items, but a different parser may not: for any scalar compared
      with `==` (`status`, `worktree_bypass`), put the rationale on its own comment line, never
      trailing the value.
- [ ] **Frontmatter must start at byte 0.** A plan file whose first line is anything other than
      `---` has no parsed frontmatter and is never active, whatever `status: active` strings
      appear in its body (e.g. inside a fenced documentation example). To find which plans are
      active, mimic the gate's top-of-file parser — don't `grep "^status: active"` the
      directory, which gives false positives.
- [ ] **Include the commands the close-out needs**, not just the work: `git add`, `git commit`,
      the push, and whatever opens the PR — and remember the flip-last rule (below).

### 6b. Review-depth decision

Decide how deep the review of this unit's *result* must be, and put it in the plan, not in
a late afterthought. For a unit that **reviews or changes a high-blast-radius artifact** — a
spec feeding a build, a security gate, a hook, anything whose error is hard to undo — scope the
security lens as **two stages**: an adversarial first pass, then an independent second pass, in
a **separate context, explicitly told to refute** the first (confirm / refute / re-rate each
finding, plus a false-negative sweep). Single-pass is fine for low-stakes or purely local work.
Run the second stage even when the first looks airtight.

If you automate this decision (for example by scoring the plan's paths for security
sensitivity, churn, and size), treat the score as a **floor, not a ceiling**, and report the
resulting yes/no rather than the raw score.

- [ ] Review depth decided and written into the plan.

> **Why this step exists.** In a recorded instance, a first-stage reviewer mistook an
> illustrative "e.g." recipe in a spec for the locked rule and raised a high-severity finding
> that would have escalated a non-problem to the operator. The independent second stage, told
> to refute, caught the misreading, re-rated the finding, redirected attention to the
> actually-correct issue, and found three false negatives a single pass missed. The verification
> stage is what stops a wrong "high" from ever reaching the human.

### 6c. Fresh-worktree check

- [ ] If the plan assumes installed dependencies and the worktree is fresh (no
      `node_modules`-equivalent), call it out in the approval report ("fresh worktree; install
      needed before tests; roughly N seconds"). Put the install commands in the narrow
      `allowed_commands` so the unit isn't gated, and so the cost is not a surprise mid-unit.

### 7. Get explicit approval

Show the operator:

- [ ] the unit's scope in one line,
- [ ] `allowed_paths` (the files this unit will touch),
- [ ] `allowed_commands` (the non-read-only commands it needs),
- [ ] the **currency verdicts** and any **deferred-marker** / **collision** findings from 5-5c,
      with evidence,
- [ ] the **review-depth decision** from 6b, as a plain yes/no,
- [ ] anything from 6c.

Wait for an explicit "yes / go / approved". Don't pattern-match an approval from earlier in the
conversation; ask once, explicitly. If the operator adjusts scope, update the plan file before
continuing.

### 8. Branch and the kickoff commit

Branch naming that keeps the history legible: `feat/<unit-name>` for code, `chore/<unit-name>`
for non-code, `docs/<unit-name>` for doc-only.

```
git checkout <main>
git pull --ff-only origin <main>
git checkout -b <branch>
```

The **first commit on the new branch** reconciles the state file, *before any feature work*:

- [ ] Acknowledge that the prior unit shipped (its merge sha becomes the new `git_ref`).
- [ ] Reframe Next Action around this unit's priorities.
- [ ] Flip any item closed by prior merges to archived — move it from Active Threads to the
      archive **in the same commit**.
- [ ] Fold in anything swept from the mailbox (step 2).
- [ ] Update the touched-files line.
- [ ] If your state file has **generated regions** (projected from per-record files), do not
      hand-edit them: change the source records, regenerate, and commit both together, or the
      generated-sink gate will refuse the commit. See
      [`externalized-memory/PROJECTION-SCHEMA.md`](../externalized-memory/PROJECTION-SCHEMA.md)
      and [`generated-sink-commit-gate`](../generated-sink-commit-gate/).

If the prior unit **hasn't merged yet** (its close-out commit landed but isn't on trunk), leave
the state file alone: the prior close-out commit already reflects the correct "code-complete,
awaiting merge" state. The new branch can still be created, but there is nothing to reconcile.

**Commit message.** The [`commit-msg-gate`](../commit-msg-gate/) accepts either a ceremony
prefix on the subject line or a `plan: <slug>` line in the body. A kickoff commit carries no
feature work, so the ceremony prefix is the natural form:

```
chore(kickoff): <unit> — opening <what> after <prior unit>

- Reconciled state file: prior unit merged at <sha>, <date>
- <item> closed earlier; <item> framing in Next Action
- Inbox swept (1 entry -> <thread id>)
- Files touched: <state-file>, <archive-file>
```

The gate's default prefixes already accept `chore(kickoff):` (a `chore` with any scope). If you
narrowed the list with `COMMIT_MSG_GATE_PREFIXES`, make sure your ceremony prefixes are still
in it.

### 8b. Claim the paths (fleets only)

If this unit runs alongside other agents in sibling worktrees, claim its path set exclusively
now that the branch exists: [`scope-lease`](../scope-lease/) reads the same active plan's
`allowed_paths` and takes an exclusive lease. Release it at close-out (see
[`session-close.md`](session-close.md)). This step is advisory when you run one agent at a time.
A lease expires at `SCOPE_LEASE_TTL` (default 30 min); re-run `scope-lease.py acquire` during
long work to extend it (re-acquiring your own lock refreshes the deadline), and run
`scope-lease.py fence-check` just before you hand off for merge (see
[`session-close.md`](session-close.md)). If you run [`liveness-scan`](../liveness-scan/), it
does not read the lease; it classifies this session from its active plan plus a heartbeat file
(`LIVENESS_SCAN_HEARTBEAT_RELPATH`) or commit mtime. Touch the heartbeat file alongside the
acquire so it reports this session as running.

### 9. Push the branch

```
git push -u origin <branch>
```

This unblocks preview builds and CI for downstream smoke checks. Phases A and B are now
complete; feature work follows.

---

## When NOT to use this checklist

- **Mid-unit commits.** Only the *first* commit on a new branch gets the kickoff treatment.
- **Close-out commits.** Use [`session-close.md`](session-close.md) instead — different
  protocol.
- **Prior unit hasn't merged.** Leave the state file alone (step 8); no reconciliation.
- **A genuinely tiny unit** (one or two files, one feature commit, no items closed): the kickoff
  reconcile *is* the close-out reconcile. Don't author a redundant close-out commit, and don't
  frame its absence as a process violation. If it touches three or more files, has two or more
  feature commits, closes any item, or introduces any decision or constraint, the normal
  multi-commit protocol applies.

---

## Lessons

Distilled from real kickoff runs. Newest is most authoritative if two conflict. Prune what
turns out wrong; fold what proves durable into the steps above.

- **Read-only commands work with no plan.** The gate's read-only allowance is evaluated before
  the active-plan check, so Phase A flows naturally (read state → drift-check → propose scope →
  write plan → activate → kickoff commit). Before that reorder, a kickoff had to either
  activate a plan first (chicken-and-egg with scope decisions that depend on what shipped) or
  toggle an emergency bypass for the read commands. If a command you expect to be read-only is
  denied, work around it and report it at close-out as a candidate for the read-only list.
- **Surface 3+ units of drift first** (step 4), not after the research is done.
- **Worktree copy, not main copy** (step 1).
- **Frontmatter parsing is not a whole-file grep** (step 6). A near-miss: a kickoff almost spent
  effort "archiving a stale second active plan" that did not exist; the supposed second
  `status: active` lived inside a fenced example in a file with no byte-0 frontmatter.
- **Keep checkout narrow, never bare** (step 6). A deny log counted three `git checkout` denials
  across three units in a day because the plan template omitted `git checkout -b` entirely;
  the fix was the two narrow forms, not a blanket entry that would widen into a data-loss
  authorization.
- **Don't list always-writable or universally touched files** (step 6). A concurrent-session
  test collided on a shared index file that every unit lists at close-out; a third peer became
  active mid-close-out and the disjoint check denied the close-out write. The deferred-add
  workaround was the wrong abstraction; the right answer is "never list it."
- **Two-stage review for high-blast-radius reviews** (step 6b).
- **Generated regions: change the source and regenerate** (step 8). A hand-edit is lost on the
  next regenerate and refused at commit time by the sink gate. If a commit is denied by an
  integrity hook, fix the staleness — don't toggle the bypass for routine drift.
- **A hook denial isn't always the plan gate.** Check which hook produced the message before
  editing the plan.
- **Heartbeat age vs lease expiry are different things.** A liveness heartbeat that ages past
  its timeout only makes a report-only scanner say "stalled", which is harmless. A scope-lease
  that passes its deadline can be reclaimed by a peer, which is not harmless: extend it, and
  fence-check before merge.
