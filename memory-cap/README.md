# memory-cap

**Structurally cap the size of an agent's auto-loaded memory index.**

A [StrictLock](../README.md) module. Many agent harnesses auto-load a memory *index* file
into the context of every session. Left unmanaged it grows without bound and becomes the
single largest fixed cost you pay on every turn. The usual fix — "keep index entries to one
line" — is a convention, and conventions quietly rot.

The index grows two ways: longer entries and more entries. `memory-cap` makes the convention
**structural**: it's a `PreToolUse` hook that refuses any Write/Edit to the index file that
would introduce an over-length index entry, and (opt-in) any write that grows the index past a
total entry-count or total-characters cap. Same posture as the rest of StrictLock — fail
closed, deny at the boundary, before the bad write lands.

## What it does

- Watches Write / Edit / NotebookEdit calls targeting the configured **index file**.
- Measures only **index entries** — lines starting with `- ` (dash + space). Headings,
  blockquotes, blank lines, and all other prose are exempt.
- Checks only the index entries a write **introduces or modifies** — the multiset delta
  between the write's result and the file's pre-existing `- ` lines (compared after
  `splitlines()`/`rstrip`, so the comparison is line-ending agnostic across CRLF and LF).
  A pre-existing, untouched over-cap line is never re-flagged — by `Write`, `Edit` or
  `NotebookEdit` (all three baseline against the file's on-disk content) — so a compliant
  write that merely retains it is allowed and the index cannot self-wedge. A brand-new or
  unreadable file has an empty baseline, so a fresh over-cap write is still denied.
- The **per-entry** cap is on by default (200 characters per `- ` line).
- Two **opt-in total caps** (`MEMORY_CAP_MAX_ENTRIES`, `MEMORY_CAP_TOTAL_CHARS`) bound the
  index as a whole, counting only `- ` entries (total chars = sum of each entry's length
  excluding trailing whitespace). They use the same changed-region logic: a write is refused
  only if the result is over the cap **and** larger than the pre-existing file on that
  measure. An index already over a cap is never wedged — edits that keep it level or shrink
  it pass; edits that grow it are refused. Total caps are not evaluated for `NotebookEdit`
  (it carries one cell's source, not the whole file).
- If a write introduces or modifies an index entry that exceeds the cap (default **200**
  characters, whole line excluding trailing whitespace), the write is **denied** with a
  message naming the offending lines.
- Every other file — including topic files in the same directory — is untouched. Those are
  the intended place to offload detail.

## Install

Wire `memory-cap.py` as a `PreToolUse` hook in your agent harness (same mechanism as
[`plan-gate`](../plan-gate/) — see its `examples/settings.json` for the Claude Code shape),
then turn it on:

```bash
export MEMORY_CAP=on
```

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `MEMORY_CAP` | `on` enables the hook. Anything else makes it inert (allow-all). |
| `MEMORY_CAP_CHARS` | Integer cap on a `- ` line (whole line, excluding trailing whitespace). Default `200`. |
| `MEMORY_CAP_PATH_RE` | Regex (matched case-insensitively against the forward-slash form of the target path) identifying the index file to cap. Default matches a Claude Code per-project memory index: `/.claude/projects/<project>/memory/memory.md`. An invalid regex falls back to the default; a valid-but-wrong pattern still matches the wrong file, so check yours. |
| `MEMORY_CAP_BYPASS` | `1` bypasses the cap. Single-use by convention: set inline per command; every use is logged (on stderr), so a lingering bypass is visible in the trail. A distinct bypass per gate is deliberate. |
| `MEMORY_CAP_MAX_ENTRIES` | Opt-in. Positive integer cap on the number of `- ` entries. Unset / non-numeric / `<= 0` = off. |
| `MEMORY_CAP_TOTAL_CHARS` | Opt-in. Positive integer cap on the summed length of all `- ` entries. Unset / non-numeric / `<= 0` = off. |
| `MEMORY_CAP_DECISION_LOG` | Opt-in. `1` records every decision on the capped index (allow, deny, bypass) to a hash-chained decision log. Needs `MEMORY_CAP_LOG_DIR`. |
| `MEMORY_CAP_LOG_DIR` | Directory holding `memory-cap-decisions.log`. Logging is on only when this is set **and** `MEMORY_CAP_DECISION_LOG=1`. |

## Decision log (opt-in)

With `MEMORY_CAP_DECISION_LOG=1` and `MEMORY_CAP_LOG_DIR` set, each decision on the capped
index file is appended as one JSON line to `memory-cap-decisions.log`, using the same row shape
and hash chain as [`plan-gate`](../plan-gate/)'s decision log (copied, so the module stays
standalone): `ts`, `decision` (`allow`/`deny`), `surface`, `tool`, `target`, `class`
(`within_cap`, `over_cap`, `total_cap`, `edit_deferred`, `empty_payload`, `parse_error`,
`crash`, `emergency_bypass`), `reason`, `cwd`, plus `prev` (previous row's SHA-256) and `h`
(this row's). Bypass uses are logged too. Rows carry paths, sizes and reasons only — never
file contents. Logging is best-effort and **never** changes a decision; concurrent writers
share the log safely (advisory lock: `flock` on POSIX, `msvcrt` on Windows). Not logged:
the gate being off, and calls that are not Write/Edit/NotebookEdit or do not target the index.

```bash
python3 memory-cap.py verify-log [path]   # exit 0 = chain intact, 2 = damage/tampering
```

The chain makes edits and deletions within the log detectable; it is tamper-evident, not
tamper-proof (no secret key or external anchor — anchoring the latest `h` off-box is a
deployment practice).

### Example

```bash
# Cap index entries at 160 chars, targeting a custom index file.
export MEMORY_CAP=on
export MEMORY_CAP_CHARS=160
export MEMORY_CAP_PATH_RE='/notes/INDEX\.md$'
```

## Why a separate bypass per gate?

If every fail-closed gate honored one shared bypass variable, disabling one would silently
disable them all. `memory-cap` has its own `MEMORY_CAP_BYPASS` so a deliberate, narrow escape
from this cap never weakens `plan-gate` or `commit-msg-gate`.

## Requirements

Python 3.8+ (standard library only).

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
