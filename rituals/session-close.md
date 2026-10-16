# Session close-out checklist

**End a unit of work the same way every time: reconcile the state file in the same commit,
archive what closed, capture what spilled over, read the gate logs, push, and retire the plan
last.**

A [rituals](README.md) checklist. It is the mirror of [`session-kickoff.md`](session-kickoff.md).
It turns the close-out protocol into an enforced walkthrough so the recurring **archive-move
miss** stops happening: an item closed in the work but left in the "open" table, discovered a
session or three later when the next kickoff has to clean it up.

Walk the steps in order. Don't skip any; if a step doesn't apply, **say so out loud** and
continue ("this unit closed no items" is a finding; silence is a miss). Read the checklist in
full at the start, and read its [Lessons](#lessons) section.

Conventions: `<state-file>` is your [externalized-memory](../externalized-memory/) blackboard,
`<archive-file>` its append-only archive, `<plans-dir>` your plan-gate plans directory,
`<main>` your trunk branch.

---

## Before the commit

### 1. The plan is still `status: active`

- [ ] Open the plan file in `<plans-dir>`. Its frontmatter must still say `status: active`.

If it has already been flipped, the close-out commit will be denied — the gate needs an active
plan to authorize the state-file writes. The flip happens **after** the work lands, never
before. This is covered at the end of the checklist (step 12) and in
[`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) §5; the check is here too because the
mistake is made at this point.

### 2. State-file Meta refreshed

- [ ] `last_session` — today's date plus a brief summary of what shipped.
- [ ] `agent` — which agent or role ran it.
- [ ] `git_ref` — the close-out commit's sha. You can't know your own commit sha before you
      make it: write a placeholder (e.g. `TBD — close-out`) and correct it after the commit
      lands, or leave the field to be filled by the **next** kickoff from the merge sha
      (a unit can never know its own merge sha).
- [ ] `touched` — the files this unit changed.

> **Why this step exists.** Reconciling the state file *in the close-out commit* is the point,
> not "I'll do that at the next kickoff". Several units once skipped the refresh and deferred it
> to the next kickoff, which often missed it too; drift compounded. A freshness hook that
> compares the state file's `git_ref` to trunk now refuses a stale reconcile. When it blocks
> you, the fix is to do the work, not bypass the hook.

> **If your Meta and Next Action are generated** (projected from per-unit record files), don't
> hand-edit them. Flip *this unit's* record to its "code-complete" status, fill in the PR
> reference and a forward-looking next action, regenerate, and stage the record and the
> regenerated state file together. Hand-edits to a generated region are overwritten and refused
> by the sink gate. See
> [`externalized-memory/PROJECTION-SCHEMA.md`](../externalized-memory/PROJECTION-SCHEMA.md) and
> [`generated-sink-commit-gate`](../generated-sink-commit-gate/). A transitional note: if your
> freshness hook only parses an *older* state-file shape, use a ceremony subject it accepts
> (`docs:` or a `plan:` body line) until the hook's fix lands.

### 3. Next Action refreshed

- [ ] Reframe Next Action around what comes **after** this unit merges. The common pattern:
      smoke-and-merge for this unit, then kickoff for the next backlog item.

### 4. Active Threads — close and archive **in this commit** (the load-bearing step)

Any item this unit closes must be **(a)** flipped from pending to closed **and (b)** moved to
`<archive-file>` — **both in this same commit.** Not next session, not "I'll get to it." This is
the most recurring miss; treat it as load-bearing.

For each item closed by this unit:

- [ ] Confirm it is listed in the state file (Active Threads or Carry-over).
- [ ] Add a row to the archive: `| <id> | <date> | <close reason> |`.
- [ ] Remove the row from Active Threads.
- [ ] Update Carry-over or Backlog wherever the item is referenced.
- [ ] If the unit closed **no** items, say so explicitly.

### 4a. Addendum hygiene — extract before commit

Active Threads rows hold **latest state only**. For each row this unit *touched* (added text to,
even if the item didn't fully close):

1. Re-read the row as it now stands.
2. Decide what a session reading it **cold** needs: current state plus the forward ask = keep;
   open design questions on an unscoped item = keep (they are the scoping context);
   closed-half retros, dated notes, inlined data inventories, multi-paragraph rationale,
   origin-story paragraphs = **extract**.
3. Append the extracted text verbatim to `<archive-file>` under a dated section, and replace it
   in the state file with a one-line `<latest state> -> <archive-file> section <date>` pointer.
4. Verify by re-reading both: the row should scan as one concise paragraph, and the archive
   section should hold the full preserved text.

- [ ] Done, or "this unit touched no rows."

> **Why this step exists.** Step 4 catches "item closed -> move to archive". Historically the
> process missed the symmetric case, "addenda accumulated -> extract to archive". One state
> file grew to 58 KB with 62% of it in nine fat rows. Both are archive moves; this makes the
> second explicit. (Where your items are persisted as structured records with an immutable
> `closed` status, the extraction can't happen by construction and this step is superseded for
> them. It remains load-bearing for any hand-maintained prose table.)

### 4b. Shared-state-file claim — did this unit claim the convention or get denied?

If several concurrent units write one shared state file, the kickoff set a single-writer
convention (see [`session-kickoff.md`](session-kickoff.md) step 6). Close-out depends on how
that went:

1. **Claimed (first-mover or solo).** No collision at kickoff. Proceed with steps 2-4 normally.
   The common case.
2. **Denied, and you dropped the shared-file entry to proceed.** You cannot write the shared
   state file. **Skip steps 2 and 3, and the state-file half of step 4.** A catch-up commit run
   after the whole concurrent batch merges reconciles the Meta for the window, and rolls into
   the next kickoff's reconcile. Your item closures still go into their own records and any
   decisions you raised still land in their normal places.
3. **Denied, and you waited (sequential).** You became first-mover once the earlier plan merged.
   Proceed as in case 1.

- [ ] Case identified. (Deterministic check: if your active plan lists both the
      worktree-anchored path and the main-checkout path of the shared state file, you claimed
      it; if only the worktree-anchored one, you did not.)

### 5. New decisions, constraints, and threads logged

- [ ] If this unit introduced any new decision, constraint, or thread, confirm it is in the right
      state-file table. Ids must be unique: find the prior max for each prefix and increment.

### 5b. Spillover capture and routing — silent, two passes

Before the commit, sweep the session for **out-of-scope spillover** and route each item. Do it
silently — no per-unit prompt to the operator (see the cadence note at step 10).

Route each captured item:

- **Belongs to this project** -> a new thread in the state file (rides the step-5 authorization).
- **Not this project's / cross-project / not sure** -> your outbound chute: one new file per
  item in a designated folder, written so it rides this close-out commit. It is an *outbound
  chute*, not a second backlog. If the capture is done by a helper script, have the plan
  authorize the **command** rather than a path — the file write then happens inside the script's
  process and the gate sees only the authorized command.

Two passes, both capturing **agent-flagged** items, not only operator-named ones:

1. **Items the operator named this session** — "put that in the backlog / remember this /
   that's outside this."
2. **The raised-but-unrecorded sweep.** Scan for things *you yourself* surfaced that were never
   recorded or acted on: "two things to flag...", "by the way...", "worth noting...", deferred
   suggestions, implied-but-unrecorded decisions. One line each, with enough context to act on
   cold.

> **Pass 2 is best-effort introspection, not a structural guarantee.** Unlike the log reviews in
> steps 10a and 10b, which replay append-only files, pass 2 reads the *conversation itself* and
> fails open: if context was summarized, the evaporated items are exactly the ones a summary
> already dropped. It improves recall; it does not promise nothing is missed. A structural
> log-at-flag-time version is the real fix.

- [ ] Done, or "no spillover this unit."

### 6. Commit message check

The [`commit-msg-gate`](../commit-msg-gate/) accepts either a `plan: <slug>` line in the body or
an approved ceremony prefix on the subject. Close-out commits usually use a ceremony prefix:

```
chore(handoff): <unit> close-out — <one-line summary>

- 3 critical paths covered: ...
- <item> closed (archived to <archive-file>)
- <anything validated end to end>
```

The gate's default prefixes already accept `chore(handoff):`, `chore(kickoff):` and `docs:`
(a `chore` with any scope). If you narrowed the list with `COMMIT_MSG_GATE_PREFIXES`, make sure
your ceremony prefixes are still in it.

- [ ] Subject or body satisfies the gate.

### 7. Final pre-commit read-through

Re-read `<state-file>` and `<archive-file>` once before committing. Confirm:

- [ ] Meta reflects this unit.
- [ ] Next Action is forward-looking.
- [ ] Every closed item is in the archive and removed from Active Threads.
- [ ] New decisions / constraints / threads are in the right tables.
- [ ] The plan is still `status: active`.

---

## After the commit lands

### 8. Push the branch and open the PR

```
git push -u origin <branch>
<open the PR with your PR tool>
```

The agent opens the PR; a human merges it through your normal review process.

### 8b. If trunk advanced under you — self-heal a generated-sink conflict

If `origin/<main>` advanced and the PR shows a conflict on a **generated sink** (the common
concurrent-units case: an earlier PR merged its own regenerated state file), don't ask the
operator to resolve it and don't hand-merge it. Use [`sink-resolver`](../sink-resolver/): it
regenerates from the merged sources, byte-checks the result, and **escalates any conflict that
isn't purely the sink**. Then act on the **outcome token it prints, never its exit code**
(a resolver can always exit 0, so the exit status says nothing):

| Outcome | What to do |
|---|---|
| **resolved** | The sink conflict was auto-resolved and byte-verified. Re-push the rewritten branch, then re-check mergeability. If it *still* reports a conflict (trunk advanced again between your rebase and your push — the second-merger case), re-run this step. |
| **nothing to resolve** | The rebase cleared it locally. **A clean worktree is not the same as a mergeable PR** — re-push (the branch may have been rebased) and re-confirm mergeability. |
| **plan-only** | You ran a dry run; the apply didn't take. Re-run with apply. Do **not** push. |
| **any escalation** | A real non-sink conflict, a failed verify, a tripped circuit breaker. **Surface to the operator. Do not force-push. Do not retry in a loop** — a failed verify won't self-clear, and a tripped breaker can leave the worktree staged but not finalized, so you may need `git rebase --abort` to back out. The judgment is the human's. |

**Force-push safety and the gate.** The re-push rewrites history, so it needs the narrow
`git push --force-with-lease --force-if-includes origin <branch>` form: the lease refuses if your
remote-tracking ref doesn't match origin, and the includes check additionally refuses if a
background fetch advanced a ref you never integrated. It targets the **feature branch only,
never trunk**. If the gate denies it because the plan lacks that line, **surface the
rebased-branch state to the operator** — don't toggle a bypass and don't edit the gate.

**The plan stays `status: active` through this step.** The flip is the last step (12). Flipping
any earlier blocks the gate from authorizing the push or the PR.

### 9. Hand-off line

- [ ] Send the hand-off line now: "`<unit>` close-out commit landed at `<sha>` on `<branch>`.
      Smoke against `<preview>`, then merge via your normal process." Nothing waits in front
      of it.

### 10. Retrospective — at the sprint boundary, never per unit

**Do not prompt for a retro at an individual unit's close-out**, whatever its size or whether it
closed items. Ask the retro question **only at a sprint-end close-out**.

- At per-unit close-out: skip the prompt entirely. If the operator *volunteers* a retro, record
  it in the unit's close-out notes — the write side survives, only the prompt is removed.
- At sprint end: first do a **cross-unit pattern read** — skim the sprint's deny-log and
  test-guard lines (recorded silently in steps 10a and 10b) plus any volunteered retros, and pull
  out one or two recurring patterns (a deny cluster, a repeated friction, a test-coupling shape).
  Then ask once, scoped to the sprint: **"Sprint wrap — (1) what surprised you across this
  sprint, (2) what's working that we should deliberately keep, and (3) what would you change?"**
  Ground it with the patterns so the retro evaluates *impact* rather than re-enumerating what
  the gate blocked. The next kickoff reads the sprint-end retro for pattern surfacing.

> **Why this step is shaped this way.** A per-unit retro prompt is friction that reads as
> interrogation, not signal; in the run that produced this rule, it fired on a unit that had
> only closed its own self-raised item and drew an immediate "why did it fire now?". The wrong
> premise was the *cadence*, not the question. Batching to sprint boundaries matches how
> attention actually works and keeps the question valuable. Earlier versions of this checklist
> prompted on every unit, then carved out small ones, then dropped per-unit prompts entirely;
> only the last is the rule.

### 10a. Gate decision log — record per unit silently, surface clusters at sprint end

Same no-per-unit-interrogation rule as step 10.

1. Read [`plan-gate`](../plan-gate/)'s decision log (`plan-gate-decisions.log` in
   `PLAN_GATE_LOG_DIR`; the entries since the prior close-out, or the last 20 rows). Verify the
   chain with `plan-gate.py verify-log` if the log is going to be cited as evidence.
2. **Record** the unit's denials with a one-line classification (**one-off** vs **cluster**) in
   the unit's close-out notes — silently.
3. **Surface accumulated clusters at sprint end only.** If the same safe-by-design command was
   blocked across multiple units -> propose extending the gate's read-only list. If a
   plan-template gap recurred -> propose a template fix. One-offs stay recorded but unsurfaced:
   the gate did its job. If a cluster warrants a tooling change, **queue it as a follow-up** —
   don't fix it mid-close-out.

- [ ] Recorded, or "the gate logged zero denials this unit."

### 10b. Test-protection log — record per unit silently, surface flagged events at sprint end

[`test-protection-guard`](../test-protection-guard/) appends a row whenever a commit changed or
removed an **existing** test assertion alongside a source edit. A `flagged` row is the coupling
**without** a `TEST-CORRECTNESS:` justification — the "fixed the test, not the code" shape. A
`justified` row is the same coupling, justified up front. **This log is the actual enforcement
surface**: the guard is advisory (it never blocks) and the justification token is
self-administered by whoever wrote the commit, so both are bypassable by the agent they
constrain. The human's review of this log is where the teeth are.

1. Read the log (entries since the prior close-out, or the last 20 rows). If the file is absent,
   no coupling events fired — say so.
2. **Record** this unit's rows silently, with the count of `flagged` vs `justified`.
3. **A flagged row is a substantive signal, not a retro question.** The guard already warned
   live at commit time, so don't re-interrogate — but if the unit produced any, state it as a
   recorded fact in the hand-off line ("N flagged test-coupling event(s) this unit, logged for
   sprint-end review") so it isn't invisible.
4. **At sprint end** walk the accumulated flagged rows: were they genuine test-correctness fixes
   that merely lacked a token, or actual code-gaming that should be reverted and re-fixed? A
   recurring pattern (same file, same kind of assertion churn) is a candidate for a follow-up.
   Don't act on flagged rows mid-close-out: surface, record, defer.

- [ ] Recorded, or "no coupling events."

### 11. Confirm the local close-out artifacts exist

Before the plan flip, confirm the unit's close-out notes carry the gate-log lines (10a, 10b) and
any retro the operator volunteered. No prompt here.

### 11b. Release the path claim

- [ ] If you claimed paths at kickoff with [`scope-lease`](../scope-lease/), release them now so
      a fleet scanner no longer counts the session as live. Release is idempotent; releasing a
      claim that was never taken, or already released, is a no-op. Do this **before** step 12:
      the flip-last discipline wants nothing left for the gate to authorize after it.

### 12. Flip the plan to `status: executed` — the LAST step

Now, and only now, open the plan file and change `status: active` to `status: executed`. The
flip is the genuinely final keystroke of the unit of work. Flipping earlier de-authorizes
whatever you scheduled after it: a pre-commit flip blocks the commit; a post-commit,
pre-push flip blocks the push; a pre-PR flip blocks opening the PR; any of them blocks the
post-commit fixups the operator may ask for during smoke.

> **Mnemonic.** If you are about to flip and can think of *any* operation the operator might
> still need the gate to authorize, you are flipping too early. This is
> [`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) §5 ("never archive first") applied to
> close-out.

- [ ] Flipped, last.

---

## When NOT to use this checklist

- **Kickoff commits.** Use [`session-kickoff.md`](session-kickoff.md).
- **Mid-unit commits.** Only the final commit of a unit gets the close-out treatment; a mid-unit
  commit needs no state-file update.
- **A genuinely tiny unit** (one or two files, one feature commit, no item or decision closed):
  the kickoff's state-file reconcile already covers everything a separate close-out commit would
  do, so don't author a redundant one and don't apologize for its absence. The branch is just
  `[kickoff, feature]`: push, smoke, merge. The local-only close-out work (close-out notes,
  plan flip) still happens after the merge, and the next kickoff reconciles the new trunk sha.
  **Calibration:** if the unit touches three or more files, has two or more feature commits,
  closes any item, or introduces any decision or constraint, the multi-commit close-out applies
  normally.
- **Non-tiny units: author the close-out commit on the branch *before* handing off for merge**,
  so the reconcile rides the unit's own PR. A separate post-merge housekeeping PR is an
  explicitly rejected anti-pattern: the reconcile slips to the next kickoff, which then has to
  span several merges.

---

## Lessons

Distilled from real close-out runs. Newest is most authoritative if two conflict. Prune what
turns out wrong; fold what proves durable into the steps above.

- **Archive moves and reconcile happen in the close-out commit, not the next kickoff** (steps 2,
  4). Multiple units skipped this and deferred it; the next kickoff often missed it too, and
  drift compounded until a freshness hook was added to refuse a stale reconcile.
- **The plan flip is the last keystroke** (step 12). The original wording was "after the commit
  is in", which was read as "immediately after", and the push was denied. The real rule is
  "after everything that needs the gate". The recurring failure: flip, push denied, re-flip to
  active, push, re-flip to executed.
- **Non-tiny units: author the close-out commit before handing off for merge.** A unit that
  touched eleven files in three commits handed off for merge without one; the reconcile slipped
  to the next kickoff, which then spanned four merges.
- **Retros batch to sprint end** (step 10), and the gate-log lines are recorded silently
  (10a, 10b).
- **Tiny-unit carve-out** (when-not section): a single-file tweak validated that the kickoff
  reconcile can *be* the close-out. Don't invent ceremony for work too small to need it.
- **Truncate green-path verification output.** When you run a build or test suite only to
  confirm it is green, pipe it to a count or tail (`... 2>&1 | tail -3`, or a dot reporter).
  Keep full output only when verification **fails** and you need the detail. A multi-line
  green summary is small per unit but compounds across many close-outs in one session.
- **Integrity hooks fail closed with their own bypasses.** If a close-out commit or push is
  denied by a generated-sink hook, **fix the underlying staleness** (regenerate, restage,
  retry). Don't toggle a bypass for routine drift; bypasses are for documented exceptional
  situations. On rebase, merge, or cherry-pick, the commit-time hook may allow intermediate
  states (the post-operation commit re-validates), but the pre-push backstop always validates
  the branch tip.
- **Surface; don't fix mid-close-out.** Gate-log clusters, flagged test-coupling rows, and
  tooling gaps are recorded and queued as follow-ups. A close-out that starts editing the gate
  is a close-out that never finishes.
