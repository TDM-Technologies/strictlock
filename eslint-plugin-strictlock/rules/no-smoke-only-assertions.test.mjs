/**
 * Tests for the no-smoke-only-assertions rule.
 *
 * Run with plain node — `node --test` drives ESLint's own RuleTester, no extra
 * test framework. RuleTester is itself a strong assertion engine: each `valid`
 * case must produce ZERO lint errors and each `invalid` case must produce the
 * EXACT errors declared (messageId + count), so these tests prove behavior —
 * they are deliberately the opposite of smoke-only.
 *
 * Code samples are plain JS so the default parser suffices; filenames end in
 * `.test.ts` because the rule self-guards to test files.
 */
import test from 'node:test';

import { RuleTester } from 'eslint';

import rule from './no-smoke-only-assertions.mjs';

// Bind RuleTester's describe/it/afterAll to node:test primitives.
RuleTester.afterAll = (fn) => fn();
RuleTester.describe = (name, fn) => fn();
RuleTester.it = (name, fn) => test(name, fn);
RuleTester.itOnly = (name, fn) => test.only(name, fn);

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
});

const FN = 'example.test.ts';

ruleTester.run('no-smoke-only-assertions', rule, {
  valid: [
    // A real value assertion.
    { code: `it('x', () => { expect(add(1,2)).toBe(3); });`, filename: FN },
    // Smoke + strong mixed → the strong one redeems the block.
    {
      code: `it('x', () => { expect(r).toBeDefined(); expect(r.id).toBe('a'); });`,
      filename: FN,
    },
    // Smoke-only but justified with the default escape-hatch comment.
    {
      code: `it('x', () => { /* TEST-CORRECTNESS: render-only smoke; behavior covered in e2e */ expect(html).toContain('Hi'); });`,
      filename: FN,
    },
    // Escape hatch with a custom marker string via options.
    {
      code: `it('x', () => { /* SMOKE-OK: trivial getter */ expect(v).toBeTruthy(); });`,
      filename: FN,
      options: [{ marker: 'SMOKE-OK:' }],
    },
    // Grandfathered file via the baseline option → rule inert for it.
    {
      code: `it('x', () => { expect(r).toBeDefined(); });`,
      filename: 'legacy/weak.test.ts',
      options: [{ baseline: ['legacy/weak.test.ts'] }],
    },
    // Not a test file → rule is inert even on smoke-only code.
    { code: `it('x', () => { expect(r).toBeDefined(); });`, filename: 'helpers.ts' },
    // Delegates to a helper, no direct expect → not flagged (can't see inside).
    { code: `it('x', () => { assertRendered(html); });`, filename: FN },
    // toHaveLength(n) asserts an exact count → strong.
    { code: `it('x', () => { expect(rows).toHaveLength(3); });`, filename: FN },
    // Non-trivial floor → strong.
    { code: `it('x', () => { expect(score).toBeGreaterThan(5); });`, filename: FN },
    // Pending test (no callback) → ignored.
    { code: `it('todo');`, filename: FN },
  ],
  invalid: [
    {
      code: `it('x', () => { expect(r).toBeDefined(); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // toContain alone is smoke-only.
    {
      code: `it('x', () => { expect(html).toContain('Foo'); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // The length>0 tautology.
    {
      code: `it('x', () => { expect(arr.length).toBeGreaterThan(0); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // Bare expect with no matcher → asserts nothing.
    {
      code: `it('x', () => { expect(value); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // Member-callee test forms (test.only / it.skip) still analyzed.
    {
      code: `test.only('x', () => { expect(r).toBeNull(); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // Multiple smoke matchers, still all weak.
    {
      code: `it('x', () => { expect(a).toBeUndefined(); expect(b).toBeDefined(); });`,
      filename: FN,
      errors: [{ messageId: 'smokeOnly' }],
    },
    // A project-defined custom matcher promoted to smoke via options.
    {
      code: `it('x', () => { expect(res).toBeOk(); });`,
      filename: FN,
      options: [{ extraSmokeMatchers: ['toBeOk'] }],
      errors: [{ messageId: 'smokeOnly' }],
    },
    // The default marker does NOT justify when a custom marker is configured.
    {
      code: `it('x', () => { /* TEST-CORRECTNESS: stale marker */ expect(r).toBeDefined(); });`,
      filename: FN,
      options: [{ marker: 'SMOKE-OK:' }],
      errors: [{ messageId: 'smokeOnly' }],
    },
  ],
});
