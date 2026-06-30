# test-protection-guard — decision-log schema

The guard's output of record is its **decision log**: an append-only JSONL file, one object per
line, written only when a real coupling is found (decision `flagged` or `justified`). Clean
outcomes — no test+source co-commit, additions-only test edits — are **not** logged; they are not
events. The log is what a human reads at close-out, so its shape is stable and self-describing.

## Log record (one JSON object per line)

| Field | Type | Meaning |
|---|---|---|
| `ts` | string (ISO-8601 UTC) | when the decision was made, e.g. `2026-06-26T17:04:22.118402+00:00`. |
| `mode` | string | `pretooluse` or `precommit` — which door produced the record. |
| `root` | string | absolute repo root (`git rev-parse --show-toplevel`). |
| `test_files` | string[] | the staged test files (repo-relative) that triggered the check. |
| `source_files` | string[] | the staged non-test source files staged alongside them. |
| `removed_assertions` | string[] | up to 10 changed/removed test lines that carried an assertion (trimmed). The evidence. |
| `token_present` | boolean | whether a `TEST-CORRECTNESS:` justification was found. |
| `decision` | string | `flagged` (coupling, no token) or `justified` (coupling, token present). |

### Example — flagged

```json
{"ts":"2026-06-26T17:04:22.118402+00:00","mode":"precommit","root":"/repo",
 "test_files":["src/calc.test.ts"],"source_files":["src/calc.ts"],
 "removed_assertions":["- expect(compute()).toBe(5);"],
 "token_present":false,"decision":"flagged"}
```

### Example — justified

```json
{"ts":"2026-06-26T17:05:01.770991+00:00","mode":"pretooluse","root":"/repo",
 "test_files":["src/calc.test.ts"],"source_files":["src/calc.ts"],
 "removed_assertions":["- expect(compute()).toBe(5);"],
 "token_present":true,"decision":"justified"}
```

## Decision states

| Decision | Condition | Logged? | PreToolUse output | pre-commit output |
|---|---|:--:|---|---|
| *(clean)* | gate off, not a `git commit`, no test+source co-commit, or test edits add assertions only | no | `allow` + reason | exit 0, silent |
| `flagged` | existing assertion changed/removed **and** source staged, **no** token | yes | `allow` + reason `…FLAGGED…` + **stderr warning** | exit 0 + **stderr warning** |
| `justified` | same coupling **with** a `TEST-CORRECTNESS:` token | yes | `allow` + reason `…justification…` | exit 0, silent |

In every state the guard **allows** — it has no deny path. The difference between states is what
gets **logged** and whether a warning is printed.

## PreToolUse stdout contract

In PreToolUse mode the guard prints exactly one JSON object on stdout and exits 0:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow",
 "permissionDecisionReason":"test-protection-guard: …"}}
```

`permissionDecision` is always `allow`. The `permissionDecisionReason` carries the outcome
(`…FLAGGED…`, `…justification…`, `…not a \`git commit\`.`, `…inert.`, …). In pre-commit mode the
guard writes **nothing** to stdout — warnings go to stderr, and the exit code is always 0.

## Reviewing the log (the teeth)

```bash
# every flagged coupling that lacked a justification, this sprint:
grep '"decision":"flagged"' "$TEST_PROTECTION_GUARD_LOG_DIR/test-protection-guard.log"
```

Each `flagged` line is a prompt for a human: did this commit fix the code, or move the goalposts?
A `justified` line says someone asserted the test itself was wrong — still worth a glance, but
they left a reason.
