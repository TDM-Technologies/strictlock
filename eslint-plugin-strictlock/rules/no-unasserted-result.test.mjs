/**
 * Tests for the no-unasserted-result rule. Same harness as
 * no-smoke-only-assertions.test.mjs: ESLint's RuleTester bound to node:test.
 */
import test from 'node:test';

import { RuleTester } from 'eslint';

import rule from './no-unasserted-result.mjs';

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
