# eslint-plugin-strictlock wave 2 — semantic test-quality rules (design spec)

Date: 2026-10-16. Status: **draft for Tim's read — no code written, nothing decided.** Direction
calls are listed in §8 as open questions.

Starting point: `eslint-plugin-strictlock/rules/no-smoke-only-assertions.mjs`
(wave 1) catches tests whose assertions are all *weak by shape*. Its README "Can't address"
section names what is left: tautologies, wrong-value assertions, unasserted behavior. This spec
covers four lint rules for most of that gap (§3–§6) and explains why the remainder (wrong values)
belongs to a different mechanism (§7).

---

## 1. Constraints (carried from wave 1 and the StrictLock thesis)

- Deterministic gate at the tool boundary; the agent cannot talk past a lint error.
- **Pure AST + ESLint scope analysis.** No type information (no `parserServices`, no tsconfig
  requirement), so the plugin runs on JS and TS alike and stays zero-runtime-dependency beyond the
  `eslint` peer.
- Same guard as wave 1: inert outside `*.test.{js,jsx,ts,tsx,cjs,mjs}`.
- Same two escape hatches, same semantics (§2).
- Prefer a missed detection over a false positive. A rule that cries wolf gets baselined wholesale
  and stops being a gate. Each rule below says what it deliberately does *not* flag.

## 2. Shared infrastructure (build once, before the first rule)

Wave 1 keeps these helpers private inside the rule file. Wave 2 has four rules needing the same
pieces, so one option is a small `rules/_shared/` module (underscore-prefixed, not exported as a
rule):

