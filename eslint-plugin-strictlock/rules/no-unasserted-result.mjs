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
import {
  DEFAULT_MARKER,
  getDeclaredVariables,
  isTestBlockCall,
  isTestFile,
  markerRegex,
  relForward,
} from './_shared.mjs';

const MOCK_ROOTS = new Set(['jest', 'vi', 'sinon']);

function unwrapAwait(node) {
  let n = node;
  while (n && n.type === 'AwaitExpression') n = n.argument;
  return n;
}

// Leftmost identifier of a callee: `a.b.c(...)` -> `a`.
function calleeRoot(callNode) {
  let n = callNode.callee;
  while (n && (n.type === 'MemberExpression' || n.type === 'CallExpression')) {
    n = n.type === 'MemberExpression' ? n.object : n.callee;
  }
  return n && n.type === 'Identifier' ? n.name : null;
}

function isCallInit(init) {
  const n = unwrapAwait(init);
  if (!n) return false;
  if (n.type === 'NewExpression') return true;
  if (n.type !== 'CallExpression') return false;
  return !MOCK_ROOTS.has(calleeRoot(n));
}

// Is this read of the variable one that cannot constitute an assertion?
function isNoopRead(identifier) {
  let node = identifier;
  while (node.parent && node.parent.type === 'AwaitExpression') node = node.parent;
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

    const cwd = context.cwd ?? process.cwd();
    if (baseline.has(relForward(cwd, filename))) return {};

    const sourceCode = context.sourceCode ?? context.getSourceCode();
    const stack = [];

    return {
      CallExpression(node) {
        if (isTestBlockCall(node)) stack.push({ node, findings: [] });
      },

      VariableDeclarator(node) {
        if (stack.length === 0 || !node.init || !isCallInit(node.init)) return;
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

export default rule;
