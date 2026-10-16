# Pre-WP currency check

**Before you lock a plan around a backlog item, ask whether the item is still worth doing.**

A [rituals](README.md) checklist. A backlog item was raised for a reason, on a particular day,
against a particular state of the code and the product. By the time it is pulled into a unit of
work, any of those may have changed. The currency check is the gate between "this is in the
backlog" and "this is in the plan's `allowed_paths`".

It runs **after** you have a candidate scope and **before** you author the plan, as one step in
[`session-kickoff.md`](session-kickoff.md). It is a sibling of the two checks in
[`wp-interaction-analysis.md`](wp-interaction-analysis.md): together the three cover *is this
item still relevant*, *what did the last unit leave for me*, and *what do my peers collide with*.

It cannot be a hook. The verdicts require reading and interpretation, not a syntactic match —
which is exactly why it is a ritual. The mechanism that makes it hard to skip is the
**report-before-lock** rule below.

## The rule

For every backlog item being pulled into the unit of work, answer three questions before
locking `allowed_paths`:

1. **Overcome by events?** Has work shipped since the item was logged that already addressed
   the underlying problem? Check the git log, recent units' retros or close-out notes, and the
   *current state of the files the item would touch* — not the item's own description of them.
2. **Does the original driver still apply?** The item was raised for a reason. Is that reason
   still load-bearing, or has the product, the strategy, or the feedback that motivated it
   moved? Read the original item, and whatever evidence it cites, to ground yourself in the
   motivation, then compare to the present.
3. **Is it still a good idea given recent state?** Could doing it now contradict a recent
   direction decision or feedback that has since landed? An item can be un-addressed, still
   motivated, and *still wrong* because something decided after it was logged points the other
   way.

## The three verdicts

Give each item exactly one:

| Verdict | Meaning | What happens |
|---|---|---|
| **Still applies** | Nothing has overtaken it. | Keep it in scope; its files go in the plan's `allowed_paths`. |
| **Partially absorbed** | Some of the concern was already handled, some remains. | **Re-scope.** Write down what remains and what was already addressed; scope the plan to the remainder. Note that the real scope may be *larger* than the original as well as smaller (see the motivating instance). |
| **OBE** (overcome by events) | The problem is gone, the driver is gone, or doing it now would contradict newer direction. | **Drop it.** Record in your state file that the item is dropped *and why*. If the underlying concern could resurface, flag that a new scoped item would be needed — a dropped item should not silently leave a gap no backlog row covers. |

## Report before you lock

Present the findings to the human **before** the plan is locked. For each item give:

- the files or records you looked at,
- the evidence (an excerpt, the current code state, a citation of the retro or feedback that
  overtook it),
- the verdict.

The human signs off on the final scope. This is the design point: the check does not decide, it
makes the decision *impossible to skip and cheap to make*. A reader who sees "item 2: OBE —
the sentence it targets no longer appears; here is the commit" can confirm in seconds what would
otherwise take a re-investigation, and a reader who disagrees has the evidence in hand to say so.

Where this plugs into the gates: the verdicts decide what goes into the plan, and the plan is
what [`plan-gate`](../plan-gate/) enforces. A stale item that slips through becomes an exact
entry in `allowed_paths` — authorized, in-scope, and wasted. This check is the cheapest place to
stop it.

## Why this exists — the pilot

The check was introduced after a discussion-first unit of work about bundling small items. Run
on that unit's planned scope, it changed the outcome for two of its items:

- **An item was dropped as OBE.** The item proposed removing a sentence from a marketing page
  to fix a framing problem. Reading the feedback that had since landed showed reviewers
  explicitly praised that sentence as the page's sharpest, most falsifiable claim and asked for
  it to be *expanded*, and the branding the original concern targeted had already been removed
  from the page. Doing the item would have been wasted work that contradicted shipped feedback.
- **An item was re-scoped upward.** A copy fix estimated at ~30 minutes in one file turned out
  to need two or three files, because the mismatch lived on a second page that bridged to the
  first. Without the check the second page would have been missed and the surface would have
  stayed inconsistent.

Without the check, one item ships as wasted work and the other as a half-fix. The verdicts
could not have been enforced by a hook; they were found by reading.

## When to revisit the rubric

- **If it starts dropping items that turn out to still apply** (false-positive verdicts):
  tighten the evidence requirement — ask for the specific commit or excerpt that overtook the
  item, not a general impression.
- **If it lets stale items through that ship and have to be reverted** (false-negative
  verdicts): add a post-merge audit to your close-out that compares what shipped against the
  currency-check evidence recorded for it.

## A short template

```
Item:        <backlog id / one-line description>
Raised:      <date, and why>
Q1 OBE?      <yes/no/partly — evidence: commits, notes, current file state>
Q2 Driver?   <still load-bearing / moved — evidence>
Q3 Still good? <yes / contradicts <recent direction> — evidence>
Verdict:     Still applies | Partially absorbed (remaining: ...) | OBE (reason: ...)
Plan impact: <files added to allowed_paths / scope reduced or grown / dropped + where recorded>
```
