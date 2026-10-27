/**
 * eslint-plugin-strictlock — CommonJS entry.
 *
 * Same plugin as index.mjs (`rules`, `configs.recommended` flat,
 * `configs['recommended-legacy']`), for consumers that load plugins with
 * require(): ESLint 8 with a legacy `.eslintrc`, or a CJS `eslint.config.cjs`.
 *
 * This is the single place rules and configs are registered: index.mjs
 * re-exports this object, so `import` and `require` get the same plugin.
 */
'use strict';

const noSmokeOnlyAssertions = require('./rules/no-smoke-only-assertions.cjs');
const noTautologicalAssertions = require('./rules/no-tautological-assertions.cjs');
const noUnassertedResult = require('./rules/no-unasserted-result.cjs');

// Read from package.json so the reported version cannot drift from the
// published one. index.mjs re-exports this object, so ESM gets it too.
const { version } = require('./package.json');

const meta = {
  name: 'eslint-plugin-strictlock',
  version,
};

const rules = {
  'no-smoke-only-assertions': noSmokeOnlyAssertions,
  'no-tautological-assertions': noTautologicalAssertions,
  'no-unasserted-result': noUnassertedResult,
};

const plugin = {
  meta,
  rules,
  configs: {},
};

// Flat config (eslint.config.js). Spread into your config array.
plugin.configs.recommended = [
  {
    name: 'strictlock/recommended',
    plugins: { strictlock: plugin },
    rules: {
      'strictlock/no-smoke-only-assertions': 'error',
      'strictlock/no-tautological-assertions': 'warn',
      'strictlock/no-unasserted-result': 'warn',
    },
  },
];

// Legacy eslintrc shim (.eslintrc.*) for adopters not yet on flat config.
plugin.configs['recommended-legacy'] = {
  plugins: ['strictlock'],
  rules: {
    'strictlock/no-smoke-only-assertions': 'error',
    'strictlock/no-tautological-assertions': 'warn',
    'strictlock/no-unasserted-result': 'warn',
  },
};

module.exports = plugin;
module.exports.meta = meta;
module.exports.rules = rules;
