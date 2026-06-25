# generated-sink-prepush-gate

**Don't let a stale generated artifact leave the machine.**

A [StrictLock](../README.md) module, and the SIBLING of
[`generated-sink-commit-gate`](../generated-sink-commit-gate/). Both enforce the same
property — never ship a *generated sink* (manifest, index, schema, swagger, generated README)
that has drifted from its source — but at different lines of defense.

The commit-time gate is the **first** line. A `--no-verify` commit, a deliberate
single-commit bypass, or a source change committed without its companion sink can let a stale
sink slip onto a branch anyway. `generated-sink-prepush-gate` is the **last** line: wired as a
git `pre-push` hook, it re-runs your generator in `--check` mode against the **terminal
working-tree state** on every push and **refuses the push** unless the checked-in sink is a
**byte-exact** match of a fresh regeneration. Fail closed, deny at the boundary, before it
leaves the machine.

## What it does

- Resolves the repo root with `git rev-parse --show-toplevel` — no hardcoded paths.
- Runs on **every** `git push`. Unlike the commit gate, there is **no staged-source
  trigger** — the backstop validates the terminal sink state regardless of what is about to
  ship. That is precisely what catches a stale sink the commit gate's trigger never saw.
- Runs your generator's `--check` mode (`GENERATED_SINK_PREPUSH_GATE_GENERATOR`): a
  regenerate-and-compare that exits non-zero iff the checked-in sink is not byte-exact. The
  gate **never** mutates your working tree and **never** auto-stages — fail and tell.
- Every blocking decision can be appended to an audit log (`GENERATED_SINK_PREPUSH_GATE_LOG_DIR`).

A git `pre-push` hook receives the remote name and URL as arguments and the refs being pushed
on stdin. This gate validates the *working-tree* sink, so it ignores both — it is a plain
drop-in `pre-push` hook with nothing extra to wire.

## Install

```bash
cp generated-sink-prepush-gate.py /path/to/your/repo/.git/hooks/pre-push
chmod +x /path/to/your/repo/.git/hooks/pre-push
```

Then configure it (below) and turn it on with `GENERATED_SINK_PREPUSH_GATE=on`. Git honors the hook's
exit code: `0` allows the push, non-zero blocks it. See [`examples/`](examples/) for an
end-to-end walkthrough.

## Configuration (environment)

Everything is configured by environment variables — no machine-specific or project-specific
defaults are baked in. If `GENERATED_SINK_PREPUSH_GATE` is not `on`, the hook is inert (allow-all).

| Variable | Required | Meaning |
|---|---|---|
| `GENERATED_SINK_PREPUSH_GATE` | yes | `on` (case-insensitive) enables the gate. Anything else makes it inert (allow-all). |
| `GENERATED_SINK_PREPUSH_GATE_GENERATOR` | yes (when enabled) | The generator command to run in **check** mode, e.g. `npm run build:manifest -- --check`. It MUST exit non-zero iff the checked-in sink is not a byte-exact regeneration, and MUST NOT mutate the tree. Parsed with shell-style word splitting; no shell is invoked. |
| `GENERATED_SINK_PREPUSH_GATE_GENERATOR_CWD` | no | Directory to run the generator from (absolute, or repo-root-relative). Default: the repo root. Useful when the generator lives in a subproject (e.g. `app`). |
| `GENERATED_SINK_PREPUSH_GATE_TIMEOUT` | no | Seconds before the generator is treated as a fail-closed timeout. Default `120`. A non-integer or non-positive value falls back to the default **loudly** — a misconfiguration can't silently disable the gate. |
| `GENERATED_SINK_PREPUSH_GATE_LOG_DIR` | no | Directory for the append-only decision log (`sink-prepush-gate.log`, one JSON line per decision). Your audit trail; if unset, no log is written. |
| `GENERATED_SINK_PREPUSH_GATE_BYPASS` | no | `1` skips the gate for a single push (logged on use). A distinct bypass per gate is deliberate — see below. |

Note there is **no** `SOURCE_PATHS` here: the backstop always validates the terminal state,
so it needs no trigger.

### Example

```bash
export GENERATED_SINK_PREPUSH_GATE=on
export GENERATED_SINK_PREPUSH_GATE_GENERATOR="npm run build:manifest -- --check"
export GENERATED_SINK_PREPUSH_GATE_GENERATOR_CWD="app"          # generator lives in app/
export GENERATED_SINK_PREPUSH_GATE_LOG_DIR="$HOME/.agent/logs"
```

## What it prevents, detects, and can't address

- **Prevents:** pushing a stale generated sink onto a remote — *including* one that got onto
  the branch via a `--no-verify` commit or a one-off commit-time bypass. The check runs on the
  terminal working-tree state regardless of how the sink got there, so it closes the holes the
  commit-time gate structurally can't.
- **Detects (and reports):** a misconfigured or broken generator (missing command, uninstalled
  tool, error, or hang) — all surface as a loud, named, fail-closed refusal of the push rather
  than a green light.
- **Can't address:** `git push --no-verify` (git skips the pre-push hook entirely), a
  deliberate `GENERATED_SINK_PREPUSH_GATE_BYPASS=1`, a sink already pushed *before* this gate was
  installed, or a generator whose own `--check` lies. This is the **last local** line of
  defense, not a server-side one — it keeps a stale commit off the remote from *your* machine,
  but it can't refuse a merge or police a push from a machine without the hook. Pair it with a
  server-side CI check that re-runs the same `--check` as the defense-in-depth peer.

## Why a separate bypass per gate?

If the commit-time and pre-push gates shared one bypass, bypassing the first line of defense
would silently disable the last line too — exactly the failure this layering exists to
prevent. So this gate honors **only** its own `GENERATED_SINK_PREPUSH_GATE_BYPASS`, never the commit
gate's `GENERATED_SINK_COMMIT_GATE_BYPASS`. (Its test suite proves the isolation: a stale push with the
commit gate's bypass set still blocks here.) When a gate blocks, the right move is almost
always to **regenerate the sink** — the bypass is for a documented, deliberately-shipped stale
snapshot, not for routine friction.

## Requirements

Python 3.8+ (standard library only) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
