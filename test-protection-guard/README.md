# test-protection-guard

**Flag a test that was quietly rewritten to pass — instead of fixing the code.**

A [StrictLock](../README.md) module (the Gates family). It catches one specific, corrosive
move: a test is failing *correctly* — it caught a real bug — and instead of fixing the code,
someone (often an agent, under pressure to go green) edits the test's **existing assertion** so
the expected value now matches the buggy output:

```diff
- expect(compute()).toBe(5);
+ expect(compute()).toBe(4);   // the code returns 4 because it's broken
```

That is not a born-weak smoke test and not a new minimal test — it is the same assertion shape
with a different expected value. A "smoke-only" linter (see the sibling
[`eslint-plugin-strictlock`](../eslint-plugin-strictlock/)) and a vocabulary-weakening differ
both miss it. The signal that *does* catch it is **co-commit coupling**: an existing test
assertion was changed/removed in the **same commit** as a change to non-test source. This guard
flags exactly that, and asks for a `TEST-CORRECTNESS:` justification.

## Advisory by design — the deliberate departure from the other gates

Unlike the fail-**closed** gates in this suite (`generated-sink-*-gate`, `commit-msg-gate`),
this guard is **advisory**:

- **It always allows. It never blocks.** A false positive on a legitimate refactor must not
  wedge work — that friction is what trains reflexive token use and turns the token into noise.
- **It fails open** on its own infra error (git failure, parse error, crash). An advisory nudge
  that blocked on its own bug would be worse than useless.
- **Its teeth are not in the gate.** Both the hook and the self-administered token are bypassable
  by the very actor they constrain. The real enforcement is a human's review of the decision log
  at close-out. The guard's only job is to make the coupling **visible and logged** so that review
  has something to read. (The git-mode logic is one `git diff <range>` away from a CI check — the
  natural home for real teeth, since local hooks are bypassable. See below.)

## Dual-mode: one script, one analysis core, two doors

The same coupling-analysis core ships behind two interchangeable I/O shells. Adopt either or both.

| Door | Catches | Where the warning lands | Install |
|---|---|---|---|
| **PreToolUse hook** (agentic harness, e.g. Claude Code) | the agent's `git commit` tool call | inline in the agent's context (`permissionDecisionReason`) + stderr — so it can self-correct *this turn* | a `PreToolUse` matcher in `settings.json` |
| **git `pre-commit` hook** (any repo / any harness / humans) | every commit | stderr at commit time | `cp` into `.git/hooks/pre-commit` (or wire via husky / `pre-commit` / lefthook) |

The mode is **auto-detected** — a tool-call JSON payload on stdin selects PreToolUse; empty stdin
(how git runs a hook) selects pre-commit — and can be pinned with `TEST_PROTECTION_GUARD_MODE`.

### Install — PreToolUse

```jsonc
// ~/.claude/settings.json  (see examples/settings.json)
{ "hooks": { "PreToolUse": [
  { "matcher": "Bash",
    "hooks": [ { "type": "command",
      "command": "python3 /abs/path/to/test-protection-guard/test-protection-guard.py" } ] } ] } }
```

### Install — git pre-commit

```bash
cp test-protection-guard/test-protection-guard.py /path/to/repo/.git/hooks/pre-commit
chmod +x /path/to/repo/.git/hooks/pre-commit
# git runs it with no stdin -> the guard picks pre-commit mode automatically.
```

Then turn it on with `TEST_PROTECTION_GUARD=on`. See [`examples/`](examples/) for a runnable
walkthrough of both doors.

## Configuration (environment)

Everything is configured by environment variables — no machine-specific or project-specific
defaults are baked in. If `TEST_PROTECTION_GUARD` is not `on`, the guard is inert (allow-all).
The repo root is resolved with `git rev-parse --show-toplevel`. Full reference: [`CONFIG.md`](CONFIG.md).

