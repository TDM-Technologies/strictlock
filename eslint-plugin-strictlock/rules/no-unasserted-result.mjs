// Thin ESM re-export. The implementation lives in the CommonJS file so both the
// ESM entry (index.mjs) and the CJS entry (index.cjs) share one source of truth.
import rule from './no-unasserted-result.cjs';

export default rule;
