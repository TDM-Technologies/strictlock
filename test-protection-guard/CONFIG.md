# test-protection-guard — configuration reference

Every knob is an environment variable. Nothing is hardcoded to a machine, a project, or a
language; the repo root is discovered with `git rev-parse --show-toplevel` at run time. If
`TEST_PROTECTION_GUARD` is not `on`, the guard is inert (it allows everything, in either door).

## Enablement & mode

### `TEST_PROTECTION_GUARD`
`on` (case-insensitive) enables the guard. Anything else — unset, `off`, `0` — makes it inert.
This is the module's own switch; it is deliberately **not** shared with the other gates, so
enabling/disabling one gate never collaterally moves another.

### `TEST_PROTECTION_GUARD_MODE`
Which I/O door to run. Default `auto`.

| Value | Behaviour |
|---|---|
| `auto` (default) | Detect from stdin: a JSON object carrying `tool_name`/`tool_input` → **pretooluse**; empty or non-JSON stdin → **precommit**. |
| `pretooluse` (aliases `pre-tool-use`, `hook`) | Force the agentic-harness door: read a tool-call payload, act on `git commit`, emit an `allow` decision as JSON on stdout. |
| `precommit` (aliases `pre-commit`, `git`) | Force the git-hook door: analyse the staged diff in-tree, warn on stderr, exit 0. Never writes JSON to stdout. |

Pin the mode in any environment where stdin is unpredictable, or to make a test deterministic.

## Classification

A flag requires **both** a test file and a non-test source file in the same staged commit. These
two globs decide which is which. Both are `os.pathsep`-separated lists
(`:` on Linux/macOS, `;` on Windows). A glob **without** a `/` is matched against the file's
**basename**; a glob **with** a `/` is matched against the **repo-relative path**.

### `TEST_PROTECTION_GUARD_TEST_GLOBS`
Globs identifying **test** files. Default (covers the common stacks):

```
*.test.js *.test.jsx *.test.ts *.test.tsx *.test.mjs *.test.cjs
*.spec.js *.spec.jsx *.spec.ts *.spec.tsx
test_*.py *_test.py *_test.go *_test.rs *_spec.rb *Test.java *Tests.cs
*__tests__/*
```

`*__tests__/*` is a **path** glob (it contains `/`), so it matches the JS/React
`src/__tests__/foo.ts` directory convention. Other directory layouts (`tests/`, `spec/`)
that don't use a recognised filename suffix need an override.

### `TEST_PROTECTION_GUARD_SOURCE_GLOBS`
Globs identifying **non-test source**. A staged file that matches one of these and is **not** a
test file is "source". Default:

```
*.js *.jsx *.ts *.tsx *.mjs *.cjs *.mts *.cts
*.py *.go *.rs *.rb *.java *.cs *.kt *.swift *.c *.cc *.cpp *.h *.hpp *.php *.scala
```

> Override both to retarget the guard to any stack — e.g. `*.feature` tests against `*.step`
> sources. Test-ness wins ties: a file matching both globs is treated as a test.

## Detection

### `TEST_PROTECTION_GUARD_ASSERTION_RE`
A regex; a changed/removed line in the staged **test** diff that matches it counts as an
"existing assertion". It is matched against the line's **code** — the leading `-` and the
indentation are stripped first — so syntax anchors like `^assert` behave. The default requires
assertion *syntax* (a call `(`, a method `.`, a `!` macro, or `assert` at statement start), so
the bare English word "assert" in a comment or docstring does **not** match:

```
\bexpect\s*\(              JS / Jest / Vitest / Chai
\bassert(?:_eq|_ne)?!      Rust: assert! / assert_eq! / assert_ne!
\bassert\w*\s*\(           JUnit / unittest / xUnit: assertEqual( assertTrue( assert(
\b(?:assert|require)\s*\.   Go testify: assert.Equal / require.NoError
^assert\b                  Python / pytest: `assert <expr>` at statement start
\brequire\s*\(             Go testify: require(...)
\.should\b                 Chai / RSpec
```

Only **removed/changed** lines (`-` in a `-U0` diff, excluding the `---` header) are scanned;
pure additions (`+`) are new assertions and never flag. A malformed override regex is rejected
with a stderr note and the built-in default is used (an advisory guard must not crash on config).

> The default is a **recall-over-precision heuristic** — it deliberately accepts some false
> positives (its whole posture is advisory). If you override it, **keep the pattern linear** —
> no nested quantifiers like `(a+)+` — because it runs over diff text without a regex timeout, so
> a catastrophically-backtracking pattern could hang the guard (the built-in default is
> linear-safe; the scanned slice of each line is also length-capped as a backstop).

### `TEST_PROTECTION_GUARD_TOKEN`
The justification token. Default `TEST-CORRECTNESS:`. When present, the commit is recorded as
`justified` rather than `flagged` (and no stderr warning is printed). Where to put it:

- **inline** in the staged test (e.g. `// TEST-CORRECTNESS: spec changed; 4 is correct now`) —
  works in **both** doors.
- in the **`git commit -m` message** — works in **PreToolUse** mode only (the message is on the
  command line there; a git `pre-commit` hook does not yet have the message).

## Logging

The log is the point — it is what a human reviews at close-out. It is best-effort and append-only;
a logging failure never changes the decision. One of:

### `TEST_PROTECTION_GUARD_LOG_DIR`
A **directory**; the guard writes/extends `test-protection-guard.log` inside it (the house
convention, matching the other gates). Recommended: a stable path outside any worktree, so a
pruned worktree doesn't take the audit trail with it.

### `TEST_PROTECTION_GUARD_LOG`
An explicit log **file** path. Overrides `_LOG_DIR`. Useful for tests and for pointing several
repos at one trail.

If neither is set, no log is written — and the guard's value is largely lost, since the teeth are
in the review. Configure one.

## A note on git internals

The guard strips `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` before its own git calls and
resolves the worktree via `git rev-parse --show-toplevel`, so it behaves correctly inside linked
worktrees and from a `cd <worktree> && git commit …` command (PreToolUse mode parses that prefix).
Every git failure is treated as fail-open (allow) — this is an advisory guard, not a gate.
