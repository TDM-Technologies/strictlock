/**
 * Tests for the CommonJS entry (index.cjs) and for loading the plugin the way
 * real adopters do: by package name, through the `exports` map.
 *
 * - Shape / parity tests run on every ESLint version.
 * - The legacy `.eslintrc` integration test runs only on ESLint 8 (ESLint 9+
 *   removed eslintrc from the default `ESLint` class).
 * - The flat-config integration test runs on ESLint >= 9.
 *
 * CI installs each ESLint major in turn (see .github/workflows/ci.yml).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

import { ESLint } from 'eslint';

import esmPlugin from '../index.mjs';

const pkgRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const cjsPlugin = require('../index.cjs');
const pkgJson = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf8'));
const eslintMajor = Number(
  JSON.parse(fs.readFileSync(require.resolve('eslint/package.json'), 'utf8')).version.split('.')[0],
);

const SMOKE_ONLY = `it('x', () => { expect(a).toBeDefined(); });\n`;
const REAL = `it('x', () => { expect(a).toBe(1); });\n`;

// A scratch project whose node_modules links to this package, so the plugin is
// resolved by name ("strictlock" -> eslint-plugin-strictlock) like an adopter's.
function makeScratchProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'strictlock-cjs-'));
  fs.mkdirSync(path.join(dir, 'node_modules'));
  fs.symlinkSync(pkgRoot, path.join(dir, 'node_modules', 'eslint-plugin-strictlock'), 'dir');
  return dir;
}

test('package.json exports: import -> index.mjs, require -> index.cjs; main is CJS', () => {
  assert.deepEqual(pkgJson.exports['.'], { import: './index.mjs', require: './index.cjs' });
  assert.equal(pkgJson.main, './index.cjs');
  assert.ok(pkgJson.files.includes('index.cjs'));
  assert.ok(pkgJson.files.includes('rules/no-smoke-only-assertions.cjs'));
});

test('CJS entry exposes rules, flat recommended, and recommended-legacy', () => {
  assert.deepEqual(Object.keys(cjsPlugin.rules), ['no-smoke-only-assertions']);
  assert.equal(typeof cjsPlugin.rules['no-smoke-only-assertions'].create, 'function');
  assert.deepEqual(cjsPlugin.configs['recommended-legacy'], {
    plugins: ['strictlock'],
    rules: { 'strictlock/no-smoke-only-assertions': 'error' },
  });
  const flat = cjsPlugin.configs.recommended;
  assert.ok(Array.isArray(flat));
  assert.equal(flat[0].plugins.strictlock, cjsPlugin, 'flat config self-references its own plugin');
  assert.deepEqual(flat[0].rules, { 'strictlock/no-smoke-only-assertions': 'error' });
});

test('CJS and ESM entries agree: meta, version, rule, configs', () => {
  assert.deepEqual(cjsPlugin.meta, esmPlugin.meta);
  assert.equal(cjsPlugin.meta.version, pkgJson.version);
  // Same rule implementation (single CommonJS source), so identical objects.
  assert.equal(cjsPlugin.rules['no-smoke-only-assertions'], esmPlugin.rules['no-smoke-only-assertions']);
  assert.deepEqual(cjsPlugin.configs['recommended-legacy'], esmPlugin.configs['recommended-legacy']);
  const [c] = cjsPlugin.configs.recommended;
  const [e] = esmPlugin.configs.recommended;
  assert.equal(c.name, e.name);
  assert.deepEqual(c.rules, e.rules);
  assert.equal(e.plugins.strictlock, esmPlugin, 'ESM flat config self-references the ESM plugin');
});

test('require() by package name resolves through exports to index.cjs', async () => {
  const dir = makeScratchProject();
  try {
    const scratchRequire = createRequire(path.join(dir, 'noop.js'));
    const resolvedCjs = fs.realpathSync(scratchRequire.resolve('eslint-plugin-strictlock'));
    assert.equal(resolvedCjs, fs.realpathSync(path.join(pkgRoot, 'index.cjs')));
    assert.equal(scratchRequire('eslint-plugin-strictlock').meta.version, pkgJson.version);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test(
  'legacy .eslintrc: plugins:["strictlock"] + extends recommended-legacy reports (ESLint 8)',
  { skip: eslintMajor !== 8 && `ESLint ${eslintMajor}: eslintrc path is ESLint 8 only` },
  async () => {
    const dir = makeScratchProject();
    try {
      const eslint = new ESLint({
        cwd: dir,
        useEslintrc: false,
        resolvePluginsRelativeTo: dir,
        overrideConfig: {
          root: true,
          parserOptions: { ecmaVersion: 2022, sourceType: 'module' },
          plugins: ['strictlock'],
          extends: ['plugin:strictlock/recommended-legacy'],
        },
      });
      const [bad] = await eslint.lintText(SMOKE_ONLY, { filePath: path.join(dir, 'a.test.js') });
      assert.equal(bad.messages.length, 1);
      assert.equal(bad.messages[0].ruleId, 'strictlock/no-smoke-only-assertions');
      assert.equal(bad.messages[0].messageId, 'smokeOnly');
      const [good] = await eslint.lintText(REAL, { filePath: path.join(dir, 'b.test.js') });
      assert.equal(good.messages.length, 0);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  },
);

for (const [label, plugin] of [
  ['CJS entry', cjsPlugin],
  ['ESM entry', esmPlugin],
]) {
  test(
    `flat config (${label}): configs.recommended reports (ESLint >= 9)`,
    { skip: eslintMajor < 9 && `ESLint ${eslintMajor}: flat ESLint class is ESLint 9+` },
    async () => {
      const dir = makeScratchProject();
      try {
        const eslint = new ESLint({
          cwd: dir,
          overrideConfigFile: true,
          overrideConfig: [
            { languageOptions: { ecmaVersion: 2022, sourceType: 'module' } },
            ...plugin.configs.recommended,
          ],
        });
        const [bad] = await eslint.lintText(SMOKE_ONLY, { filePath: path.join(dir, 'a.test.js') });
        assert.equal(bad.messages.length, 1);
        assert.equal(bad.messages[0].ruleId, 'strictlock/no-smoke-only-assertions');
        const [good] = await eslint.lintText(REAL, { filePath: path.join(dir, 'b.test.js') });
        assert.equal(good.messages.length, 0);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    },
  );
}
