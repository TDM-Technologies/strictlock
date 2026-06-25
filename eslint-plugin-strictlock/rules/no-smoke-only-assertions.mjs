/**
 * ESLint rule: no-smoke-only-assertions
 *
 * Flags a test block (`it` / `test`) whose assertions are ALL "smoke-only" —
 * matchers that pass for almost any value and so assert ~nothing
 * (`toBeDefined`, `toBeTruthy`, `toContain`, `length > 0`, bare `expect(x)`, …).
 * Such a test looks protective in the suite but is a false safety net: it turns
 * green whether or not the behavior under test is correct.
 *
 * PREVENTIVE, not remedial. The rule fires on a test block only when *every*
 * assertion in it is smoke-only — a single real assertion redeems the block.
 *
 * Two deliberate escape hatches, both auditable:
 *   1. Inline marker comment. A deliberately-minimal smoke test justified up
 *      front with an inline `// TEST-CORRECTNESS: <reason>` comment inside the
 *      block is allowed. The marker string is configurable (option `marker`).
 *   2. Grandfathering baseline. Adopters can list known-weak test files in the
 *      `baseline` option so `eslint .` stays green at adoption while new files
 *      and new violations still error. The baseline IS your remediation
 *      worklist — drain it, don't grow it.
 *
 * Configured entirely by ESLint rule options (see `meta.schema`); the default
 * is strict / no baseline / no project specifics.
 */

// Matchers that assert ~nothing on their own.
const DEFAULT_SMOKE_MATCHERS = [
  'toBeDefined',
  'toBeUndefined',
  'toBeNull',
  'toBeTruthy',
  'toBeFalsy',
  'toContain',
  'toContainEqual',
  'toBeNaN',
];

// Matchers that are smoke-only ONLY when compared against a trivial floor
// (the `expect(x.length).toBeGreaterThan(0)` idiom).
const TRIVIAL_FLOOR = {
  toBeGreaterThan: [0],
  toBeGreaterThanOrEqual: [0, 1],
};

const TEST_FNS = new Set(['it', 'test']);
const DEFAULT_MARKER = 'TEST-CORRECTNESS:';
const DEFAULT_TEST_FILE_RE = /\.test\.[cm]?[jt]sx?$/;

function isTestFile(filename) {
  return DEFAULT_TEST_FILE_RE.test(filename);
}

// Normalize a path to forward slashes and make it relative to `cwd` so a
// configured baseline entry matches regardless of host OS path separators.
function relForward(cwd, filename) {
  let rel = filename;
  if (cwd && filename.startsWith(cwd)) {
    rel = filename.slice(cwd.length);
    if (rel.startsWith('/') || rel.startsWith('\\')) rel = rel.slice(1);
  }
  return rel.split('\\').join('/');
}

// Is this CallExpression a test-block call (`it(...)`, `test.only(...)`, …)?
function isTestCall(node) {
  const callee = node.callee;
  if (!callee) return false;
  if (callee.type === 'Identifier') return TEST_FNS.has(callee.name);
  if (
    callee.type === 'MemberExpression' &&
    callee.object &&
    callee.object.type === 'Identifier'
  ) {
    return TEST_FNS.has(callee.object.name);
  }
  return false;
}

function hasFnArg(node) {
  return node.arguments.some(
    (a) =>
      a &&
      (a.type === 'ArrowFunctionExpression' || a.type === 'FunctionExpression'),
  );
}

// If `callNode` is `expect(...)[.not]?.<matcher>(...)`, return {matcher, arg0}.
function asExpectMatcher(callNode) {
  const callee = callNode.callee;
  if (!callee || callee.type !== 'MemberExpression' || callee.computed) return null;
  const matcher = callee.property && callee.property.name;
  if (!matcher) return null;
  let node = callee.object;
  while (node) {
    if (node.type === 'CallExpression') {
      if (
        node.callee &&
        node.callee.type === 'Identifier' &&
        node.callee.name === 'expect'
      ) {
        return { matcher, arg0: callNode.arguments[0] };
      }
      node = node.callee;
    } else if (node.type === 'MemberExpression') {
      node = node.object;
    } else {
      break;
    }
  }
  return null;
}

