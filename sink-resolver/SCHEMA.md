# sink-resolver schema

Four things have a defined shape: the **sink set**, the **generator / oracle command contract**,
the **byte-oracle** (the determinism guard), and the two **placement variants**.

## The sink set

`SINK_RESOLVER_SINKS` is the set of generated artifacts the resolver is *allowed* to
auto-resolve. It is the exact same set you place under `merge=binary` in `.gitattributes`.

- Entries may be **absolute or repo-relative**, with `./` prefixes, trailing slashes, or
  back/forward slashes — each is folded to a single **canonical repo-relative POSIX key** so it
  can be compared against git's repo-relative unmerged-path output (`git diff --name-only
  --diff-filter=U`) regardless of spelling.
- A sink that resolves **outside** the repo is a fail-closed misconfiguration: the resolver could
  never match it against an unmerged path, so a stale sink could slip through unnoticed — better
  to refuse loudly.
- The set defines the **escalation boundary**: any unmerged path *not* in the set forces an
  escalation. This is the safety property — see below.

## The generator / oracle command contract

| Command | Mutates? | Role |
|---|---|---|
| `SINK_RESOLVER_GENERATOR_CMD` | **yes** — overwrites the sink(s) in place | Regenerate the sink from the (merged) sources. Run after a sink-only conflict and for the no-`CHECK_CMD` fallbacks. A non-zero exit is fail-closed (the generator errored). |
| `SINK_RESOLVER_CHECK_CMD` | **no** — regenerate-internally, compare | The strong **byte-oracle**: exit 0 iff the committed sink is a byte-exact render of the current sources, non-zero on any drift. The universal `--check` convention. |

Both run in `SINK_RESOLVER_GENERATOR_CWD` (default: repo root) with worktree-private git env
(`GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE`) stripped, so a generator that itself shells to
git re-discovers the repo cleanly from its cwd — which matters inside a linked worktree and during
an in-progress merge.

## The byte-oracle (the determinism guard that makes the clobber safe)

`resolve` does the equivalent of `--ours`-then-regenerate, which is a **deliberate silent
clobber** of the unmerged sink bytes — valid *only* because the sink is a pure render. The oracle
is the structural proof that the clobber is safe to finalize:

- **`SINK_RESOLVER_CHECK_CMD` set → the STRONG oracle.** After regenerating, the check command
  must exit 0. This proves the staged bytes equal a fresh render of the **current** sources. If it
  exits non-zero, the sources may still be unresolved or the generator is non-deterministic →
  **fail closed** (exit 1, finalize nothing).
- **unset → the WEAKER double-regenerate oracle.** Snapshot the sinks, regenerate again, require
  byte-identical output. This proves the generator is **deterministic** on these sources (so the
  clobber is reproducible) but not that the sink matches the sources independently. Announced on
  stderr so the weaker guarantee is never silent.

Either way, a failed oracle is **fail-closed**: an unproven sink is escalated to a human, never
committed.

## The escalation boundary (the safety story)

`resolve` reads the **complete** unmerged set in one read, then partitions it:

```
unmerged = git diff --name-only --diff-filter=U
non_sink = unmerged − SINKS        # anything not in the configured sink set
```

- `non_sink` empty → every conflict is a regenerable sink → auto-resolve.
- `non_sink` non-empty → **escalate** (exit 4), write nothing, name every offender.

Mis-bucketing here is the only silent-data-loss path, so the partition is conservative by
construction: a path is auto-resolved *only* if it is explicitly in `SINK_RESOLVER_SINKS`.
Everything else — including a source file that genuinely conflicted — goes to a person.

### The coverage guard (the second safety property)

The partition assumes your `SINK_RESOLVER_GENERATOR_CMD` actually *regenerates* every path in
`SINK_RESOLVER_SINKS`. If it doesn't — a sink is in the set but the generator (and `CHECK_CMD`)
are blind to it — then `git add`-ing that sink would stage its unmerged **'ours'** bytes and
silently drop the peer's edit. So `resolve` does not trust that assumption: between regenerate and
`git add`, it checks that **every conflicted sink actually changed**. A conflicted sink left
byte-identical to its 'ours' bytes means the generator did not rewrite it → **escalate** (restore
the conflicted sinks to 'ours', write nothing, exit 4). Its one false positive — a sink whose
merged render legitimately equals 'ours' (e.g. a subset merge) — over-escalates to a human, the
safe direction. This is what makes "the generator/oracle covers every sink" a *checked* property,
not an operator promise.

### Path spelling (sink-key normalization)

`normalize_sinks` resolves each configured sink against the repo top-level and **follows
symlinks** (`Path.resolve`), then compares the resulting repo-relative key against git's literal
unmerged-path output by exact string. The asymmetry (keys are resolved; git's paths are not
re-normalized) only ever **over-escalates**: a sink presented via an unexpected symlinked or
miscased spelling fails to match and goes to a human, rather than colliding with a real non-sink.
Present your sinks via the same spelling git uses (and the same one in `.gitattributes`).

## Placement variants

| | Default (this script) | Documented alternative |
|---|---|---|
| **Where** | local merges | web-UI / server-side merges |
| **Mechanism** | `merge=binary` + `post-merge` hook + `resolve` on conflict + `check` in CI | a **pre-merge worktree** resolver that lands an already-resolved tree |
| **When it acts** | after the merge stops on the sink | before the merge completes |
| **Default mode** | `resolve` acts (with `--dry-run` to preview) | dry-run by default; `--apply` to act |
| **Extras** | — | circuit breakers (a ceiling on repeated re-resolution), `--rebase` onto the base first |
| **Reference** | `sink-resolver.py` (the git-native core) | HIPAAPath `conductor-resolve.py` |

**Why two.** `merge=binary` and local hooks are **inert on a web-UI merge** — git ignores
user-defined merge drivers there and fires no local `post-merge` hook. The default covers that
with the CI `check` backstop (catch-after-the-fact). The alternative instead runs the recipe
*pre-merge in a worktree* so the server-side merge fast-forwards an already-correct tree — heavier
(it needs a worktree and a runaway-resolution circuit breaker), but it lands the trunk clean
rather than failing CI. Both share the same core recipe; they differ only in *where* it runs.

## State this module writes

- **`resolve`**: regenerates the configured sink(s) and **stages only the sink set**; finalizes
  the merge commit unless `--no-commit`. On escalation or any fail-closed condition it stages and
  commits **nothing** (and the coverage-guard escalation restores the conflicted sinks to 'ours'
  first, leaving the merge exactly as found). NOTE: the *generator* is expected to write only the
  sink set — `resolve` stages only the sinks, so a generator that also writes other tracked files
  leaves those **dirty** after finalize (out of contract; surfaced by `git status` and the commit
  gates, never committed by `resolve`).
- **`check`**: with `CHECK_CMD`, mutates nothing. Without it, it **snapshots** the sink bytes,
  regenerates transiently, compares, and **restores the exact pre-call bytes** — so it is net
  non-mutating on **any** tree (it never `git checkout`s, so it cannot clobber a locally-modified
  sink or give a worktree-vs-index false verdict).
- **the decision log** (`SINK_RESOLVER_LOG_DIR/sink-resolver.log`): append-only JSONL, best-effort.
