/**
 * eslint-plugin-strictlock — ES module entry.
 *
 * A thin re-export of index.cjs, so `import` and `require` consumers get the
 * SAME plugin object (ESLint 9 refuses to register two different plugin
 * objects under one name, which a mixed import+require setup would trigger).
 *
 * Rules and configs are registered in index.cjs, the single place to edit.
 * Rule implementations must be CommonJS (rules/*.cjs) so Node 18, which cannot
 * require() an ES module, can load them.
 */
import plugin from './index.cjs';

export default plugin;
export const { meta, rules } = plugin;