function makeIsSmoke(smokeMatchers) {
  return function isSmoke(matcher, arg0) {
    if (smokeMatchers.has(matcher)) return true;
    if (Object.prototype.hasOwnProperty.call(TRIVIAL_FLOOR, matcher)) {
      if (arg0 && arg0.type === 'Literal' && typeof arg0.value === 'number') {
        return TRIVIAL_FLOOR[matcher].includes(arg0.value);
      }
    }
    return false;
  };
}

// A bare `expect(x)` statement with no matcher chain asserts nothing.
function isBareExpect(node) {
  return (
    node.callee &&
    node.callee.type === 'Identifier' &&
    node.callee.name === 'expect' &&
    node.parent &&
    node.parent.type === 'ExpressionStatement'
  );
}

const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow test blocks whose assertions are all smoke-only (false safety nets).',
      url: 'https://github.com/TDM-Technologies/strictlock/tree/main/eslint-plugin-strictlock',
    },
    schema: [
      {
        type: 'object',
        properties: {
          // Test files (forward-slash, relative to cwd) to grandfather in.
          // Default empty = strict.
          baseline: {
            type: 'array',
            items: { type: 'string' },
            uniqueItems: true,
          },
          // Inline marker that justifies a deliberately-minimal smoke test.
          marker: {
            type: 'string',
            minLength: 1,
          },
          // Extra matcher names to treat as smoke-only, in addition to the
          // built-in set (e.g. project-specific custom matchers).
          extraSmokeMatchers: {
            type: 'array',
            items: { type: 'string' },
            uniqueItems: true,
          },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      smokeOnly:
        'This test asserts nothing meaningful — every assertion is smoke-only ({{matchers}}). ' +
        'Assert the actual behavior/value, or justify a minimal smoke test with an inline ' +
        '`// {{marker}} <reason>` comment.',
    },
  },

  create(context) {
    const filename = context.filename ?? context.getFilename();
    if (!isTestFile(filename)) return {};

    const options = context.options[0] ?? {};
    const baseline = new Set(options.baseline ?? []);
    const marker = options.marker ?? DEFAULT_MARKER;
    const markerRe = new RegExp(
      marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
    );
    const smokeMatchers = new Set([
      ...DEFAULT_SMOKE_MATCHERS,
      ...(options.extraSmokeMatchers ?? []),
    ]);
    const isSmoke = makeIsSmoke(smokeMatchers);

    const cwd = context.cwd ?? process.cwd();
    if (baseline.has(relForward(cwd, filename))) return {}; // grandfathered

    const sourceCode = context.sourceCode ?? context.getSourceCode();
    const stack = [];

    return {
      CallExpression(node) {
        if (isTestCall(node) && hasFnArg(node)) {
          stack.push({ node, strong: 0, smoke: 0, matchers: [] });
          return;
        }
        if (stack.length === 0) return;
        const frame = stack[stack.length - 1];

        const m = asExpectMatcher(node);
        if (m) {
          if (isSmoke(m.matcher, m.arg0)) {
            frame.smoke += 1;
            frame.matchers.push(m.matcher);
          } else {
            frame.strong += 1;
          }
          return;
        }
        if (isBareExpect(node)) {
          frame.smoke += 1;
          frame.matchers.push('expect()');
        }
      },

      'CallExpression:exit'(node) {
        if (stack.length === 0) return;
        const frame = stack[stack.length - 1];
        if (frame.node !== node) return;
        stack.pop();

        const total = frame.smoke + frame.strong;
        if (total === 0 || frame.strong > 0) return; // no assertions, or has a real one

        const comments = sourceCode.getCommentsInside(node);
        if (comments.some((c) => markerRe.test(c.value))) return; // justified

        const report = node.arguments[0] ?? node.callee;
        context.report({
          node: report,
          messageId: 'smokeOnly',
          data: {
            matchers: [...new Set(frame.matchers)].join(', '),
            marker,
          },
        });
      },
    };
  },
};

export default rule;
