# generated-sink-commit-gate

**Refuse to commit a stale generated artifact.**

A [StrictLock](../README.md) module. A *generated sink* is any checked-in file that is
**derived** from canonical source by a generator command — a manifest, an index, a JSON
schema, an OpenAPI/Swagger document, a generated README, a rendered table. You commit the
sink so people and tools (and a stateless agent's cross-session memory) can read it without
re-running the generator. The hazard is silent: the instant a source changes but the sink is
not regenerated, every downstream reader desyncs, and nothing notices until a stale fact has
already propagated.

`generated-sink-commit-gate` makes "commit a stale sink" **structurally impossible**. Wired
as a git `pre-commit` hook, on any commit whose staged changes touch the configured source
paths it re-runs your generator in `--check` mode and **refuses the commit** unless the
checked-in sink is a **byte-exact** match of a fresh regeneration. Same posture as the rest
of StrictLock — fail closed, deny at the boundary, before the bad commit lands.

It is the **commit-time, first line of defense**. Its sibling
[`generated-sink-prepush-gate`](../generated-sink-prepush-gate/) is the pre-push backstop —
adopt both for layered enforcement.

## What it does

- Resolves the repo root with `git rev-parse --show-toplevel` — no hardcoded paths.
- **Triggers only** when the staged changes touch a configured **source path** prefix
  (`SINK_COMMIT_GATE_SOURCE_PATHS`). A commit that stages nothing in that set passes
  untouched — the gate is scoped, not a tax on every commit.
- Runs your generator's `--check` mode (`SINK_COMMIT_GATE_GENERATOR`): a
  regenerate-and-compare that exits non-zero iff the checked-in sink is not byte-exact. The
  gate **never** mutates your working tree and **never** auto-stages — it fails and tells; the
  human stays in the loop.
- A rebase / merge / cherry-pick in progress is skipped (those fire the commit hook but the
  post-operation commit re-validates).
- Every blocking decision can be appended to an audit log (`SINK_COMMIT_GATE_LOG_DIR`).

## Install

```bash
cp generated-sink-commit-gate.py /path/to/your/repo/.git/hooks/pre-commit
chmod +x /path/to/your/repo/.git/hooks/pre-commit
```

Then configure it (below) and turn it on with `SINK_COMMIT_GATE=on`. It applies to **every**
commit in that repo — human or agent. Git invokes a `pre-commit` hook with no arguments and
honors its exit code: `0` allows the commit, non-zero blocks it. See [`examples/`](examples/)
for an end-to-end walkthrough.

## Configuration (environment)

Everything is configured by environment variables — no machine-specific or project-specific
defaults are baked in. If `SINK_COMMIT_GATE` is not `on`, the hook is inert (allow-all).

| Variable | Required | Meaning |
|---|---|---|
| `SINK_COMMIT_GATE` | yes | `on` (case-insensitive) enables the gate. Anything else makes it inert (allow-all). |
| `SINK_COMMIT_GATE_GENERATOR` | yes (when enabled) | The generator command to run in **check** mode, e.g. `npm run build:manifest -- --check` or `python tools/gen.py --check`. It MUST exit non-zero iff the checked-in sink is not a byte-exact regeneration, and MUST NOT mutate the tree. Parsed with shell-style word splitting; no shell is invoked. |
| `SINK_COMMIT_GATE_SOURCE_PATHS` | yes (when enabled) | `os.pathsep`-separated list of repo-root-relative **source path prefixes** that trigger the check (e.g. `docs/manifest:openapi/spec.yaml`). A staged path under any prefix fires the gate. |
| `SINK_COMMIT_GATE_GENERATOR_CWD` | no | Directory to run the generator from (absolute, or repo-root-relative). Default: the repo root. Useful when the generator lives in a subproject (e.g. `app`). |
| `SINK_COMMIT_GATE_TIMEOUT` | no | Seconds before the generator is treated as a fail-closed timeout. Default `120`. A non-integer or non-positive value falls back to the default **loudly** — a misconfiguration can't silently disable the gate. |
| `SINK_COMMIT_GATE_LOG_DIR` | no | Directory for the append-only decision log (`sink-commit-gate.log`, one JSON line per decision). Your audit trail; if unset, no log is written. |
| `SINK_COMMIT_GATE_BYPASS` | no | `1` skips the gate for a single commit (logged on use). A distinct bypass per gate is deliberate — see below. |

`os.pathsep` is `:` on Linux/macOS and `;` on Windows — it applies to
`SINK_COMMIT_GATE_SOURCE_PATHS`.

### Example

```bash
export SINK_COMMIT_GATE=on
export SINK_COMMIT_GATE_GENERATOR="npm run build:manifest -- --check"
export SINK_COMMIT_GATE_SOURCE_PATHS="docs/manifest"
export SINK_COMMIT_GATE_GENERATOR_CWD="app"          # generator lives in app/
export SINK_COMMIT_GATE_LOG_DIR="$HOME/.agent/logs"
```

## What it prevents, detects, and can't address

- **Prevents:** committing a generated sink that has drifted from its source. The byte-exact
  `--check` runs *before the commit lands*, so a stale manifest/index/schema never enters
  history through a normal commit. That's the whole game — stop the drift at the boundary, not
  after it has propagated to every reader.
- **Detects (and reports):** a misconfigured or broken generator. A missing generator command,
  an uninstalled tool, a generator that errors or hangs — all surface as a loud, named,
  fail-closed refusal rather than a green light. The gate would rather stop you than guess.
- **Can't address:** a `--no-verify` commit (git skips local hooks entirely), a deliberate
  `SINK_COMMIT_GATE_BYPASS=1`, or a generator whose own `--check` lies about freshness. The
  gate is only as honest as the generator it runs and the hook git agrees to invoke. For the
  bypass / `--no-verify` hole, layer the sibling
  [`generated-sink-prepush-gate`](../generated-sink-prepush-gate/) so a stale sink that slips
  past commit time still can't leave the machine — and treat a server-side CI check as the
  defense-in-depth peer that catches what reaches the remote anyway.

## Why a separate bypass per gate?

If every fail-closed gate honored one shared bypass variable, disabling one would silently
disable them all. This gate has its own `SINK_COMMIT_GATE_BYPASS`, distinct from the pre-push
gate's `SINK_PREPUSH_GATE_BYPASS`, so a deliberate one-commit escape here never weakens the
backstop. When a gate blocks, the right move is almost always to **regenerate the sink** —
not to reach for the bypass. The bypass is for a documented, deliberately-shipped stale
snapshot, not for routine friction.

## Requirements

Python 3.8+ (standard library only) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
