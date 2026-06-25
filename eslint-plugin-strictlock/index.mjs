/**
 * eslint-plugin-strictlock — the StrictLock suite's JS / Gates-family sibling.
 *
 * A flat-config-first ESLint plugin. Its rules are structural gates against
 * false-safety-net tests: a test that turns green whether or not the code is
 * correct is governance theater, and the suite's job is to catch that at the
 * tool boundary rather than trust a convention nobody enforces.
 *
 * Exports the flat-config plugin shape: { meta, rules, configs }. The
 * `recommended` config is published in flat-config form (an array of config
 * objects), so adopters can spread it straight into `eslint.config.js`.
 */
import noSmokeOnlyAssertions from './rules/no-smoke-only-assertions.mjs';

const meta = {
  name: 'eslint-plugin-strictlock',
  version: '0.1.0',
};

const rules = {
  'no-smoke-only-assertions': noSmokeOnlyAssertions,
};

// Defined first as a bare object so the recommended config can reference the
// same instance via `plugin` below (flat-config plugins are self-referential).
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

export default plugin;
export { meta, rules };