| Piece | Source today | Wave 2 use |
|---|---|---|
| `isTestFile`, `relForward` | wave-1 rule file | all four rules |
| `isTestCall` / `hasFnArg` (find `it`/`test` blocks) | wave-1 rule file | all four |
| `asExpectMatcher` (parse `expect(a).[not.]matcher(b)` → `{subject, matcher, args}`) | wave-1 rule file; currently returns only `matcher, arg0` | needs the **subject** node (`expect`'s argument) too |
| marker check (`getCommentsInside` + regex) | wave-1 rule file | all four |
| baseline lookup | wave-1 rule file | all four |
| **new:** `resolveToInit(node, scope)` — follow an `Identifier` through `context.sourceCode.getScope(node)` to its single `const` initializer, else `null` | — | rules 2, 3, 4 |
| **new:** `isPure(node)` — literal / template-without-expressions / identifier resolving to a `const` literal / unary-on-pure / array-or-object of pure | — | rule 1 |

**Escape hatch (all rules).** Inline comment `// TEST-CORRECTNESS: <reason>` inside the test
block (or on the line directly above the offending statement) silences that block. Option `marker`
overrides the string. Shared option across rules so one marker string audits everything:
`grep -rn "TEST-CORRECTNESS:"` lists every waved-through exception and its reason.

**Baseline (all rules).** Per-rule `baseline: string[]` of test files (forward-slash, cwd-relative)
that make the rule inert for that file. Rule-specific, so adopting rule 1 on a legacy suite does
not force baselining rule 4's findings. Baseline = remediation worklist; drain it, don't grow it.

**Packaging.** Each rule is its own file + `.test.mjs` (RuleTester), registered in
`eslint-plugin-strictlock/index.mjs`. Whether new rules join `recommended`
is a staging call (§8 Q3).

**Test-framework scope.** Wave 1 recognizes `it`/`test` and `expect(...)` chains (Jest / Vitest /
Playwright-expect shape). Wave 2 inherits that. `assert.*` (node:assert, chai) is out of scope
for the first cut (§8 Q4).

---

## 3. Rule 1 — `no-tautological-assertions`

**Intent.** Flag assertions whose outcome is fixed by the test text alone and so cannot depend on
the code under test.

### Detection (pure AST)

On each `expect(subject).[not.]matcher(...args)` parsed by `asExpectMatcher`:

1. **Self-comparison.** For equality-family matchers (`toBe`, `toEqual`, `toStrictEqual`,
   `toBeCloseTo`, `toMatchObject`, `toHaveProperty` with one arg excluded): subject and `args[0]`
   are the same *source text* after `sourceCode.getText()` normalization, **and** contain no call
   expression and no `await`/`yield`/`new` (so `expect(next()).toBe(next())` is not flagged — the
   two sides can legitimately differ).
2. **Literal vs literal.** `isPure(subject) && isPure(args[0])` — e.g. `expect(1).toBe(1)`,
   `expect('a').toEqual('a')`, `expect([1,2]).toEqual([1,2])`.
3. **Constant subject, matcher on its own value.** `expect(true).toBe(true)`,
   `expect(true).toBeTruthy()`, `expect(null).toBeNull()`, `expect(0).toBeFalsy()` — subject is
   `isPure` and the matcher takes no argument (or the argument is also pure).
4. **`expect(constant)` with *any* matcher** where subject is a pure literal and the matcher is
   not one the test is plausibly *exercising* (e.g. `expect(0.1 + 0.2).toBeCloseTo(0.3)` is a
   legitimate numeric-semantics test — see false positives). Report only when the subject is a
   bare literal (no operators) so the arithmetic case is skipped by construction.
5. **Assertion on a pure local.** `const x = 5; expect(x).toBe(5)` — via `resolveToInit`, subject
   resolves to a pure `const` initializer and arg is pure. Same family as case 2.

Message names the category (self-comparison / literal-vs-literal / constant-subject) so the fix is
obvious.

### True positives

```js
expect(result).toBe(result);                 // self-comparison
expect(5).toBe(5);                           // literal vs literal
expect(true).toBe(true);                     // constant subject
const expected = { id: 1 };
expect(expected).toEqual({ id: 1 });         // pure local vs pure literal
expect(user.name).toBe(user.name);           // self-comparison, member chain, no calls
```

### Likely false positives and mitigations

| Case | Why it is legitimate | Mitigation |
|---|---|---|
| `expect(NaN).toBe(NaN)` / `Object.is` semantics tests | Testing the matcher itself (rare; only in assertion-library test suites) | marker; no special-casing |
| `expect(0.1 + 0.2).not.toBe(0.3)` | Documents float behavior | subject has an operator → not "bare literal" → skipped by construction |
| `expect(fn()).toBe(fn())` memoization / referential identity | Two calls can differ | any call/`new`/`await` on either side → skip |
| `expect(a).toBe(a)` where `a` is a getter-backed or Proxy-backed value | Identity check for stable references | marker. Rare enough to accept |
| `expect(() => {...}).toThrow()` | Subject is a function literal, not "pure" | `isPure` does not accept functions → not flagged |
| `expect.assertions(1)` / `expect.hasAssertions()` | Not a matcher chain | `asExpectMatcher` already requires a matcher call |
| Table-driven tests: `it.each([[1,1]])('%s', (a,b) => expect(a).toBe(b))` | `a`,`b` are parameters, not consts | identifiers resolve to parameters → `resolveToInit` returns null → skipped |

### Effort / confidence

Smallest of the four. The AST work is shallow; the cost is `isPure` edge cases and the RuleTester
matrix. Estimate: **~1 day** including false-positive tuning against a real suite. Expected
precision: high.

---

## 4. Rule 2 — `no-oracle-reuse`

**Intent.** Flag a test whose *expected* value is produced by calling the same function the test is
exercising. The test then compares the function with itself; any bug appears on both sides.

### Detection (AST + scope)

1. For each `expect(subject).matcher(expectedArg)` in an equality-family matcher, extract the
   **callee identity** of any call in `subject`: the leftmost identifier or `obj.method` path
   (`calc.total`, `parse`, `utils.format`). Call this the *SUT callee set*.
2. Compute the **expected-side callee set**: calls found in `expectedArg`, plus — via
   `resolveToInit` — calls found in the initializer of an identifier `expectedArg` resolves to
   (one hop, `const` only; deeper chains are not followed, §8 Q5).
3. Report when the intersection is non-empty.
4. Matching is by *resolved variable* where possible: both callee roots must resolve (via scope) to
   the same `Variable` (same import binding or same local). Falls back to textual path equality
   for member chains (`calc.total` ↔ `calc.total`).

### True positives

```js
expect(total(items)).toBe(total(items));            // also caught by rule 1 (self-comparison);
                                                    // here via different args below
expect(formatPrice(5)).toBe(formatPrice(5.0));      // same callee on both sides
const expected = parse(input);
expect(parse(input)).toEqual(expected);             // one hop through const
expect(slugify(s)).toBe(slugify(s).toLowerCase());  // expected derived from the SUT
```

The 4th example shows the main value: the expected value is *derived from* the SUT's own output
rather than independently stated.

### Likely false positives and mitigations

| Case | Why it is legitimate | Mitigation |
|---|---|---|
| **Idempotence / involution**: `expect(normalize(normalize(x))).toBe(normalize(x))`, `expect(decode(encode(x))).toBe(x)` | Property-style tests intentionally compose the SUT | Allowlist structural patterns: (a) nested call of the same callee inside its own argument list on one side; (b) round-trip pair where the expected side is a plain identifier (decode∘encode → `x`) — expected side has no SUT call so it already passes. Idempotence needs (a). |
| Equivalence between *two different implementations* (`fastSort` vs `Array.prototype.sort`) | Reference-oracle testing, which is valid | Different callees → no intersection → not flagged |
| Helper callees reused for setup (`makeUser()` on both sides) | Fixture builders are not the SUT | Option `ignoreCallees: string[]` (default includes names matching `/^(make|create|build|mock|fixture|given)/i`); marker for the rest |
| Calling a *getter-style accessor* both sides (`store.get('a')` after `store.set`) | State-based tests | Flag only when both calls have identical argument source text **or** the expected side is *only* the SUT call (no other terms). Otherwise skip — conservative cut |
| Deterministic clock/random reuse | Same callee different seeds | Identical-args requirement above excludes most |

The "identical args" tightening is the main precision lever: it narrows the rule from "same
function on both sides" to "same function *and* same inputs on both sides", which is nearly always
a real oracle reuse.

### Effort / confidence

Medium. Scope analysis and the allowlist logic are where the time goes. Estimate: **~2–3 days**
including tuning. Expected precision: medium-high once the identical-args restriction is in;
recall deliberately limited (indirect reuse through helpers, multi-hop consts, is not followed).

---

## 5. Rule 3 — `no-mock-echo`

**Intent.** Flag an assertion that only re-reads a value the test itself injected through a stub,
so the assertion passes regardless of the code under test.

### Detection (AST + scope)

Recognize **stub declarations** in the same test block (or in `beforeEach`/`beforeAll` of the
enclosing `describe` — see below): calls of the shape `<target>.mockReturnValue(V)`,
`.mockResolvedValue(V)`, `.mockReturnValueOnce(V)`, `.mockImplementation(() => V)` (arrow with
expression body), `vi.fn(() => V)` / `jest.fn(() => V)` with literal `V`, and
`jest.spyOn(o, 'm').mockReturnValue(V)` / `vi.spyOn`. Record `(stubbedTarget, V)` where `V` is
`isPure`.

Then, for each `expect(subject).matcher(expectedArg)`:

1. **Direct echo.** `subject` is a call to the stubbed target (`expect(mockFn()).toBe(5)`, or
   `await` of it for `mockResolvedValue`) and `expectedArg` is `isPure` and deep-equal (by source
   normalization) to `V`. The test never reaches production code.
2. **Echo through a binding.** `const r = stubbed(); expect(r).toBe(V)` via `resolveToInit`.
3. **Echo through the stub's own property.** `expect(mock.mock.results[0].value).toBe(V)` —
   asserting what the mock recorded returning.

**What is deliberately *not* flagged:** `subject` is a call to a *different* function (the SUT),
even when the stub's value flows through it — `mockReturnValue(5); expect(sut()).toBe(5)` where
`sut` is not the stubbed target is the *normal* "dependency returns X, SUT passes it along" test.
Distinguishing a passthrough SUT from nothing requires data-flow beyond AST, so it stays out
(§7 for the mutation-gate backstop).

### True positives

```js
const getPrice = vi.fn().mockReturnValue(5);
expect(getPrice()).toBe(5);                    // direct echo

jest.spyOn(api, 'fetchUser').mockResolvedValue({ id: 1 });
expect(await api.fetchUser()).toEqual({ id: 1 });   // spied method asserted directly

const mock = vi.fn(() => 'ok');
const r = mock();
expect(r).toBe('ok');                          // via binding
```

### Likely false positives and mitigations

| Case | Why it is legitimate | Mitigation |
|---|---|---|
| Testing the mock helper itself (in a test-utils package) | The mock *is* the SUT | marker; also `baseline` on that package's files |
| Stub in `beforeEach`, subject is the *SUT export that is itself the stubbed module member* (module-mock of the import under test) | Pattern misuse sometimes, but also a deliberate "wiring" check | Resolve subject callee to its import binding; if it is an import from a module mocked with `vi.mock`/`jest.mock` **and** has a stub → flag (it is an echo). If subject is a different binding → not flagged |
| `mockReturnValueOnce` sequences consumed by loops under test | Stub values legitimately reach assertions after SUT logic | Echo requires the *subject callee itself* to be the stubbed target; sequences consumed by an SUT never match |
| Reassigned stubs (`mock = vi.fn()` in different branches) | Scope can't tell which value is live | `resolveToInit` only follows `const`; `let`/reassigned → skip |
| `expect(mock).toHaveBeenCalledWith(args)` | Verifies the SUT called the dependency; this is a real assertion | Not a value-echo shape; never considered |

### Effort / confidence

Medium-large because stub shapes are framework-specific and there are many (jest, vitest, sinon,
`testdouble`). First cut: jest + vitest only. Estimate: **~2–3 days** for jest/vitest, +1 day per
additional library. Expected precision: high on the direct-echo shape; recall limited by design
(passthrough SUT is invisible to AST).

---

## 6. Rule 4 — `no-unasserted-result`

**Intent.** Flag a variable bound from a call in a test that never reaches any assertion — the test
computes something and then drops it.

### Detection (AST + scope)

Inside a test block, for each `VariableDeclarator` whose `init` is a call (or `await` of a call,
or `new`), where the bound name is a simple identifier or destructuring pattern:

1. Get the `Variable` from `sourceCode.getDeclaredVariables(declarator)` and walk
   `variable.references` (scope manager gives every read/write).
2. The result is **asserted** if *any* read reference is, transitively within the same test block:
   - inside an `expect(...)` argument or any matcher argument;
   - passed as an argument to a function whose name matches `assert*|verify*|check*|expect*|should*`
     (assertion-helper heuristic; extensible via option `assertionHelpers`);
   - used to derive another binding that is itself asserted (fixed-point over bindings in the
     block — transitive closure, bounded to one block);
   - returned from the test callback, or `await`/`return`ed in a promise chain the framework
     consumes (`return foo().then(...)`);
   - passed to `expect.soft` / `assert` / `t.is` style calls (shared with the rule-1 allowlist).
3. Otherwise the call result is unreferenced or only referenced by non-asserting statements
   (logging, reassigning, passing to another call that does not assert) → report on the declarator.
4. Also report the **bare statement** shape where it is clearly pointless: an `await call()`
   statement is *not* flagged (the call may be for side effects); only declared-but-never-read
   bindings are, since the author gave the value a name and then dropped it.

### True positives

```js
it('creates a user', async () => {
  const user = await createUser({ name: 'a' });   // bound, never read
  expect(true).toBe(true);
});

it('parses', () => {
  const ast = parse(src);                         // bound
  console.log(ast);                               // read, but only by console.* → not asserted
});

it('totals', () => {
  const a = total([1]);
  const b = total([2]);                           // b never reaches an expect
  expect(a).toBe(1);
});
```

### Likely false positives and mitigations

| Case | Why it is legitimate | Mitigation |
|---|---|---|
| Value used only in a *side-effect* setup call (`const server = start(); await client.hit(server.url)`) and assertions are on external state | Result feeds later steps; assertion elsewhere | Counts as **asserted** if the binding is read by any call whose result is itself asserted (transitive closure), or if it is used in any `await`ed call. Pure setup variables read by anything non-logging are given the benefit of the doubt unless the *test has no assertions at all through them*. Tunable via option `strict` (default false: only flag never-read-or-logging-only bindings) |
| Cleanup handles (`const unsub = subscribe(); ... unsub()`) | Called, not asserted | Any call-use (`name(...)`) of the binding counts as consumed |
| Assertions delegated to helpers with unusual names | Helper asserts internally | `assertionHelpers` option (regex strings) + marker |
| Snapshot style: `expect(render(x)).toMatchSnapshot()` | Inline, not bound | Not a bound-variable shape; unaffected |
| Variables used in `afterEach`-level assertions | Cross-block | Rule scopes to the single test block; bindings declared in `describe`/`beforeEach` are out of scope (not the rule's job) |
| Playwright/Testing-Library `const el = screen.getByRole(...)` then `await user.click(el)` | Action target, assertion follows via DOM state | Used as call argument → consumed; only never-read or log-only are flagged in default mode |

Default mode (conservative): flag only (a) never-read bindings and (b) bindings read exclusively by
a configurable no-op set (`console.*`, `void`, bare expression statement). `strict: true` adds
"read but does not reach an assertion via the closure in step 2".

### Effort / confidence

Highest FP-tuning cost: the whole value of the rule depends on how many reads count as
"asserting", and test styles diverge. Estimate: **~2–3 days** default mode, +1–2 days for `strict`
mode and tuning on real suites. Expected precision: high for never-read, medium for `strict`.

---

## 7. Wrong value targets — why lint cannot decide it, and the real mechanism

### Why it is outside lint's reach

`expect(total).toBe(0)` when `42` is correct is a *semantic* fact about the intended behavior. The
information that distinguishes right from wrong lives in the requirement (a spec, a ticket, the
author's head), not in the code or the test text. Both the test and the implementation can agree on
the wrong number: the AST is well-formed, the matcher is strong, nothing is self-referential. Any
lint heuristic ("is 0 a suspicious expected value?") is a guess about intent and will be wrong
often in both directions. It also never checks whether the test is sensitive to the code at all.

What *is* decidable is a different question: **does this test fail when the code is broken?** That
is mutation testing. It cannot say "42 is the right number", but it catches the practical
consequence: a test whose assertions are too weak or too loose survives mutants of the code it
claims to cover. A low mutation score on changed lines is the deterministic proxy for "these tests
would not notice if this code were wrong".

### Sketch: a separate mutation gate

- **Tool.** Stryker (`@stryker-mutator/core`, JS/TS, vitest/jest/mocha runners). Lives in the
  adopting project's devDependencies, *not* in `eslint-plugin-strictlock` — the plugin stays
  zero-dep. The StrictLock repo would ship a **gate script + config template** (shell/Python,
  consistent with the rest of the repo), not a lint rule.
- **Scope: changed lines only.** Full-suite mutation runs are far too slow for per-PR gating.
  Compute changed line ranges from `git diff --unified=0 <base>...HEAD`, hand Stryker the file list
  via `--mutate "file:startLine-endLine"`, and use `--incremental` (reuses results for unchanged
  code/tests) with the incremental file cached between CI runs.
- **Gate rule.** Mutation score on the mutated (changed) set below a threshold (e.g. 70–80%) fails
  the check; surviving mutants are printed with file:line so the author sees which assertions did
  not bite. Threshold and per-path overrides live in a config file. Escape hatch: the same
  `TEST-CORRECTNESS:` marker pattern, expressed as a Stryker `// Stryker disable next-line <mutator>: <reason>`
  comment (Stryker's native mechanism) plus an audit grep, and a baseline list of files exempt
  until remediated.
- **Where it runs.** CI (PR check) and optionally pre-merge; too slow for a pre-commit hook, so it
  is a different tier from the lint rules, which are instant.

### CI-cost notes

- Cost scales roughly as `(#mutants on changed lines) × (time to run the relevant tests)`. Stryker
  uses per-mutant test coverage analysis to run only tests that cover each mutant, which is the
  main reason changed-lines-only runs are tractable.
- Typical shape (to be measured per project, labeled as an estimate): a small PR (tens of changed
  lines) → low hundreds of mutants → minutes on 2–4 parallel workers. A large refactor PR can
  exceed 10–30 minutes; cap with `--timeoutMS`, `--concurrency`, and a max-mutants-per-run guard
  that downgrades to "warn" rather than blocking.
- Flaky tests make mutation results unreliable (a flaky pass looks like a surviving mutant, a flaky
  fail like a killed one). Gate should require the baseline test run to be green and deterministic
  before mutating; Stryker does a dry run first and aborts on failure.
- Equivalent mutants (changes that cannot alter behavior) produce unkillable survivors, so the
  gate needs a threshold below 100% and a documented way to disable a mutant with a reason.
- Cache: persist `reports/stryker-incremental.json` via CI cache keyed on the base branch to keep
  repeat runs cheap.
- Rough cost framing for Tim's read: lint rules in §3–§6 add ~seconds to CI; the mutation gate adds
  ~minutes per PR and some infra (cache, runner choice) — a different order of commitment.

### Honest boundary

Mutation score reflects *sensitivity*, not *correctness of intent*. A test that pins the wrong
value for an input that mutants do change still gets killed-or-not independent of whether the value
is right. It reduces, but does not remove, the wrong-value gap; the remaining gap is a
requirements-review problem.

---

## 8. Open questions (Tim's calls)

1. **Which of the four rules to build, and in what order?** Observation for the order: rule 1 is
   the cheapest and shares helpers with the rest; rule 4 has the largest FP-tuning surface; rules
   2 and 3 are mid-cost and mid-value. A natural "ladder" is 1 → 2 → 3 → 4, but any subset is
   independent.
2. **Is the mutation gate in scope for StrictLock at all**, or is §7 documentation-only (README
   "pair with a mutation tool" note) with no shipped gate script? If in scope: Stryker-specific, or
   a tool-agnostic gate contract (changed-lines in → pass/fail + survivors out) so other runners
   can plug in?
3. **Rollout staging:** add new rules to `configs.recommended` immediately (breaking for adopters
   — new errors on upgrade, softened only by per-rule baseline), or ship them opt-in for one
   release and promote later? A semver minor vs. major follows from this.
4. **Assertion-library coverage:** stay with Jest/Vitest `expect` chains, or also cover
   `node:assert`, chai (`expect().to.equal`), and `assert.equal`? Each adds a parser branch to
   `asExpectMatcher` and widens the FP surface.
5. **Depth of data-flow:** the specs follow one `const` hop. A two-or-three-hop follower catches
   more oracle reuse and mock echo but costs complexity and adds FP risk. One hop is the starting
   point here; deeper is a tuning decision after a first corpus run.
6. **Calibration corpus:** FP tuning needs real test suites. Candidates: StrictLock's own JS tests,
   Tim's other repos, a handful of public repos. Which is acceptable to run the rules against, and
   who owns the precision bar (e.g. "≤1 FP per 100 reported findings" as release criterion)?
7. **Rule naming/split:** keep four separate rules (finer baselines and markers), or merge 1+2 into
   one `no-self-fulfilling-assertions` rule? Separate rules are assumed above.
8. **Marker granularity:** wave 1 accepts the marker anywhere inside the test block. For rules 1–3
   a per-statement marker (line above the `expect`) is more precise. Both could be accepted; this
   spec assumes both.

## 9. Effort summary

| Rule | Estimate | Precision outlook | Main risk |
|---|---|---|---|
| `no-tautological-assertions` | ~1 day | high | `isPure` edge cases |
| `no-oracle-reuse` | ~2–3 days | medium-high (with identical-args restriction) | idempotence / property tests |
| `no-mock-echo` | ~2–3 days (jest+vitest) | high on direct echo | framework stub-shape sprawl |
| `no-unasserted-result` | ~2–3 days default, +1–2 `strict` | high default / medium strict | what counts as "asserting" |
| shared infra (§2) | ~0.5–1 day | n/a | refactor of wave-1 helpers without changing its behavior |
| mutation gate (§7) | separate project, ~3–5 days + per-project CI tuning | n/a | CI time, equivalent mutants, flaky tests |

Estimates include false-positive tuning and RuleTester coverage; they are single-engineer figures
and un-validated until a first corpus run (§8 Q6).
