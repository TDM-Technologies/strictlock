/**
 * Tests for the no-tautological-assertions rule. Same harness as
 * no-smoke-only-assertions.test.mjs: ESLint's RuleTester bound to node:test.
 */
import test from 'node:test';

import { RuleTester } from 'eslint';

import rule from './no-tautological-assertions.mjs';

RuleTester.afterAll = (fn) => fn();
RuleTester.describe = (name, fn) => fn();
RuleTester.it = (name, fn) => test(name, fn);
RuleTester.itOnly = (name, fn) => test.only(name, fn);

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
});

const FN = 'example.test.ts';
const t = (body) => `it('x', async () => { ${body} });`;

ruleTester.run('no-tautological-assertions', rule, {
  valid: [
    // Real assertion: subject is a call result.
    { code: t(`expect(add(1, 2)).toBe(3);`), filename: FN },
    // Near-miss: identical text but with calls — two calls can differ.
    { code: t(`expect(next()).toBe(next());`), filename: FN },
    { code: t(`expect(await load()).toEqual(await load());`), filename: FN },
    { code: t(`expect(new Id()).toEqual(new Id());`), filename: FN },
    // Near-miss: float semantics — binary operator is not "pure".
    { code: t(`expect(0.1 + 0.2).not.toBe(0.3);`), filename: FN },
    { code: t(`expect(0.1 + 0.2).toBeCloseTo(0.3);`), filename: FN },
    // Near-miss: subject varies — parameter / let / call-initialised const.
    { code: `it.each([[1, 1]])('x', (a, b) => { expect(a).toBe(b); });`, filename: FN },
    { code: t(`let x = 5; x = f(); expect(x).toBe(5);`), filename: FN },
    { code: t(`const x = f(); expect(x).toBe(5);`), filename: FN },
    // Near-miss: function subject (toThrow) is not a constant.
    { code: t(`expect(() => run()).toThrow();`), filename: FN },
    // Near-miss: expect.assertions is not a matcher chain.
    { code: t(`expect.assertions(1); expect(f()).toBe(1);`), filename: FN },
    // Constant subject but the matcher argument is real input.
    { code: t(`expect(1).toBe(compute());`), filename: FN },
    // Outside a test block → not analyzed.
    { code: `expect(1).toBe(1);`, filename: FN },
    // Not a test file → inert.
    { code: t(`expect(1).toBe(1);`), filename: 'helpers.ts' },
    // Marker escape hatch (default).
    {
      code: t(`/* TEST-CORRECTNESS: asserting the matcher library itself */ expect(NaN).toBe(NaN);`),
      filename: FN,
    },
    // Custom marker.
    {
      code: t(`// SELF-TEST: matcher sanity
        expect(1).toBe(1);`),
      filename: FN,
      options: [{ marker: 'SELF-TEST:' }],
    },
    // Baseline.
    {
      code: t(`expect(1).toBe(1);`),
      filename: 'legacy/weak.test.ts',
      options: [{ baseline: ['legacy/weak.test.ts'] }],
    },
  ],
  invalid: [
    // Self-comparison.
    { code: t(`expect(result).toBe(result);`), filename: FN, errors: [{ messageId: 'selfComparison' }] },
    {
      code: t(`expect(user.name).toEqual(user.name);`),
      filename: FN,
      errors: [{ messageId: 'selfComparison' }],
    },
    // Literal vs literal (even when they differ — still not about the code).
    { code: t(`expect(5).toBe(5);`), filename: FN, errors: [{ messageId: 'literalVsLiteral' }] },
    { code: t(`expect(1).toBe(2);`), filename: FN, errors: [{ messageId: 'literalVsLiteral' }] },
    {
      code: t(`expect([1, 2]).toEqual([1, 2]);`),
      filename: FN,
      errors: [{ messageId: 'literalVsLiteral' }],
    },
    {
      code: t(`expect({ id: 1, tags: ['a'] }).toStrictEqual({ id: 1, tags: ['a'] });`),
      filename: FN,
      errors: [{ messageId: 'literalVsLiteral' }],
    },
    // Constant subject, no-arg or constant-arg matcher.
    { code: t(`expect(true).toBe(true);`), filename: FN, errors: [{ messageId: 'literalVsLiteral' }] },
    { code: t(`expect(true).toBeTruthy();`), filename: FN, errors: [{ messageId: 'constantSubject' }] },
    { code: t(`expect(null).toBeNull();`), filename: FN, errors: [{ messageId: 'constantSubject' }] },
    { code: t(`expect('abc').toMatch(/b/);`), filename: FN, errors: [{ messageId: 'constantSubject' }] },
    { code: t(`expect(undefined).toBeUndefined();`), filename: FN, errors: [{ messageId: 'constantSubject' }] },
    // One const hop.
    {
      code: t(`const expected = { id: 1 }; expect(expected).toEqual({ id: 1 });`),
      filename: FN,
      errors: [{ messageId: 'literalVsLiteral' }],
    },
    {
      code: t(`const x = 5; expect(x).toBe(5);`),
      filename: FN,
      errors: [{ messageId: 'literalVsLiteral' }],
    },
    // Negated chain still tautological.
    { code: t(`expect(1).not.toBe(2);`), filename: FN, errors: [{ messageId: 'literalVsLiteral' }] },
    // Member-form test block; two findings reported separately.
    {
      code: `test.only('x', () => { expect(2).toBe(2); expect(v).toBe(v); });`,
      filename: FN,
      errors: [{ messageId: 'literalVsLiteral' }, { messageId: 'selfComparison' }],
    },
    // A stale default marker does not justify when a custom one is configured.
    {
      code: t(`/* TEST-CORRECTNESS: stale */ expect(1).toBe(1);`),
      filename: FN,
      options: [{ marker: 'SELF-TEST:' }],
      errors: [{ messageId: 'literalVsLiteral' }],
    },
  ],
});
