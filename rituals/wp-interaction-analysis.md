# Unit-of-work interaction analysis

**Two planning questions the currency check doesn't ask: what did the last unit leave for me,
and what do the units running beside me collide with?**

A [rituals](README.md) checklist. It is the companion to the
[pre-work currency check](pre-wp-currency-check.md). Where currency asks "is this backlog item
still relevant?", interaction analysis asks two structural questions about how units of work
relate to each other. Both fire **before the plan is locked**, because that is the last cheap
moment: once the plan is active, every discovery costs a stop-and-replan.

| Lens | Question | Looks at | Fires when |
|---|---|---|---|
| **1. Prior-unit deferred-marker grep** | What obligations did the previous unit bury in its code for me? | The previous unit's *shipped files* | This unit continues a prior unit's code, or is step N of a multi-step arc |
| **2. Peer-unit collision pass** | What do the units scoped or running alongside me collide with? | The *set* of plans under consideration | Several units are scoped together, bundled, or will run concurrently from sibling worktrees |

Together with the currency check they cover the three ways a plan gets locked around the wrong
picture: the item is stale (currency), the last unit left work for you that nobody wrote down
(lens 1), or a peer is about to reach for your files (lens 2).

---

## Lens 1 — Prior-unit deferred-marker grep

### Why it exists

A scan of the spec, the backlog, and the previous unit's retro catches obligations that were
*written down in a planning surface*. It does not catch an obligation that lives **inside the
shipped code** — in a comment, a TODO, or a placeholder reference — because that obligation was
never promoted to any planning surface. Those are invisible to every document you would read
when scoping the next unit, and visible only to a search of the code itself.

**The motivating instance (anonymized).** A kickoff for the second step of a two-step build
missed that its scope needed an integrity check activated. The obligation was buried in the
*first* step's shipped code: a no-op placeholder reference that kept an unused value "wired in"
plus a comment saying it was kept for the next step. Neither the spec scan nor the backlog nor
the first step's close-out notes mentioned it. The miss was discovered when the unit's first
feature commit hit the gate for a file the plan had not listed, and the unit stopped to replan
and widen `allowed_paths` mid-flight — exactly the friction the pre-lock gate exists to
prevent.

The currency check answers "is this item stale?" It cannot answer "did the prior unit ship code
with a latent deferred-to-here marker?" This grep is the structural complement.

### When it fires — and when to skip

Run it only if this unit is **step N of a multi-step arc** or **continues a prior unit's code**
(the unit's name shares a stem with a recently shipped unit, or its scope references a prior
unit's shipped files). **Skip it entirely for green-field units** — a check that always fires
trains people to skip it.

### The grep

Before locking `allowed_paths`:

1. **Identify the prior unit's shipped files.** Read its close-out notes' "files touched" list,
   or run `git show --stat <prior-merge-sha>` and read the file list.
2. **Search those files for deferred-to-here markers:**

| Marker | What it usually means |
|---|---|
| a no-op reference to an otherwise-unused value (e.g. `void x;` in JS/TS, or the equivalent in your language) | Code wired in but not yet consumed; a later unit is meant to use it |
| `TODO`, `FIXME`, `XXX` | Standard deferred-work tokens |
| a literal hand-off phrase your team uses (e.g. "kept wired for", "placeholder until", "follow-up:") | A verbatim hand-off from one step to the next; search for it literally, and expect it to recur across an arc |
| **this unit's own name or step label** | The prior unit named *you* as the recipient |
| generic references to the arc's other steps (e.g. `step 2`, `phase B`, the arc's name prefix) | Possible hand-offs addressed to a later step by number |

   A one-liner is enough — enumerate the files with `git show --stat <sha>`, then
   `grep -nE '<pattern-for-your-markers>' <file-list>`. Tune the pattern to the markers your
   project actually uses.

### What to do with findings

Classify every hit as exactly one of:

| Classification | Action |
|---|---|
| **In scope for this unit** | Expand the plan's `allowed_paths` / `allowed_commands` **before locking**. Surface the finding in the approval report so the widened scope is explicit, not implicit. |
| **Out of scope for this unit** | Record it in your state file as "deferred to `<unit>`". **Do not silently expand scope.** A deferral you write down is a deferral someone can find. |
| **Genuinely stale** (the obligation already shipped under another name) | Note it and ignore it. |

