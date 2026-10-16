/**
 * eslint-plugin-strictlock — CommonJS entry.
 *
 * Same plugin as index.mjs (`rules`, `configs.recommended` flat,
 * `configs['recommended-legacy']`), for consumers that load plugins with
 * require(): ESLint 8 with a legacy `.eslintrc`, or a CJS `eslint.config.cjs`.
 *
 * The CJS and ESM entries build separate plugin objects (dual-package hazard);
 * each entry's flat `recommended` config references its own plugin object.
 */
'use strict';

const noSmokeOnlyAssertions = require('./rules/no-smoke-only-assertions.cjs');

const meta = {
  name: 'eslint-plugin-strictlock',
  version: '0.1.2',
};

const rules = {
  'no-smoke-only-assertions': noSmokeOnlyAssertions,
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
    },
  },
];

// Legacy eslintrc shim (.eslintrc.*) for adopters not yet on flat config.
plugin.configs['recommended-legacy'] = {
  plugins: ['strictlock'],
  rules: {
    'strictlock/no-smoke-only-assertions': 'error',
  },
};

module.exports = plugin;
module.exports.meta = meta;
module.exports.rules = rules;
