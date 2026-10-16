/**
 * ESLint rule: no-tautological-assertions
 *
 * Flags an assertion whose outcome is fixed by the test text alone, so it can
 * never depend on the code under test:
 *
 *   - self-comparison      expect(a.b).toBe(a.b)   (no calls / await / new)
 *   - literal vs literal   expect(1).toBe(1), expect([1]).toEqual([1])
 *   - constant subject     expect(true).toBeTruthy(), const x = 5; expect(x).toBe(5)
 *
 * Pure AST + ESLint scope analysis (one `const` hop); no type information.
 * "Pure" values are literals, call-free arrays/objects of pure values, unary
 * operators on pure values, and `const` bindings of those. Anything with a
 * call, `await`, `new`, a binary operator (`0.1 + 0.2`), a parameter, or a
 * `let` is NOT pure, so those assertions are never flagged.
 *
 * Same two auditable escape hatches as the rest of the plugin: an inline
 * `// TEST-CORRECTNESS: <reason>` marker inside the test block, and a per-file
 * `baseline` option.
 */
import {
  DEFAULT_MARKER,
  constInit,
  findVariable,
  getScopeOf,
  isTestBlockCall,
  isTestFile,
  markerRegex,
  parseExpectMatcher,
  relForward,
} from './_shared.mjs';

const EQUALITY_MATCHERS = new Set(['toBe', 'toEqual', 'toStrictEqual', 'toMatchObject']);
const PURE_UNARY = new Set(['-', '+', '!', 'typeof', 'void']);
const PURE_GLOBALS = new Set(['undefined', 'NaN', 'Infinity']);
const MAX_HOPS = 4;
// Node kinds whose presence means the two sides of a comparison can differ.
const IMPURE = new Set([
  'CallExpression',
  'NewExpression',
  'AwaitExpression',
  'YieldExpression',
  'ImportExpression',
  'TaggedTemplateExpression',
  'UpdateExpression',
  'AssignmentExpression',
]);

function isPure(node, scope, hops = 0) {
  if (!node || hops > MAX_HOPS) return false;
  switch (node.type) {
    case 'Literal':
      return true;
    case 'TemplateLiteral':
      return node.expressions.length === 0;
    case 'UnaryExpression':
      return PURE_UNARY.has(node.operator) && isPure(node.argument, scope, hops);
    case 'ArrayExpression':
      return node.elements.every((e) => e && e.type !== 'SpreadElement' && isPure(e, scope, hops));
    case 'ObjectExpression':
      return node.properties.every(
        (p) =>
          p.type === 'Property' &&
          !p.computed &&
          p.kind === 'init' &&
          !p.method &&
          isPure(p.value, scope, hops),
      );
    case 'Identifier': {
      const variable = findVariable(scope, node.name);
      if (!variable || variable.defs.length === 0) return PURE_GLOBALS.has(node.name); // global
      const init = constInit(variable);
      return init ? isPure(init, variable.scope, hops + 1) : false;
    }
    default:
      return false;
  }
}

// True when the subtree contains something that could make two textually
// identical expressions evaluate differently.
function containsImpure(node, visitorKeys) {
  if (!node || typeof node.type !== 'string') return false;
  if (IMPURE.has(node.type)) return true;
  const keys = visitorKeys[node.type] ?? [];
  for (const key of keys) {
    const child = node[key];
    if (Array.isArray(child)) {
      if (child.some((c) => containsImpure(c, visitorKeys))) return true;
    } else if (child && containsImpure(child, visitorKeys)) {
      return true;
    }
  }
  return false;
}

const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow assertions whose outcome is fixed by the test text alone (tautologies).',
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
      selfComparison:
        'This assertion compares `{{text}}` with itself, so its outcome is fixed by the test text ' +
        'and never depends on the code under test. ' +
        'Assert against an independently stated expected value, or justify it with an inline ' +
        '`// {{marker}} <reason>` comment.',
      literalVsLiteral:
        'This assertion compares two constants written in the test, so it cannot depend on the ' +
        'code under test. Assert the result of the code under test, or justify it with an inline ' +
        '`// {{marker}} <reason>` comment.',
      constantSubject:
        'This assertion checks a constant written in the test, so it cannot depend on the code ' +
        'under test. Assert the result of the code under test, or justify it with an inline ' +
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
    const visitorKeys = sourceCode.visitorKeys;
    const stack = [];

    function classify(node, scope) {
      const m = parseExpectMatcher(node);
      if (!m || !m.subject) return null;
      const arg0 = m.args[0];
      const subjectPure = isPure(m.subject, scope);
      const argsPure = m.args.every((a) => isPure(a, scope));

      if (EQUALITY_MATCHERS.has(m.matcher) && arg0) {
        // Pure constants first: the more specific message for `expect(5).toBe(5)`.
        if (subjectPure && isPure(arg0, scope)) return { messageId: 'literalVsLiteral' };
        const subjText = sourceCode.getText(m.subject);
        if (
          subjText === sourceCode.getText(arg0) &&
          !containsImpure(m.subject, visitorKeys) &&
          !containsImpure(arg0, visitorKeys)
        ) {
          return { messageId: 'selfComparison', text: subjText };
        }
      }
      if (subjectPure && argsPure) return { messageId: 'constantSubject' };
      return null;
    }

    return {
      CallExpression(node) {
        if (isTestBlockCall(node)) {
          stack.push({ node, findings: [] });
          return;
        }
        if (stack.length === 0) return;
        const hit = classify(node, getScopeOf(context, node));
        if (hit) stack[stack.length - 1].findings.push({ node, hit });
      },

      'CallExpression:exit'(node) {
        if (stack.length === 0) return;
        const frame = stack[stack.length - 1];
        if (frame.node !== node) return;
        stack.pop();
        if (frame.findings.length === 0) return;

        const comments = sourceCode.getCommentsInside(node);
        if (comments.some((c) => markerRe.test(c.value))) return; // justified

        for (const { node: at, hit } of frame.findings) {
          context.report({
            node: at,
            messageId: hit.messageId,
            data: { text: hit.text ?? '', marker },
          });
        }
      },
    };
  },
};

export default rule;
