# eslint-plugin-strictlock

**Structural ESLint gates against false-safety-net tests.**

The first JS / Gates-family sibling in the [StrictLock](../README.md) suite. StrictLock's
thesis is that governance belongs at the tool boundary, in code the agent doesn't control —
not in prose it can talk its way past. A test suite is governance too: it's the floor that's
supposed to catch a regression before it ships. A test that turns **green whether or not the
code is correct** is a hole in that floor. This plugin makes that hole a lint error.

It ships three rules today — [`no-smoke-only-assertions`](#rule-no-smoke-only-assertions),
[`no-tautological-assertions`](#rule-no-tautological-assertions) and
[`no-unasserted-result`](#rule-no-unasserted-result) — and isolates the JS toolchain inside its own `package.json`, so the rest of the StrictLock repo
stays Python + shell and zero-dep.

## Install

```bash
npm install --save-dev eslint-plugin-strictlock
```

Requires ESLint **>= 8** (declared as a peer dependency).

## Usage

### Flat config (`eslint.config.js`) — recommended

Spread the published recommended config:

```js
import strictlock from 'eslint-plugin-strictlock';

export default [
  // …your other config…
  ...strictlock.configs.recommended,
];
```

Or wire the rule by hand to pass options:

```js
import strictlock from 'eslint-plugin-strictlock';

export default [
  {
    plugins: { strictlock },
    rules: {
      'strictlock/no-smoke-only-assertions': 'error',
      'strictlock/no-tautological-assertions': 'error',
      'strictlock/no-unasserted-result': 'error',
    },
  },
];
```

### Legacy config (`.eslintrc.*`)

```jsonc
{
  "plugins": ["strictlock"],
  "extends": ["plugin:strictlock/recommended-legacy"]
}
```

…or rule-by-rule:

```jsonc
{
  "plugins": ["strictlock"],
  "rules": {
    "strictlock/no-smoke-only-assertions": "error",
    "strictlock/no-tautological-assertions": "error",
    "strictlock/no-unasserted-result": "error"
  }
}
```

## Rule: `no-smoke-only-assertions`

### What it does

Flags a test block (`it` / `test`, including member forms like `test.only` / `it.skip`)
whose assertions are **all smoke-only** — matchers that pass for almost any value and so
assert close to nothing:

- `toBeDefined`, `toBeUndefined`, `toBeNull`, `toBeTruthy`, `toBeFalsy`, `toBeNaN`
- `toContain`, `toContainEqual`
- `expect(x.length).toBeGreaterThan(0)` and the `>= 0` / `>= 1` "trivial floor" idioms
- a **bare** `expect(x)` with no matcher chain

The check is **all-or-nothing per block**: a single real assertion redeems the block. So
`expect(r).toBeDefined()` followed by `expect(r.id).toBe('a')` is fine — the second
assertion actually pins a value. The rule only fires when *every* assertion in the block is
smoke.

It self-guards to **test files** (`*.test.{js,jsx,ts,tsx,cjs,mjs,…}`) and stays inert
everywhere else, so production code and helpers are never touched. A test that delegates to
a helper (`assertRendered(html)`) with no direct `expect` isn't flagged — the rule can't see
inside the helper and won't guess.

### Why

A born-weak test is worse than no test. No test is an obvious gap; a smoke-only test is a
**false safety net** — it sits in the suite looking protective, stays green through the exact
regression it appears to guard, and quietly raises everyone's confidence in code nothing is
actually checking. Audits of large suites routinely find these are the *majority* of weak
tests. This rule turns "looks tested" into "is tested," mechanically, at lint time.

### Options

All behavior is configured through standard ESLint rule options (validated by a JSON
schema). The **default is strict**: no baseline, the built-in matcher set, and the
`TEST-CORRECTNESS:` marker.

```jsonc
{
  "strictlock/no-smoke-only-assertions": ["error", {
    // Test files (forward-slash, relative to the lint cwd) to grandfather in.
    // The rule is inert for these. Default: [] (strict).
    "baseline": ["legacy/weak.test.ts"],

    // Inline comment marker that justifies a deliberately-minimal smoke test.
    // Default: "TEST-CORRECTNESS:".
    "marker": "TEST-CORRECTNESS:",

    // Extra matcher names to treat as smoke-only, on top of the built-ins
    // (e.g. project-specific custom matchers). Default: [].
    "extraSmokeMatchers": ["toBeOk"]
  }]
}
```

| Option | Type | Default | Purpose |
|---|---|---|---|
| `baseline` | `string[]` | `[]` | Grandfather known-weak files so adoption stays green. |
| `marker` | `string` | `"TEST-CORRECTNESS:"` | The inline escape-hatch marker (see below). |
| `extraSmokeMatchers` | `string[]` | `[]` | Promote custom matchers into the smoke set. |

#### The escape hatch

Some smoke tests are legitimate and deliberate — a render-only smoke check whose real
behavior is covered by an end-to-end test, for instance. Justify it up front with an **inline
marker comment inside the test block**:

```js
it('renders without throwing', () => {
  // TEST-CORRECTNESS: render-only smoke; behavior is covered in e2e
  expect(html).toContain('Hi');
});
```

The marker makes the exception **auditable**: a reviewer can grep every smoke test that was
waved through and the reason it was. It's the same fail-closed discipline as the rest of
StrictLock — the exception is allowed, but it's explicit and on the record, not silent.

#### The baseline

Adopting on an existing suite without grandfathering would light up every pre-existing weak
test at once. List those files in `baseline` instead: the rule goes inert for exactly those
files while **new files and new violations still error**. Treat the baseline as a remediation
worklist — strengthen the tests and remove the entries; never add to it. (Ship the baseline
*list* in your config; the content is yours, and this plugin carries none.)

## Rule: `no-tautological-assertions`

### What it does

Flags an `expect(...)` assertion whose outcome is fixed by the test text alone, so it can
never depend on the code under test:

- **self-comparison** — `expect(user.name).toEqual(user.name)` (same text both sides, no calls)
- **literal vs literal** — `expect(5).toBe(5)`, `expect([1, 2]).toEqual([1, 2])`
- **constant subject** — `expect(true).toBeTruthy()`, `expect('abc').toMatch(/b/)`

It follows one `const` hop, so `const x = 5; expect(x).toBe(5)` is caught too. A value counts
as constant only if it is a literal, a call-free array/object of constants, a unary operator
on one, or a `const` bound to one.

Deliberately **not** flagged: anything with a call, `await` or `new` (`expect(next()).toBe(next())`
— two calls can differ), arithmetic (`expect(0.1 + 0.2).toBeCloseTo(0.3)`), parameters and
`let` bindings (`it.each` table parameters), and function subjects
(`expect(() => f()).toThrow()`).

It only looks inside `it` / `test` blocks of test files, including `.only` / `.skip` /
`.concurrent` forms and the `.each` forms (`it.each([...])(name, fn)` and the tagged-template
`test.each\`...\`(name, fn)`). `test.describe` / `test.step` bodies are not test blocks. The
message wording is neutral on purpose: under `.not`, `expect(x).not.toEqual(x)` can never
*pass*, and the rule reports it all the same — the outcome is fixed by the test text.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `baseline` | `string[]` | `[]` | Grandfather known files (rule inert for them). |
| `marker` | `string` | `"TEST-CORRECTNESS:"` | Inline comment that justifies the exception. |

```js
it('matcher library sanity', () => {
  // TEST-CORRECTNESS: exercising Object.is semantics of the matcher itself
  expect(NaN).toBe(NaN);
});
```

## Rule: `no-unasserted-result`

### What it does

Flags a variable bound from a call (`const r = f()`, `await f()`, `new X()`) inside a test
block whose value is then dropped — never read, or read only where nothing can assert on it
(`console.log(r)`, `void r`, a bare `r;` statement). The test computed something and checked
nothing about it.

Conservative on purpose: any other use counts as consumed — passing it to a call, using it in
`expect`, returning it, calling it (cleanup handles), deriving another value. A destructuring
binding is flagged only when none of its names are used. Calls rooted at `jest`, `vi` or
`sinon` are skipped, since `const spy = vi.spyOn(...)` installs a spy even if `spy` is never read.

Not covered yet: a result that is *read* but never reaches any assertion (a stricter mode is
sketched in the design spec, `dev/ESLINT-SEMANTIC-RULES-SPEC.md` §6).

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `baseline` | `string[]` | `[]` | Grandfather known files (rule inert for them). |
| `marker` | `string` | `"TEST-CORRECTNESS:"` | Inline comment that justifies the exception. |
| `extraMockRoots` | `string[]` | `[]` | Extra callee roots to treat as mocking setup calls, on top of the built-ins below. |

Built-in mock roots (their bindings may go unread): the globals `jest`, `vi` and `sinon`; any
import of `vi` / `jest` / `sinon` / `mock` from `vitest`, `@jest/globals`, `sinon`, `node:test`
or `test` — including aliases (`import { vi as v } from 'vitest'`) and a default `sinon`
import; and any call path through `.mock` (node:test's `t.mock.fn()` / `t.mock.method()`).
`using` / `await using` declarations are skipped (disposal is their use). Tagged-template
and optional-chain initialisers (`sql\`...\``, `api?.fetch()`) are checked like any call.

Known limits (not flagged, by design): `console.log(r.x)` counts as consuming `r` (only
`console.*(r)` with the bare binding is a discard); `expect.soft` and TypeScript `as` /
`satisfies` / `!` wrappers around the initialiser are not unwrapped; `await x;` counts as
consuming `x`. It also analyses the `.each` forms the same way as `no-tautological-assertions`.

```js
it('constructs without side effects', () => {
  // TEST-CORRECTNESS: the constructor throwing is the failure; nothing to read back
  const widget = new Widget();
});
```

## The marker silences the whole test block

For all three rules, a single `TEST-CORRECTNESS:` marker comment anywhere inside a test block
silences **every strictlock rule** for that whole block — not just the statement next to it, and
not just the rule the comment was written for. Keep blocks small when you use it. A per-statement
marker is an open design question (see `dev/ESLINT-SEMANTIC-RULES-SPEC.md` §8 Q8).

## What it prevents, detects, and can't address

In the StrictLock house style — be honest about the boundary of the mechanism.

- **Prevents (its job).** A *new* test whose assertions are all smoke-only never lands green.
  The error fires at lint time, before the false safety net is in the suite — the same
  preventive, fail-closed posture as the gates: catch it at the boundary, not after.
- **Detects (as a side effect).** Run it across an existing suite and the violation list is a
  map of your born-weak tests — the worklist you'd otherwise have to find by hand.
- **Can't address (be clear).** These are **syntactic** checks on assertion *shape* and on
  what a test does with its results, not semantic judges of test *quality*. Still uncovered:
  a strong-looking matcher asserting the **wrong value** (`expect(total).toBe(0)` when it
  should be `42` — a fact about intent that no lint rule can see; a mutation-testing gate is the
  usual answer), an expected value **computed by the function under test** (oracle reuse), an
  assertion on a value the test itself **stubbed** (mock echo), a result that is read but never
  reaches an assertion, and anything hidden inside a custom matcher or helper the rules can't
  see into. They raise the floor under your assertions; they do not certify they're correct.
  Pair them with review and real behavioral tests.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 TDM Technologies LLC.
