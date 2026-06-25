# sink-resolver — examples & walkthrough

A worked end-to-end demonstration: two branches change different sources, both regenerate the
generated index, the merge conflicts on the index (and only the index), and `resolve` produces
the provably-correct merged render.

## The pieces here

| File | What it is |
|---|---|
| [`gitattributes.example`](gitattributes.example) | The `merge=binary` line(s) to add to your `.gitattributes`, one per sink. |
| [`post-merge.example.sh`](post-merge.example.sh) | A `post-merge` git hook that regenerates the sink after a **clean** merge (the non-conflicting case). |
| [`build_index.py`](build_index.py) | A worked pure, deterministic generator with a `--check` mode — the kind of renderer this module requires. It renders `dist/index.md` from `records/*.md`. |

## Walkthrough (copy-paste in a scratch dir)

```bash
# 0. A scratch repo with the example generator.
mkdir /tmp/sink-demo && cd /tmp/sink-demo && git init -q
git config user.email t@example.com && git config user.name Tester
mkdir records dist
cp /path/to/sink-resolver/examples/build_index.py .
printf 'Alpha record\n' > records/a.md
printf 'Bravo record\n' > records/b.md
python3 build_index.py                       # render dist/index.md
echo 'dist/index.md merge=binary' > .gitattributes
git add -A && git commit -q -m base

# 1. Branch 1 adds a record + regenerates.
git checkout -q -b feat-charlie
printf 'Charlie record\n' > records/c.md
python3 build_index.py && git add -A && git commit -q -m 'add charlie'

# 2. Branch main adds a DIFFERENT record + regenerates.
git checkout -q main
printf 'Delta record\n' > records/d.md
python3 build_index.py && git add -A && git commit -q -m 'add delta'

# 3. Merge -> sources merge cleanly (different files); dist/index.md conflicts (merge=binary).
git merge --no-edit feat-charlie || echo "as expected: conflict on the sink"
git diff --name-only --diff-filter=U          # -> dist/index.md  (only)

# 4. Resolve: regenerate from the MERGED records (a,b,c,d), byte-check, finalize.
export SINK_RESOLVER=on
export SINK_RESOLVER_SINKS='dist/index.md'
export SINK_RESOLVER_GENERATOR_CMD='python3 build_index.py'
export SINK_RESOLVER_CHECK_CMD='python3 build_index.py --check'
python3 /path/to/sink-resolver/sink-resolver.py resolve
#   -> RESOLVED + finalized the merge: regenerated dist/index.md
#   dist/index.md now lists a, b, c, AND d — the correct union neither side alone had.
```

## What to notice

- **Nobody hand-resolved the index.** The merged index is exactly a fresh render of the merged
  sources — the union of both branches' records — which is the one correct answer.
- **The escalation boundary held.** Had `records/a.md` *also* conflicted (both branches editing
  the same record), `resolve` would have **escalated** (exit 4) and written nothing — a non-sink
  conflict is always a person's call.
- **The byte-oracle ran.** `build_index.py --check` confirmed the regenerate was byte-exact before
  the merge was finalized. Swap in a non-deterministic generator and `resolve` fails closed instead
  of committing an unprovable sink.
