/**
 * Helpers shared by the wave-2 rules (no-tautological-assertions,
 * no-unasserted-result). Not a rule itself — underscore-prefixed and not
 * registered in index.mjs.
 *
 * no-smoke-only-assertions keeps its own private copies of the equivalent
 * helpers; wave 2 deliberately leaves that rule's file untouched.
 */

export const TEST_FNS = new Set(['it', 'test']);
export const DEFAULT_MARKER = 'TEST-CORRECTNESS:';
const TEST_FILE_RE = /\.test\.[cm]?[jt]sx?$/;

export function isTestFile(filename) {
  return TEST_FILE_RE.test(filename);
}

// Normalize to a forward-slash path relative to `cwd` so a configured baseline
// entry matches regardless of host OS separators.
export function relForward(cwd, filename) {
  let rel = filename;
  if (cwd && filename.startsWith(cwd)) {
    rel = filename.slice(cwd.length);
    if (rel.startsWith('/') || rel.startsWith('\\')) rel = rel.slice(1);
  }
  return rel.split('\\').join('/');
}

// Modifier members that keep a call a test block: `test.only`, `it.concurrent.each`, …
const TEST_MODIFIERS = new Set([
  'only', 'skip', 'todo', 'fixme', 'fails', 'failing', 'concurrent', 'sequential',
  'each', 'for', 'runIf', 'skipIf',
]);

// `it` / `test` / `it.only` / `test.concurrent.skip` — a bare name or a chain of
// known modifiers. Anything else (`test.describe`, `test.step`) is not a test block.
function isTestName(node) {
  if (node.type === 'Identifier') return TEST_FNS.has(node.name);
  if (node.type === 'MemberExpression' && !node.computed && node.property?.name) {
    return TEST_MODIFIERS.has(node.property.name) && isTestName(node.object);
  }
  return false;
}

// The `.each` factory forms: `it.each([...])` and tagged-template `it.each\`...\``.
function isEachFactory(node) {
  if (node.type === 'CallExpression') return isTestName(node.callee);
  if (node.type === 'TaggedTemplateExpression') return isTestName(node.tag);
  return false;
}

// `it(...)`, `test.only(...)`, `it.concurrent(...)`, and the curried
// `it.each(table)(name, fn)` / `it.each\`...\`(name, fn)` — only with a function arg.
export function isTestBlockCall(node) {
  const callee = node.callee;
  if (!callee) return false;
  const named = isTestName(callee) || isEachFactory(callee);
  return (
    named &&
    node.arguments.some(
      (a) => a && (a.type === 'ArrowFunctionExpression' || a.type === 'FunctionExpression'),
    )
  );
}

/**
 * If `callNode` is `expect(subject)[.not|.resolves|.rejects]*.matcher(...args)`
 * return `{ matcher, subject, args }`, else null.
 */
export function parseExpectMatcher(callNode) {
  const callee = callNode.callee;
  if (!callee || callee.type !== 'MemberExpression' || callee.computed) return null;
  const matcher = callee.property && callee.property.name;
  if (!matcher) return null;
  let node = callee.object;
  while (node) {
    if (node.type === 'CallExpression') {
      if (node.callee?.type === 'Identifier' && node.callee.name === 'expect') {
        return { matcher, subject: node.arguments[0], args: callNode.arguments };
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

export function markerRegex(marker) {
  return new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
}

export function getScopeOf(context, node) {
  const sc = context.sourceCode ?? context.getSourceCode();
  return sc.getScope ? sc.getScope(node) : context.getScope();
}

export function getDeclaredVariables(context, node) {
  const sc = context.sourceCode ?? context.getSourceCode();
  return sc.getDeclaredVariables
    ? sc.getDeclaredVariables(node)
    : context.getDeclaredVariables(node);
}

// Find a Variable by name, walking outward from `scope`.
export function findVariable(scope, name) {
  for (let s = scope; s; s = s.upper) {
    const v = s.set.get(name);
    if (v) return v;
  }
  return null;
}

// The initializer of a single-definition `const x = <init>` variable, else null.
export function constInit(variable) {
  if (!variable || variable.defs.length !== 1) return null;
  const def = variable.defs[0];
  if (def.type !== 'Variable') return null;
  if (def.parent?.kind !== 'const') return null;
  if (def.node.id.type !== 'Identifier') return null;
  return def.node.init ?? null;
}
