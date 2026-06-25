# sink-resolver configuration

Everything is configured through **environment variables** — no machine-specific or
project-specific defaults are baked into the script. The repo root is resolved with
`git rev-parse --show-toplevel`.

`sink-resolver` is invoked **explicitly** by an adopter (from a merge-conflict handler, a
`post-merge` hook, or a CI step), so — like [`scope-lease`](../scope-lease/) and unlike a
`PreToolUse` gate — it is *not* inert-by-default. `SINK_RESOLVER` is an opt-out switch for
scripts that want one toggle: set it to a falsey value to make every verb a no-op. An explicit
call with `SINK_RESOLVER` unset still runs.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `SINK_RESOLVER` | no | `on` / `1` / `true` / `yes` to run (the default when unset). Any *other* value (e.g. `off`) makes every verb a no-op. |
| `SINK_RESOLVER_GENERATOR_CMD` | **yes** | The command that **regenerates the sink(s) in place** from the current (merged) sources. Run in the generator cwd with worktree-private git env stripped. A non-zero exit is fail-closed. |
| `SINK_RESOLVER_SINKS` | **yes** | The sink path(s) the resolver is allowed to auto-resolve — `os.pathsep`- or whitespace-separated. These are the paths you place under `merge=binary`. Absolute or repo-relative; folded to canonical repo-relative keys for comparison. A sink resolving **outside** the repo is fail-closed. |
| `SINK_RESOLVER_CHECK_CMD` | recommended | The **strong byte-oracle**: a generator `--check` that regenerates internally, mutates nothing, and exits non-zero on any drift. When set, it is the oracle for `resolve` and the verdict for `check`. When unset, `resolve` falls back to a weaker double-regenerate determinism check and `check` snapshots-regenerates-compares-and-restores-exact-bytes (net non-mutating; announced on stderr). |
| `SINK_RESOLVER_GENERATOR_CWD` | no | Working directory for the generator / check command (absolute, or repo-root-relative). Default: the repo root. |
| `SINK_RESOLVER_TIMEOUT` | no | Seconds before the generator / check command is treated as a fail-closed timeout. Default `120`. A non-integer or `<= 0` value falls back to the default (loudly). |
| `SINK_RESOLVER_LOG_DIR` | no | Directory for the append-only decision log (`sink-resolver.log`). Each resolve/escalate/error appends a JSON line (UTC timestamp, decision, reason, sinks). If unset, no log is written. This is your merge-resolution audit trail. |

`os.pathsep` is `;` on Windows and `:` on Linux/macOS — it applies to `SINK_RESOLVER_SINKS`.

## The generator / oracle contract

Two commands, with a clear division of labor:

- **`SINK_RESOLVER_GENERATOR_CMD` mutates**: it overwrites the sink(s) in place with a fresh
  render of the sources. `resolve` runs it after a sink-only conflict; the sources are already
  merged, so the regenerate is the correct union.
- **`SINK_RESOLVER_CHECK_CMD` does not mutate**: it is the byte-oracle — regenerate-internally,
  compare, exit non-zero on drift (the universal `--check` / `git diff --exit-code` convention).
  It proves the committed bytes equal a fresh render of the **current** sources.

Most generators expose both as one tool with a `--check` flag (e.g.
`build_manifest.py` / `build_manifest.py --check`), exactly like
[`generated-sink-commit-gate`](../generated-sink-commit-gate/) expects. Configure both for the
strong guarantee; without `CHECK_CMD` the determinism guard is weaker (it proves the generator is
deterministic, not that the sink matches the sources independently).

**Both commands must cover EVERY path in `SINK_RESOLVER_SINKS`.** `GENERATOR_CMD` must regenerate
every configured sink, and `CHECK_CMD` (when set) must verify every one. A sink the generator is
blind to would have its `ours` bytes staged on a merge — but `resolve`'s **coverage guard** is the
backstop: a conflicted sink the generator leaves unchanged is escalated to a human, not silently
finalized. Also keep the generator's *output* confined to the sink set — a generator that writes
other tracked files leaves them dirty after a resolve (out of contract).

## Per-OS examples

### Linux / macOS (bash/zsh)

```bash
export SINK_RESOLVER=on
export SINK_RESOLVER_SINKS='dist/manifest.json:docs/api/openapi.json'
export SINK_RESOLVER_GENERATOR_CMD='npm run build:manifest'
export SINK_RESOLVER_CHECK_CMD='npm run build:manifest -- --check'
export SINK_RESOLVER_LOG_DIR="$HOME/.agent/logs"

python3 sink-resolver.py resolve     # at a conflicted merge
python3 sink-resolver.py check       # in CI, on the merged result
```

### Windows (PowerShell)

```powershell
$env:SINK_RESOLVER = "on"
$env:SINK_RESOLVER_SINKS = "dist\manifest.json"
$env:SINK_RESOLVER_GENERATOR_CMD = "python build_manifest.py"
$env:SINK_RESOLVER_CHECK_CMD = "python build_manifest.py --check"
python sink-resolver.py resolve
```

## Exit codes

The contract a calling hook / CI step branches on:

| Exit | Meaning |
|---|---|
| `0` | OK — resolved / finalized, nothing to resolve, `--dry-run`, disabled no-op, or (`check`) the sink is fresh. |
| `1` | Error / **fail-closed** — a git failure, a misconfiguration, a generator that errored or timed out, a failed byte-oracle, or (`check`) the sink is **STALE**. |
| `2` | Usage error (argparse). |
| `4` | **ESCALATION** — a non-sink path is in conflict; a person is needed. Wrote nothing. (Same "stop, write nothing" family as `scope-lease`'s fence-loss exit 4.) |

## Decision log

When `SINK_RESOLVER_LOG_DIR` is set, every resolve / escalate / error appends a JSON line to
`sink-resolver.log` with a UTC timestamp, the decision, the reason, the oracle mode, and the
resolved sinks. That append-only trail is the merge-resolution audit exhaust — produced as a
byproduct of the resolver doing its job, the same posture as the rest of the suite. Logging is
best-effort: a log-write failure never changes a resolve outcome.
