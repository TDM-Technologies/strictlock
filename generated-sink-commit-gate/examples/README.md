# generated-sink-commit-gate examples

This directory shows the gate end-to-end against a real (tiny) generator.

- [`sample-generator.py`](sample-generator.py) — renders `SOURCE.txt` → `SINK.txt`; in
  `--check` mode exits non-zero iff `SINK.txt` is not a byte-exact regeneration. Stands in for
  your real manifest/index/schema/swagger builder.
- [`SOURCE.txt`](SOURCE.txt) — the canonical source.
- [`SINK.txt`](SINK.txt) — the checked-in generated artifact, currently fresh.
- [`pre-commit-hook.example.sh`](pre-commit-hook.example.sh) — what to drop into
  `.git/hooks/pre-commit`.

Git invokes a `pre-commit` hook with no arguments and honors its exit code: `0` lets the
commit proceed, non-zero blocks it.

## Setup

Point the gate at the sample generator and trigger on `SOURCE.txt`. (The paths below are
written for running the gate **from this `examples/` directory** as if it were a repo root —
in a real repo, `GENERATED_SINK_COMMIT_GATE_SOURCE_PATHS` is repo-root-relative and the hook lives in
`.git/hooks/pre-commit`.)

```bash
export GENERATED_SINK_COMMIT_GATE=on
export GENERATED_SINK_COMMIT_GATE_GENERATOR="python3 sample-generator.py --check"
export GENERATED_SINK_COMMIT_GATE_SOURCE_PATHS="SOURCE.txt"
```

## Fresh sink → commit allowed

With `SINK.txt` matching a fresh render of `SOURCE.txt`, the gate runs the `--check` and
allows the commit:

```bash
# (in a repo where SOURCE.txt is staged and SINK.txt is byte-exact)
python3 ../generated-sink-commit-gate.py
echo "exit: $?"        # -> 0  (ALLOW: sink is in sync)
```

## Stale sink → commit blocked

Now change the source but DON'T regenerate the sink, then stage the source:

```bash
echo "a new line" >> SOURCE.txt
git add SOURCE.txt           # SINK.txt is now stale and unstaged
python3 ../generated-sink-commit-gate.py
echo "exit: $?"        # -> 1  (BLOCKED: "the checked-in generated sink is STALE")
```

The fix is never the bypass — it's to regenerate:

```bash
python3 sample-generator.py   # rewrite SINK.txt from the new SOURCE.txt
git add SOURCE.txt SINK.txt
python3 ../generated-sink-commit-gate.py
echo "exit: $?"        # -> 0  (ALLOW: regenerated, byte-exact again)
```

## Missing generator config → loud fail-closed

A staged source with no generator configured cannot be verified, so the gate refuses rather
than guessing:

```bash
GENERATED_SINK_COMMIT_GATE_GENERATOR="" python3 ../generated-sink-commit-gate.py
echo "exit: $?"        # -> 1  (BLOCKED: GENERATED_SINK_COMMIT_GATE_GENERATOR is not set — fail-closed)
```

## Deliberate, documented bypass (single commit)

For a known, deliberately-shipped stale snapshot only — it logs on use:

```bash
GENERATED_SINK_COMMIT_GATE_BYPASS=1 python3 ../generated-sink-commit-gate.py
echo "exit: $?"        # -> 0  (ALLOW, with a bypass warning on stderr)
```