---

## Lens 2 — Peer-unit collision pass

### Why it exists

Each unit is normally scoped in isolation. When several are scoped together (sprint planning,
a bundle of small fixes) or designed to run at once from sibling worktrees, no one unit's plan
shows the others. Shared files, ordering constraints, and mutual deferrals stay invisible until
two sessions meet at a merge — or until one of them waits on a behavior the other was supposed
to ship. Running units in parallel makes this load-bearing; running them one at a time makes
the shared-file case mostly moot but leaves ordering and deferrals fully live.

### When it fires

When multiple units are scoped together, grouped into a bundle, or designed to run
concurrently. Skip it for a single unit with nothing else in flight.

### The pass

Run a tabletop over the *set* of plans before locking any of their `allowed_paths`:

- **Shared files.** Which `allowed_paths` overlap between units? An overlap is fine if the
  units are **sequential** and one closes before the other opens. It is a **collision** if they
  run concurrently. Resolve it one of three ways: (a) *sequence* the units so they are no
  longer concurrent; (b) *split* the shared file into per-unit regions or files; (c) *choose a
  single owner* for the touch and route the other units' needs through it.
- **Ordering constraints.** Does unit B depend on a behavior shipped by unit A? If so, lock the
  order at planning, document the dependency in **both** plans (frontmatter or body), and do not
  open B's branch until A's change is merged — or at least pushed, if B can rebase onto it
  later.
- **Mutual deferrals.** Does A defer a sub-scope to B? Does B implicitly assume something A is
  supposed to set up? Write it down on both sides — "A leaves X for B" and "B receives X from
  A" — so neither side silently drops it.

### Where to surface the findings

In the plan-approval step of [`session-kickoff.md`](session-kickoff.md), alongside the currency
verdicts. For multi-unit planning, also record the cross-unit notes wherever your project keeps
planning state (your [state file](../externalized-memory/) or a sibling planning document) so
the next planner sees them.

---

## The belt-and-suspenders note: the shared-file sub-case is now also structural

Lens 2 started life as a pure tabletop exercise. Part of it has since been backed by a gate, and
the honest account of which part matters:

- **Shared files — now structurally enforced as well.** [`plan-gate`](../plan-gate/) refuses to
  let two concurrently active plans list the same absolute file (or one a directory containing
  the other's path): it denies, naming the other active plan and the colliding paths, so you can re-scope. See the cross-plan
  disjoint check in [`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) §4. For fleets of
  worktrees where you need an *exclusive claim* rather than only a plan-time check, see
  [`scope-lease`](../scope-lease/). So the tabletop for this sub-case is now a
  **belt-and-suspenders peer of a structural guard**, not your only defense.
- **Ordering constraints and mutual deferrals — still prose only.** No gate can know that unit B
  needs a behavior unit A hasn't merged, or that A promised B a sub-scope. These remain
  entirely the tabletop's job. Do not read the shared-file guard as retiring the lens.
- **The guard has edges.** It compares *active* plans only, and counts only the *other* plan's
  **absolute** `allowed_paths` entries (a relative entry can't be attributed to a worktree, so it
  is skipped to avoid false positives between legitimate parallel sessions). It is skipped
  for a plan that opts out via `worktree_bypass`. And it fires late, as a deny, when a gated
  action is evaluated against the second plan — whereas the tabletop catches the overlap
  *before either plan is written*. The cheap time to resolve a collision is before the second
  plan exists.

Keep the tabletop. Treat the gate as the thing that catches you on the day you skip it.

## Cross-references

- [`pre-wp-currency-check.md`](pre-wp-currency-check.md) — the sibling check: is the item still
  relevant?
- [`session-kickoff.md`](session-kickoff.md) — where all three checks sit, pre-plan-lock.
- [`plan-gate/AUTHORING.md`](../plan-gate/AUTHORING.md) — how the plan that results from these
  checks should be written.
