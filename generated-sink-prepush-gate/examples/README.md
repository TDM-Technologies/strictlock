# generated-sink-prepush-gate examples

This directory shows the backstop end-to-end against a real (tiny) generator.

- [`sample-generator.py`](sample-generator.py) — renders `SOURCE.txt` → `SINK.txt`; in
  `--check` mode exits non-zero iff `SINK.txt` is not a byte-exact regeneration. Stands in for
  your real manifest/index/schema/swagger builder.
- [`SOURCE.txt`](SOURCE.txt) — the canonical source.
- [`SINK.txt`](SINK.txt) — the checked-in generated artifact, currently fresh.
- [`pre-push-hook.example.sh`](pre-push-hook.example.sh) — what to drop into
  `.git/hooks/pre-push`.

A git `pre-push` hook is invoked with the remote name and URL as arguments and the refs being
pushed on stdin. This gate validates the *working-tree* sink, so it ignores both and works as
a plain drop-in. Git honors its exit code: `0` lets the push proceed, non-zero blocks it.

## Setup

Point the gate at the sample generator. Unlike the commit gate, there is **no** `SOURCE_PATHS`
trigger — the backstop always validates the terminal state on a push.

```bash
export GENERATED_SINK_PREPUSH_GATE=on
export GENERATED_SINK_PREPUSH_GATE_GENERATOR="python3 sample-generator.py --check"
```

## Fresh sink → push allowed

With `SINK.txt` matching a fresh render of `SOURCE.txt`:

```bash
python3 ../generated-sink-prepush-gate.py origin https://example.test/repo.git
echo "exit: $?"        # -> 0  (ALLOW: sink is in sync)
```

## Stale sink → push blocked (even with nothing staged)

This is the whole point of the backstop. Change the source and DON'T regenerate — no staging,
no commit needed; the terminal working-tree sink is stale and the push is refused:

```bash
echo "a new line" >> SOURCE.txt    # SINK.txt is now stale
python3 ../generated-sink-prepush-gate.py origin https://example.test/repo.git
echo "exit: $?"        # -> 1  (BLOCKED: "the checked-in generated sink is STALE")
```

Fix by regenerating (and committing), not bypassing:

```bash
python3 sample-generator.py        # rewrite SINK.txt from the new SOURCE.txt
python3 ../generated-sink-prepush-gate.py origin https://example.test/repo.git
echo "exit: $?"        # -> 0  (ALLOW: byte-exact again)
```

## Missing generator config → loud fail-closed

```bash
GENERATED_SINK_PREPUSH_GATE_GENERATOR="" python3 ../generated-sink-prepush-gate.py origin https://example.test/repo.git
echo "exit: $?"        # -> 1  (BLOCKED: GENERATED_SINK_PREPUSH_GATE_GENERATOR is not set — fail-closed)
```

## Bypass isolation

The commit gate's bypass does **not** disable this backstop. With a stale sink:

```bash
GENERATED_SINK_COMMIT_GATE_BYPASS=1 python3 ../generated-sink-prepush-gate.py origin https://example.test/repo.git
echo "exit: $?"        # -> 1  (still BLOCKED — only GENERATED_SINK_PREPUSH_GATE_BYPASS=1 bypasses this gate)
```
