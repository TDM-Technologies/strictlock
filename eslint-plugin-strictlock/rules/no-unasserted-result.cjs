/**
 * ESLint rule: no-unasserted-result
 *
 * Flags a variable bound from a call (or `await call()` / `new X()`) inside a
 * test block whose value is then dropped: either never read at all, or read only
 * by something that cannot assert (`console.*(x)`, `void x`, a bare `x;`
 * statement). The test computed a result and then checked nothing about it.
 *
 * Conservative by design (default mode): any other read counts as "consumed" —
 * passing the value to a call, using it in an `expect(...)`, returning it,
 * calling it (cleanup handles), deriving another value from it. A destructuring
 * binding is flagged only when NONE of its names are used. Calls rooted at a
 * mocking library (`jest`, `vi`, `sinon`) are skipped: `const spy = vi.spyOn(...)`
 * is a side-effecting setup call even when `spy` is never read.
 *
 * Pure AST + ESLint scope analysis; no type information. Same escape hatches as
 * the rest of the plugin: an inline `// TEST-CORRECTNESS: <reason>` marker inside
 * the test block, and a per-file `baseline` option.
 */
const {
  DEFAULT_MARKER,
  findVariable,
  getDeclaredVariables,
  getScopeOf,
  isTestBlockCall,
  isTestFile,
  markerRegex,
  relForward,
} = require('./_shared.cjs');

// Global names of mocking libraries. Imports of these (including aliased ones) are
// recognised separately; `extraMockRoots` adds project-specific roots.
const MOCK_ROOTS = ['jest', 'vi', 'sinon'];
// Modules whose `vi` / `jest` / `sinon` / `mock` exports are mocking entry points.
const MOCK_MODULES = new Set(['vitest', '@jest/globals', 'sinon', 'node:test', 'test']);
const MOCK_EXPORTS = new Set(['vi', 'jest', 'sinon', 'mock']);

function unwrapInit(node) {
  let n = node;
  while (n && (n.type === 'AwaitExpression' || n.type === 'ChainExpression')) {
    n = n.type === 'AwaitExpression' ? n.argument : n.expression;
  }
  return n;
}

// Leftmost identifier of a callee, plus whether the path passes through `.mock`
// (node:test's `t.mock.fn()` / `t.mock.method()`).
function calleeRoot(callee) {
  let n = callee;
  let viaMock = false;
  while (n) {
    if (n.type === 'MemberExpression') {
      if (!n.computed && n.property?.name === 'mock') viaMock = true;
      n = n.object;
    } else if (n.type === 'CallExpression') n = n.callee;
    else if (n.type === 'ChainExpression') n = n.expression;
    else break;
  }
  return { name: n && n.type === 'Identifier' ? n.name : null, node: n, viaMock };
}

function isMockRoot(root, scope, extraRoots) {
  if (root.viaMock || !root.name) return root.viaMock;
  if (MOCK_ROOTS.includes(root.name) || extraRoots.has(root.name)) return true;
  // Aliased import: `import { vi as v } from 'vitest'`, `import s from 'sinon'`.
  const variable = findVariable(scope, root.name);
  const def = variable && variable.defs[0];
  if (!def || def.type !== 'ImportBinding') return false;
  const source = def.parent?.source?.value;
  if (!MOCK_MODULES.has(source)) return false;
  if (def.node.type === 'ImportSpecifier') return MOCK_EXPORTS.has(def.node.imported.name);
  return source === 'sinon'; // default / namespace import of sinon
}

function isCallInit(init, scope, extraRoots) {
  const n = unwrapInit(init);
  if (!n) return false;
  if (n.type === 'NewExpression') return true;
  if (n.type === 'TaggedTemplateExpression') return true;
  if (n.type !== 'CallExpression') return false;
  return !isMockRoot(calleeRoot(n.callee), scope, extraRoots);
}

// Is this read of the variable one that cannot constitute an assertion?
function isNoopRead(identifier) {
  // `await x` consumes the value (it settles/throws), so it is not a discard.
  const node = identifier;
  const parent = node.parent;
  if (!parent) return false;
  if (parent.type === 'ExpressionStatement') return true; // bare `x;`
  if (parent.type === 'UnaryExpression' && parent.operator === 'void') return true;
  if (
    parent.type === 'CallExpression' &&
    parent.arguments.includes(node) &&
    parent.callee.type === 'MemberExpression' &&
    parent.callee.object.type === 'Identifier' &&
    parent.callee.object.name === 'console'
  ) {
    return true;
  }
  return false;
}

const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow test blocks that bind the result of a call and never use it for an assertion.',
      url: 'https://github.com/TDM-Technologies/strictlock/tree/main/eslint-plugin-strictlock',
    },
    schema: [
      {
        type: 'object',
        properties: {
          baseline: { type: 'array', items: { type: 'string' }, uniqueItems: true },
          marker: { type: 'string', minLength: 1 },
          // Extra callee roots to treat as mocking setup calls (bindings may go unread).
          extraMockRoots: { type: 'array', items: { type: 'string' }, uniqueItems: true },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      unasserted:
        '`{{name}}` holds the result of a call but is never asserted on ({{why}}). ' +
        'Assert something about it, drop the binding, or justify it with an inline ' +
        '`// {{marker}} <reason>` comment.',
    },
  },

  create(context) {
    const filename = context.filename ?? context.getFilename();
    if (!isTestFile(filename)) return {};

    const options = context.options[0] ?? {};
    const baseline = new Set(options.baseline ?? []);
    const marker = options.marker ?? DEFAULT_MARKER;
    const markerRe = markerRegex(marker);
    const extraRoots = new Set(options.extraMockRoots ?? []);

    const cwd = context.cwd ?? process.cwd();
    if (baseline.has(relForward(cwd, filename))) return {};

    const sourceCode = context.sourceCode ?? context.getSourceCode();
    const stack = [];

    return {
      CallExpression(node) {
        if (isTestBlockCall(node)) stack.push({ node, findings: [] });
      },

      VariableDeclarator(node) {
        if (stack.length === 0 || !node.init) return;
        // `using` / `await using` bindings are disposed, which is their use.
        const kind = node.parent?.kind;
        if (kind === 'using' || kind === 'await using') return;
        if (!isCallInit(node.init, getScopeOf(context, node), extraRoots)) return;
        const variables = getDeclaredVariables(context, node);
        if (variables.length === 0) return;

        let anyConsumed = false;
        let anyNoop = false;
        for (const v of variables) {
          for (const ref of v.references) {
            if (!ref.isRead()) continue;
            if (isNoopRead(ref.identifier)) anyNoop = true;
            else anyConsumed = true;
          }
        }
        if (anyConsumed) return;

        stack[stack.length - 1].findings.push({
          node,
          name: variables.map((v) => v.name).join(', '),
          why: anyNoop ? 'only logged or discarded' : 'never read',
        });
      },

      'CallExpression:exit'(node) {
        if (stack.length === 0) return;
        const frame = stack[stack.length - 1];
        if (frame.node !== node) return;
        stack.pop();
        if (frame.findings.length === 0) return;

        const comments = sourceCode.getCommentsInside(node);
        if (comments.some((c) => markerRe.test(c.value))) return; // justified

        for (const f of frame.findings) {
          context.report({
            node: f.node,
            messageId: 'unasserted',
            data: { name: f.name, why: f.why, marker },
          });
        }
      },
    };
  },
};

module.exports = rule;
