/**
 * meta.version must track package.json in both entry points, so it cannot
 * drift from the published version.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

import esmPlugin, { meta as esmMeta } from '../index.mjs';

const pkgRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const cjsPlugin = require('../index.cjs');
const pkgJson = JSON.parse(fs.readFileSync(path.join(pkgRoot, 'package.json'), 'utf8'));

test('CJS meta.version equals package.json version', () => {
  assert.equal(cjsPlugin.meta.version, pkgJson.version);
});

test('ESM meta.version equals package.json version', () => {
  assert.equal(esmPlugin.meta.version, pkgJson.version);
  assert.equal(esmMeta.version, pkgJson.version);
});
