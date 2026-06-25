# eslint-plugin-strictlock

**Structural ESLint gates against false-safety-net tests.**

The first JS / Gates-family sibling in the [StrictLock](../README.md) suite. StrictLock's
thesis is that governance belongs at the tool boundary, in code the agent doesn't control —
not in prose it can talk its way past. A test suite is governance too: it's the floor that's
supposed to catch a regression before it ships. A test that turns **green whether or not the
code is correct** is a hole in that floor. This plugin makes that hole a lint error.

It ships one rule today — [`no-smoke-only-assertions`](#rule-no-smoke-only-assertions) — and
isolates the JS toolchain inside its own `package.json`, so the rest of the StrictLock repo
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
    "strictlock/no-smoke-only-assertions": "error"
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

## What it prevents, detects, and can't address

In the StrictLock house style — be honest about the boundary of the mechanism.

- **Prevents (its job).** A *new* test whose assertions are all smoke-only never lands green.
  The error fires at lint time, before the false safety net is in the suite — the same
  preventive, fail-closed posture as the gates: catch it at the boundary, not after.
- **Detects (as a side effect).** Run it across an existing suite and the violation list is a
  map of your born-weak tests — the worklist you'd otherwise have to find by hand.
- **Can't address (be clear).** It is a **syntactic** check on assertion *shape*, not a
  semantic judge of test *quality*. A test with a strong-looking matcher asserting the wrong
  value (`expect(total).toBe(0)` when it should be `42`), a tautology hidden inside a custom
  matcher or helper it can't see into, or a meaningful behavior left entirely unasserted —
  all pass. It raises the floor under your assertions; it does not certify they're correct.
  Pair it with review and real behavioral tests.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 TDM Technologies LLC.
