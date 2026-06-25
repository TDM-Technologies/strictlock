# Authoring a plan — the discipline

`SCHEMA.md` tells you the *shape* of a plan's frontmatter; `CONFIG.md` tells you the
*environment* the gate runs in. This guide is the missing third piece: how to author a plan that
is both **useful** (it lets the agent do the work) and **safe** (it doesn't quietly hand over
more authority than the work needs).

Everything here is grounded in what `plan-gate.py` actually enforces. The gate is small and
deny-by-default; the failure mode is never "the gate let something dangerous through by accident"
but "*you* wrote a plan looser than you meant to." A plan is a grant of authority. Author it like
one.

The mental model throughout: an `allowed_paths` entry is **exact**, an `allowed_commands` entry is
a **prefix**, and a prefix is a wildcard with the asterisk left off. Most authoring mistakes are
one of those two facts forgotten.

The gate runs as a PreToolUse-style hook in your agent harness — it sees a tool call *before* it
runs and returns allow/deny. The tool names used below (`Edit` / `Write` / `NotebookEdit`,
`Bash` / `PowerShell`) are the ones the reference contract uses; Claude Code is the worked example
in `examples/`, but the authoring discipline is the same against any harness that exposes file-write
and command tools through a pre-action hook. Map the tool names to your harness's equivalents and
the rules carry over unchanged.

---

## 1. `allowed_paths` — exact match, no globs

The gate authorizes a file write (Edit / Write / NotebookEdit) by comparing the target against
each `allowed_paths` entry with **exact, normalized equality** — not prefix, not glob, not
"under." Normalization lower-cases, converts `\` to `/`, and strips a trailing slash; that is the
*entire* flexibility. There is no `*`, no `**`, no `src/`-means-everything-inside.

Three consequences to internalize:

- **A directory entry authorizes nothing.** Listing `src/` does not authorize `src/foo.ts` or any
  other file — `src/` only ever matches a write whose target is literally `src/`, which never
  happens. The gate notices this case and prints a `WARNING: allowed_paths entry ... is a
  directory; directory entries do NOT expand to children` to stderr at the first tool call, so the
  mistake surfaces immediately instead of as a baffling "not in allowed_paths" deny mid-work. Heed
  the warning: enumerate the individual files.

- **Enumerate every file the work will touch — including the ones that aren't the "main" file.**
  Test files, snapshots, generated companions, a config you'll bump, the changelog line you'll
  add. If the agent will write it, it must be named. A plan that lists the component but forgets
  its test file will stop the agent dead at the first `npm test`-driven snapshot update. That is
  the gate working correctly; the fix is a complete plan, not a looser one.

- **No partial credit for "close."** A one-character path difference is a non-match and a deny —
  this is by design (it is exactly the war story behind the gate: a memory write was denied
  because a bypass covered a *different* directory one character apart). Copy real paths in; don't
  retype them from memory.

**Absolute vs. relative.** Entries may be absolute or repo-root-relative. Relative entries are
anchored at the session's **worktree root** if the session runs inside a git worktree, otherwise
at the repo root (see §3). Prefer relative paths for in-tree work — they travel correctly with the
worktree and keep the plan readable. Reserve absolute paths for deliberately cross-tree work, and
when you use them, read §3 and §4 first.

### Rule of thumb

> List the **exact files**, one per line, every one the work writes. If you're tempted to list a
> directory "to be safe," that instinct is backwards — broad is *less* safe, and here it's also
> simply inert.

---

## 2. `allowed_commands` — prefix match, so narrow the prefix

The gate authorizes a Bash or PowerShell command if it **starts with** one of the
`allowed_commands` entries (a plain `str.startswith`). That is more permissive than path matching,
and it is where over-granting hides.

A prefix authorizes *its entire subtree of invocations*. `allowed_commands: [npm test]` correctly
authorizes `npm test -- --watch=false` — convenient and intended. But the same mechanism means a
loosely chosen prefix authorizes things you didn't picture:

- `git` authorizes `git push`, `git reset --hard`, `git clean -fdx` — the whole tool.
- `npm` authorizes `npm publish` and `npm run <any-script>`.
- `python` authorizes running *any* script with *any* arguments.

The discipline is to **make the prefix as specific as the work requires and no looser** — usually
the subcommand, not the bare program.

### The destructive-suffix trap (the one that bites)

Because matching is prefix-only, a benign-looking entry can carry a destructive command inside it.
The canonical example:

```yaml
allowed_commands:
  - git checkout       # BAD: this is a prefix
```

That single entry authorizes the harmless branch-switch `git checkout my-branch` **and** the
working-tree-nuking `git checkout -- .` (discard all uncommitted changes) **and**
`git checkout -- some/file` (discard one file) — because every one of those *starts with*
`git checkout`. The prefix can't tell "switch branch" from "throw away the agent's own
uncommitted work."

If the work genuinely needs a branch switch, narrow the grant so the destructive form can't ride
along:

```yaml
allowed_commands:
  - git checkout -b    # create-and-switch only; "-- ." does not start with this
  - git switch         # the modern, non-destructive branch-switch verb
