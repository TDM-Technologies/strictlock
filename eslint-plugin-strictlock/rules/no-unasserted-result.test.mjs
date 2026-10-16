/**
 * Tests for the no-unasserted-result rule. Same harness as
 * no-smoke-only-assertions.test.mjs: ESLint's RuleTester bound to node:test.
 */
import test from 'node:test';

import { createRequire } from 'node:module';

import * as eslint from 'eslint';

import rule from './no-unasserted-result.mjs';

// ESLint 8's classic RuleTester is eslintrc-style; its flat-config tester lives
// at eslint/use-at-your-own-risk. ESLint >= 9's RuleTester is flat-config.
const require = createRequire(import.meta.url);
const eslintMajor = Number(require('eslint/package.json').version.split('.')[0]);
const RuleTester =
  eslintMajor < 9
    ? require('eslint/use-at-your-own-risk').FlatRuleTester
    : eslint.RuleTester;

RuleTester.afterAll = (fn) => fn();
RuleTester.describe = (name, fn) => fn();
RuleTester.it = (name, fn) => test(name, fn);
RuleTester.itOnly = (name, fn) => test.only(name, fn);

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
});

const FN = 'example.test.ts';
const t = (body) => `it('x', async () => { ${body} });`;

ruleTester.run('no-unasserted-result', rule, {
  valid: [
    // Result reaches an expect.
    { code: t(`const r = f(); expect(r).toBe(1);`), filename: FN },
    { code: t(`const r = await f(); expect(r.id).toBe(1);`), filename: FN },
    // Near-miss: derived value is what's asserted.
    { code: t(`const r = f(); const id = r.id; expect(id).toBe(1);`), filename: FN },
    // Near-miss: consumed by another call (setup chain), or returned.
    { code: t(`const server = start(); await client.hit(server.url);`), filename: FN },
    { code: t(`const p = f(); return p;`), filename: FN },
    // Near-miss: cleanup handle is called.
    { code: t(`const unsub = subscribe(); unsub();`), filename: FN },
    // Near-miss: destructuring where at least one name is used.
    { code: t(`const { a, b } = f(); expect(a).toBe(1);`), filename: FN },
    // Near-miss: init is not a call.
    { code: t(`const x = 5; const y = [1];`), filename: FN },
    // Near-miss: mocking-library setup call is a side effect, binding unused.
    { code: t(`const spy = vi.spyOn(api, 'get'); f();`), filename: FN },
    { code: t(`const spy = jest.spyOn(api, 'get').mockReturnValue(1); f();`), filename: FN },
    // Regression: `await x` consumes the value (settles/throws), not a discard.
    {
      code: t(`const found = screen.findByText('x'); fireEvent.click(btn); await found;`),
      filename: FN,
    },
    // Aliased mocking-library imports and node:test mocks are setup calls.
    {
      code: `import { vi as v } from 'vitest';
        it('x', () => { const spy = v.spyOn(api, 'get'); f(); });`,
      filename: FN,
    },
    {
      code: `import { jest as j } from '@jest/globals';
        it('x', () => { const spy = j.fn(); f(); });`,
      filename: FN,
    },
    {
      code: `import { mock } from 'node:test';
        it('x', () => { const m = mock.method(obj, 'f'); f(); });`,
      filename: FN,
    },
    {
      code: `import sandbox from 'sinon';
        it('x', () => { const stub = sandbox.stub(obj, 'f'); f(); });`,
      filename: FN,
    },
    { code: t(`const m = t.mock.fn(); f();`), filename: FN },
    // extraMockRoots option.
    {
      code: t(`const m = td.replace('./x'); f();`),
      filename: FN,
      options: [{ extraMockRoots: ['td'] }],
    },
    // `using` / `await using` bindings are disposed → their use. ESLint 8's
    // parser predates `using`, so this case runs on ESLint >= 9 only.
    ...(eslintMajor >= 9
      ? [{ code: t(`using handle = open();`), filename: FN, languageOptions: { ecmaVersion: 'latest', sourceType: 'module' } }]
      : []),
    // each-table test bodies are analyzed (and clean ones stay clean).
    { code: `it.each([1, 2])('x %i', (n) => { const r = f(n); expect(r).toBe(n); });`, filename: FN },
    // Not a test block: test.describe bodies are not analyzed.
    { code: `test.describe('s', () => { const r = f(); });`, filename: FN },
    // Declarations outside a test block are not analyzed.
    { code: `const r = f();`, filename: FN },
    { code: `describe('x', () => { const r = f(); it('y', () => { expect(1).toBe(1); }); });`, filename: FN },
    // Not a test file → inert.
    { code: t(`const r = f();`), filename: 'helpers.ts' },
    // Marker escape hatch (default) and custom.
    { code: t(`/* TEST-CORRECTNESS: constructor side effect is the point */ const r = f();`), filename: FN },
    {
      code: t(`// NO-ASSERT: warms the cache
        const r = f();`),
      filename: FN,
      options: [{ marker: 'NO-ASSERT:' }],
    },
    // Baseline.
    {
      code: t(`const r = f();`),
      filename: 'legacy/weak.test.ts',
      options: [{ baseline: ['legacy/weak.test.ts'] }],
    },
  ],
  invalid: [
    // Never read.
    { code: t(`const r = f(); expect(true).toBe(true);`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    { code: t(`const user = await createUser({ name: 'a' });`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    { code: t(`const o = new Thing();`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Only one of two results is dropped.
    {
      code: t(`const a = total([1]); const b = total([2]); expect(a).toBe(1);`),
      filename: FN,
      errors: [{ messageId: 'unasserted', column: 51 }],
    },
    // Read only by non-asserting uses.
    { code: t(`const ast = parse(src); console.log(ast);`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    { code: t(`const ast = parse(src); void ast;`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    { code: t(`const ast = parse(src); ast;`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Destructuring with every name dropped.
    { code: t(`const { a, b } = f();`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Written again but never read.
    { code: t(`let r = f(); r = g();`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Chained and tagged-template initialisers are checked too.
    { code: t(`const r = api?.fetch();`), filename: FN, errors: [{ messageId: 'unasserted' }] },
    { code: t("const r = sql`select 1`;"), filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Mock exemption does not cover an unrelated aliased import.
    {
      code: `import { thing as v } from 'other-lib';
        it('x', () => { const r = v.run(); });`,
      filename: FN,
      errors: [{ messageId: 'unasserted' }],
    },
    // each forms are analyzed: array table, tagged template, nested modifier.
    {
      code: `it.each([1, 2])('x %i', (n) => { const r = f(n); });`,
      filename: FN,
      errors: [{ messageId: 'unasserted' }],
    },
    {
      code: "test.each`\n a | b\n ${1} | ${2}\n`('x', ({ a }) => { const r = f(a); });",
      filename: FN,
      errors: [{ messageId: 'unasserted' }],
    },
    {
      code: `it.concurrent.each([1])('x', (n) => { const r = f(n); });`,
      filename: FN,
      errors: [{ messageId: 'unasserted' }],
    },
    // `await x;` is NOT a discard, but a bare `x;` and `void x` still are.
    // Member-form test block.
    { code: `test.only('x', () => { const r = f(); });`, filename: FN, errors: [{ messageId: 'unasserted' }] },
    // Stale default marker does not justify under a custom marker.
    {
      code: t(`/* TEST-CORRECTNESS: stale */ const r = f();`),
      filename: FN,
      options: [{ marker: 'NO-ASSERT:' }],
      errors: [{ messageId: 'unasserted' }],
    },
  ],
});