| Variable | Default | Meaning |
|---|---|---|
| `TEST_PROTECTION_GUARD` | — | `on` (case-insensitive) enables the guard. Anything else is inert. |
| `TEST_PROTECTION_GUARD_MODE` | `auto` | `auto` detects the door from stdin; `pretooluse` / `precommit` force it. |
| `TEST_PROTECTION_GUARD_TEST_GLOBS` | JS/TS/py/Go/Rust/… test patterns | `os.pathsep` globs that identify **test** files. A glob without `/` matches the basename; with `/`, the repo-relative path. |
| `TEST_PROTECTION_GUARD_SOURCE_GLOBS` | common code extensions | `os.pathsep` globs that identify **non-test source**. A staged file matching this and *not* a test glob is "source". |
| `TEST_PROTECTION_GUARD_ASSERTION_RE` | `expect(` / `assert*` / `assert_eq!` / `require(` / `.should` | regex; a changed/removed test line matching it is an "existing assertion". |
| `TEST_PROTECTION_GUARD_TOKEN` | `TEST-CORRECTNESS:` | the justification token that turns a flag into a logged-but-justified record. |
| `TEST_PROTECTION_GUARD_LOG_DIR` | — | directory for the append-only JSONL decision log (`test-protection-guard.log`). Your audit trail. |
| `TEST_PROTECTION_GUARD_LOG` | — | explicit log **file** path (overrides `_LOG_DIR`; handy for tests). |

## What it prevents, detects, and can't address

- **Surfaces (its whole job):** a commit that changes/removes an existing test assertion *and*
  edits non-test source together, with no `TEST-CORRECTNESS:` justification — the canonical
  "matched the test to the buggy output" move. It logs the coupling and warns loudly so a human
  review can catch it.
- **Doesn't prevent:** anything. It is advisory by design — the commit always proceeds. It will
  not stop a determined actor, and the token is self-administered.
- **Can't address on its own:** the enforcement gap. Because it never blocks and the token is
  bypassable, it is only as effective as the **close-out review of its log**. For real teeth,
  run the same coupling check in CI against the PR diff (`git diff <base>..<head>`) — local hooks
  are bypassable (`--no-verify`), a server-side check is not. The PreToolUse door also can't see a
  commit made outside the harness; that is exactly why the git door exists.

### Known limits of the heuristic (it is a signal, not a proof)

The detector is a line-level diff heuristic. It is deliberately tuned for recall on the common
case, and it has honest blind spots — none of which break its advisory contract, all of which the
log review and a CI diff can backstop:

- **It looks at the changed assertion *line*, not the value semantics.** A value-preserving edit
  that still rewrites an assertion line — a formatter reflow, a variable rename inside `expect(…)`,
  or deleting an obsolete assertion — will flag (a false positive). Deletion is flagged *on
  purpose*: removing a check alongside a code change is itself worth a glance.
- **A changed expected value on a *continuation* line is missed.** If the assertion is split across
  lines (`expect(x)\n  .toBe(5)`), or the value lives in an inline snapshot or an `it.each` data
  row, the changed `-` line carries no assertion token, so it reads as clean (a false negative).
- **Values that live outside the test file are out of scope.** A changed expectation in an external
  fixture or a `.snap` snapshot file is never seen — the guard only diffs files matching the test
  globs. A CI diff of those artifacts is the complement.
- **The PreToolUse door only sees commands it can parse.** It recognises `git commit`,
  `git -C <dir> commit`, `git -c k=v commit`, and a single `cd <dir> && …` prefix, but not a
  nested `cd`. The git `pre-commit` door has no such gap — it sees every commit — which is why
  adopting both is the robust posture.

### The justification token, in both doors

Add an inline comment in the staged test, e.g. `// TEST-CORRECTNESS: the spec changed; 4 is the
correct value now`. In **PreToolUse** mode the token is also honored in the `git commit -m`
message (it's on the command line). In **git pre-commit** mode the message isn't available to the
hook yet, so use the **inline** form there.

## Requirements

Python 3.8+ (standard library only) and `git` on `PATH`.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