```

Generalize the instinct: **before adding a command prefix, ask what the most destructive thing is
that *also* starts with this string.** If the answer is "discard files," "force-push,"
"rewrite history," or "delete," tighten the prefix until that thing no longer matches — add the
flag or subcommand that makes the grant specific. A loose prefix is a loose grant wearing the
costume of convenience.

### What you do *not* need to list

Read-only and navigational commands are always allowed, regardless of plan, so don't waste
`allowed_commands` entries on them:

- A fixed family of investigation commands: `git status`, `git diff`, `git log`, `git show`,
  `git branch`, `git rev-parse`, `ls`, `pwd`, `cat`, `rg`, `grep`, `find`, `head`, `tail`, `wc`,
  `which`, and friends. (`git config` is allowed only in its read-only `git config --get` form —
  the bare two-arg `git config <key> <value>` *writes*, so it is not on the list.)
- The cross-tree read-only shape `git -C <path> <read-only-subcommand>` — e.g.
  `git -C ../other-tree status` — so inspecting another tree needs no grant.
- Bare `cd <path>` navigation.

A compound like `cd <path> && <something>` is **not** waved through on the strength of the `cd`:
the gate splits the line on top-level `&&`, `||`, and `;` (quote-, subshell-, and heredoc-aware)
and checks **each segment independently** against the read-only set and your `allowed_commands`.
This is deliberate — it closes the `cd anywhere && <arbitrary command>` hole that a naive prefix
match would open. The practical authoring consequence: **a compound command is only as authorized
as its least-authorized segment.** If a step needs `cd build && make install`, then `make install`
must itself match an `allowed_commands` prefix; the `cd` buys it nothing.

(PowerShell is checked by prefix against `allowed_commands` too, but with **no compound parsing at
all** — `Get-Foo; Set-Bar` is not split. If you need a multi-segment PowerShell command, list each
shape you intend explicitly, or run it through Bash.)

### Rule of thumb

> A command prefix is a wildcard. Pick the **narrowest prefix that still matches the work**, and
> sanity-check it by naming the worst command that also starts with it. If that worst case is
> destructive, narrow further.

---

## 3. The worktree hard-guard and `worktree_bypass`

This rule exists because of a real wrong-target write: an agent working in an isolated worktree
once wrote to the **main** repository instead, because its plan listed main-repo paths and the
write "succeeded" — at the wrong place. The gate now anchors path resolution at the session's
actual working root and adds a hard guard against that drift.

**What the guard does.** When the session's working directory is inside a git worktree (the `.git`
is a *file* pointing at the main repo, not a directory), the gate resolves every relative
`allowed_paths` entry against the **worktree root**. Then, before authorizing anything, it
requires that **at least one** `allowed_paths` entry resolve *under that worktree*. If a plan has a
non-empty `allowed_paths` and **zero** of them land under the current worktree, the gate blocks
**every** tool call — not just the off-tree write, the whole session — with a "silent-drift
pattern" deny. The reasoning: a plan whose paths don't point where the session is running is
misaligned, and continuing would write somewhere you didn't mean. It fails closed, loudly, on the
first action.

**The authoring takeaway.** For normal in-tree work, do nothing special — just use
**repo-root-relative paths**. They anchor at the worktree automatically and the guard is satisfied
the moment one of them is genuinely in your tree. The guard only fires when a plan and a session
have drifted apart, which is exactly when you want to be stopped.

**`worktree_bypass: true`** is the deliberate opt-out, for work that is *genuinely* cross-tree —
the plan's `allowed_paths` are absolute and intentionally point outside (or across) worktrees.
Setting it:

- skips the worktree hard-guard (the plan is *expected* not to resolve locally);
- makes the plan **cross-tree exclusive** — it counts against the single-active invariant of
  *every* worktree, so no other plan can be active anywhere while it is (see §4);
- exempts the plan from the cross-plan disjoint-paths overlap check.

Treat `worktree_bypass` as load-bearing, not boilerplate. Reach for it only when the work really
does span trees and you've listed absolute paths on purpose. The cost of an unnecessary bypass is
a wider blast radius and a global lock on concurrency; the cost of a missing one is a loud deny
that tells you exactly what to add. Prefer the loud deny.

---

## 4. The active-plan rule — exactly one, and what "active" claims

The gate authorizes against a **single active plan**. A plan is armed only when its frontmatter
says `status: active` (case-insensitive); **any** other value — `draft`, `archived`, `executed`,
a typo — makes it inert. Two ways this rule bites at authoring time:

- **Zero active plans → everything denied.** With no `status: active` plan in the plans directory,
  every gated tool call is denied. That is the deny-by-default floor, not a bug. (One bootstrap
  exception: directories listed in `PLAN_GATE_ALWAYS_WRITABLE` — typically the plans directory
  itself — stay writable with no active plan, precisely so you can *write the plan* before one
  exists. Read-only commands also still work, so you can inspect state.)

- **Two active plans → also denied.** If more than one plan is active (within the same worktree's
  view), the gate refuses to guess and denies with both names, telling you to set all but one to
  `status: archived`. So the discipline is **one unit of work, one active plan** — and when you
  start the next unit, retire the last one first (§5).

**"Active" is scoped per worktree, and absolute paths are what stake the claim.** When the session
is inside a worktree, a plan "claims" that worktree only if it has `worktree_bypass: true` **or**
at least one **absolute** `allowed_paths` entry that resolves under the worktree. Relative entries
name files *within* a claimed worktree but cannot, by themselves, identify *which* worktree a plan
belongs to (a relative path would resolve under whatever session is asking). The practical rule:

> If you run concurrent sessions in separate worktrees and want each to have its *own* active
> plan, give each plan an **absolute** `allowed_paths` entry rooted in its worktree — that's the
> anchor that lets the gate tell the plans apart. A plan with only relative paths is visible only
> to sessions outside all worktrees.

There is also a **cross-plan disjoint check**: two active (non-bypass) plans may not both list the
same absolute file, nor one a directory containing the other's path. If they do, the gate denies
and names both so you can re-scope. The authoring discipline this enforces is clean: **each file
is owned by exactly one active plan at a time.** Don't write two plans that both reach for the
same shared file.

---

## 5. Lifecycle — flip `status` away from `active` as the *last* gated action

The schema's lifecycle is **Author → Work → Retire**: author a plan with `status: active`, let the
gate confine the work, then retire the plan (set `status: archived` — or `executed`, or any
non-`active` value; the gate treats every non-`active` value identically as "inert") so the next
unit of work starts from a clean deny-by-default state. The discipline is in the *ordering* of the
last step.

**The trap.** The instant you flip `status` away from `active`, the plan stops authorizing
**anything** — including the rest of its own close-out. If your close-out sequence is "flip status,
then `git add`, then `git commit`," the flip de-authorizes the `git add` and `git commit` that
were supposed to follow it. The plan has fired its own off-switch mid-shutdown, and the gate —
correctly — denies the remaining steps because there is now no active plan.

**The rule.** Make the status flip the **last gated action** in the unit of work. Run the real
work first — edits, tests, the commit — and flip `status` to `executed`/`archived` only once
everything the plan authorized is done. Sequence the close-out as:

1. final edits to the named files,
2. `git add` / `git commit` (these must be in `allowed_commands`),
3. **last:** the edit that flips `status`.

**A wrinkle worth knowing.** Whether the status-flip edit is *itself* gated depends on where the
plan file lives:

- If the plans directory is in `PLAN_GATE_ALWAYS_WRITABLE` (the common, recommended setup), the
  plan file is always writable, so flipping its status is never gated — the ordering trap above
  doesn't bite the *flip itself*. But it still bites anything you scheduled to run **after** the
  flip, because the *rest* of close-out is gated and now has no active plan. The ordering rule
  stands either way.
- If the plan file is **not** in an always-writable directory, then to flip its status the plan
  must list **its own path** in `allowed_paths` — and that edit is the literal last authorized act,
  because performing it removes the authorization for everything else.

Either way the guidance collapses to one line:

> Do the work, commit, **then** archive the plan. Never archive first.

---

## A pre-flight checklist

Before you set `status: active`, read the plan back as a grant of authority and confirm:

- [ ] Every file the work will write is listed in `allowed_paths`, **exactly**, including tests
      and generated companions. No directories.
- [ ] Relative paths are used for in-tree work; absolute paths only where the work is genuinely
      cross-tree.
- [ ] Each `allowed_commands` prefix is as narrow as the work needs. For every prefix, the worst
      command that also starts with it is **not** destructive (no `git checkout`-style trap, no
      bare `git`, no bare `npm`/`python`).
- [ ] Any multi-step Bash you rely on survives segment-wise checking (the `cd` buys nothing; each
      segment is authorized on its own).
- [ ] `worktree_bypass: true` is present **only** if the work is deliberately cross-tree, and then
      `allowed_paths` are absolute.
- [ ] Exactly **one** plan is `status: active`; the previous unit's plan is archived.
- [ ] If concurrent worktrees each need their own active plan, each carries an absolute
      `allowed_paths` anchor in its worktree, and no two active plans share a file.
- [ ] The close-out sequence ends with the status flip — work and commit come first.

A plan that passes this list grants exactly the authority the work needs and not a byte more.
That is the whole discipline: the gate will hold the floor; your job is to make sure the floor is
drawn in the right place.
