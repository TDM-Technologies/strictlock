// Thin ESM re-export of the CommonJS helpers (single source of truth).
import shared from './_shared.cjs';

export const {
  TEST_FNS,
  DEFAULT_MARKER,
  isTestFile,
  relForward,
  isTestBlockCall,
  parseExpectMatcher,
  markerRegex,
  getScopeOf,
  getDeclaredVariables,
  findVariable,
  constInit,
} = shared;
