# sink-resolver

**Deterministic, provably-correct auto-resolution of generated-file merge conflicts — because
a pure render has no merge, it has a *regenerate*.**

A [StrictLock](../README.md) Concurrency / Gates module. A *generated sink* is any checked-in
artifact derived from canonical source by a generator: a manifest, an index, a JSON schema, an
OpenAPI document, a generated README, a rendered table. When two branches both change the
sources, the sink diverges on both sides — and a plain merge either **mangles** it (line-merging
two renders into a corrupt blend) or stops with a **conflict a human must hand-resolve**, over
and over, on a file no human should ever edit by hand.

`sink-resolver` ends that. It pairs with the Wave-1
[`externalized-memory`](../externalized-memory/) projection bundle and the
[`generated-sink` gates](../generated-sink-commit-gate/) — the renderers and freshness gates this
resolver is the merge-time complement to.

## The one idea

A pure render carries no intent of its own, so its conflict has a *correct* answer, not a
judgment call. If the sink is a deterministic function of its sources, and the sources are
conflict-free by construction (or already cleanly merged), then the right post-merge sink is not
"ours", not "theirs", not a blend — it is **exactly what the generator produces from the merged
sources**. So the resolution is mechanical and provable:

> take the merged sources → run the generator → byte-check the result → stage it.

No guessing, because the sink encodes none. This is the same recipe the vault's conflict-free
work-registry uses to keep its generated index merge-proof across concurrent agents.

## ⚠ The loud precondition (on the tin, not a footnote)

**This is sound only if your sink is a PURE, DETERMINISTIC, byte-`--check`-able render of its
sources.** If the sink carries any hand-authored content the generator does not reproduce, then
regenerate-from-sources **silently clobbers those peer edits** — the one failure that makes this
dangerous if mis-adopted.

The module's structural guards against that are the **byte-oracle** (after regenerating, it proves
the result is a stable, byte-exact render — a regenerate that can't prove it **fails closed**) and
the **coverage guard** (a conflicted sink the regenerate left unchanged is escalated, not staged,
so a generator blind to one of your configured sinks can't silently keep `ours`). But the tool can
only verify *determinism and coverage* — **you** are responsible for the "no hand-authored content
in the sink" and "the generator and `CHECK_CMD` cover every configured sink" halves. If your sink
is not a pure render, do not adopt this.

## How it works

Two pieces, layered:

1. **`merge=binary` on the sink** (`.gitattributes`). This makes git **always conflict cleanly**
   on the sink instead of silently line-merging it: when both sides changed it, the path is left
   unmerged, "ours" kept verbatim — **no corrupt auto-merge and no conflict markers** (verified on
   git 2.54). The merge stops with a clean, unmerged sink.
2. **`resolve`** (this script), wired as the conflict handler. It reads the **complete** unmerged
   set and:
   - if **every** unmerged path is a configured sink → regenerates the sinks from the
     already-merged sources, runs the byte-oracle, `git add`s them, and finalizes the merge;
   - if **any** unmerged path is *not* a sink → **escalates** (exit 4), writes nothing, and names
     the offenders. The escalation is the whole safety story: auto-resolving anything but the
     deterministically-regenerable sink would be guessing at real source intent.
   - a **coverage guard** sits between regenerate and `git add`: if a conflicted sink comes out of
     the regenerate *byte-unchanged* from its `ours` bytes, the generator clearly doesn't cover
     it — so staging it would silently keep `ours` and lose the peer's edit. That escalates too,
     rather than trust that your generator regenerates every configured sink.

For the non-conflicting case (sources merged cleanly, the sink merely went stale), a plain
`post-merge` hook regenerates it — see [`examples/`](examples/).

## Why the merge gate isn't enough on its own

`merge=binary` and a `post-merge` hook are **local-merge** mechanisms. A **web-UI / server-side
merge** (GitHub "Merge pull request", a fast-forward on the server) ignores user-defined merge
drivers and fires **no local hook** — so a stale sink can reach the trunk that way, invisibly.
That's what **`check`** is for: a byte-exact freshness backstop you run in CI on the merged
result. It re-derives the sink and fails (exit 1) if the committed bytes are not a fresh render.
Layered, fail-closed: the local path self-heals; CI catches the web-UI path.

## What it prevents vs. detects vs. can't address

The same honesty the rest of StrictLock ships with — know exactly what you're buying:

- **Prevents** (at `resolve`): a corrupt or hand-resolved sink landing from a **local** merge.
  The sink is regenerated from the merged sources and byte-checked, so the merged sink is exactly
  a fresh render — never a line-merged blend, never a stale "ours".
- **Detects** (at `check`): a **stale sink that reached the merged tree via a path the local
  self-heal never ran** — the web-UI / server-side merge. CI fails closed on it.
- **Can't address**: (1) a sink that is **not a pure render** — hand-authored content in the sink
  is silently clobbered by regenerate; the byte-oracle proves determinism, not "no hand edits",
  so this half is the adopter's responsibility (see the loud precondition). (2) A **non-sink**
  conflict — that always escalates to a person; the resolver never guesses at source intent.
  (3) A **non-deterministic generator** — caught and failed-closed by the oracle, but it means the
  module is not applicable to that sink.

## Quickstart (the git-native shape — the default)

```bash
# 1. Mark the sink merge=binary so git conflicts cleanly instead of mangling it.
echo 'dist/manifest.json merge=binary' >> .gitattributes

# 2. Configure the resolver (see CONFIG.md).
export SINK_RESOLVER=on
export SINK_RESOLVER_SINKS='dist/manifest.json'
export SINK_RESOLVER_GENERATOR_CMD='python3 build_manifest.py'      # regenerate in place
export SINK_RESOLVER_CHECK_CMD='python3 build_manifest.py --check'  # the byte-oracle (recommended)

# 3. Wire the local hooks (examples/): a post-merge hook for the clean case, and call
#    `resolve` when a merge conflicts on the sink:
python3 sink-resolver.py resolve            # auto-resolve a sink-only conflict + finalize
python3 sink-resolver.py resolve --dry-run  # classify + print the plan; write nothing

# 4. In CI, on the merged result — the web-UI-merge backstop:
python3 sink-resolver.py check              # exit 1 if the merged sink isn't a fresh render
```

## The web-UI-merge variant (documented alternative)

The default above self-heals **local** merges and lets CI catch the web-UI path. If your fleet
merges primarily through a web UI and you want the merged commit to land **already resolved**
(rather than caught after the fact in CI), the alternative placement is a **pre-merge worktree
resolver**: run the regenerate-and-stage recipe *before* the merge completes, so a server-side
fast-forward lands an already-correct tree. That shape — with a dry-run default and circuit
breakers for runaway re-resolution — is heavier but workflow-agnostic. The reference
implementation for it is HIPAAPath's `conductor-resolve.py`; `sink-resolver`'s `resolve` is the
git-native core of the same recipe. See [SCHEMA.md](SCHEMA.md) "Placement variants".

## Files

| File | Purpose |
|---|---|
| [`sink-resolver.py`](sink-resolver.py) | The resolver. One file, standard library only. |
| [`CONFIG.md`](CONFIG.md) | Every `SINK_RESOLVER_*` environment variable + exit codes. |
| [`SCHEMA.md`](SCHEMA.md) | The sink set, the generator/oracle command contract, the byte-oracle, the placement variants. |
| [`examples/`](examples/) | A `merge=binary` snippet, a `post-merge` hook, a worked generator + sink, and a walkthrough. |
| [`tests/`](tests/) | A standalone suite (stdlib `unittest`) that builds real git repos and real `merge=binary` conflicted merges. |

## Requirements

Python 3.8+ (standard library only — no pip install) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
