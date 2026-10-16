# memory-cap

**Structurally cap the length of each entry in an agent's auto-loaded memory index.**

A [StrictLock](../README.md) module. Many agent harnesses auto-load a memory *index* file
into the context of every session. Left unmanaged it grows without bound and becomes the
single largest fixed cost you pay on every turn. The usual fix — "keep index entries to one
line" — is a convention, and conventions quietly rot.

The index grows two ways: longer entries and more entries. `memory-cap` makes the
**per-entry** half of the convention **structural**: it's a `PreToolUse` hook that refuses any
Write/Edit to the index file that would introduce an over-length index entry. Same posture as
the rest of StrictLock — fail closed, deny at the boundary, before the bad write lands.

## What it does

- Watches Write / Edit / NotebookEdit calls targeting the configured **index file**.
- Measures only **index entries** — lines starting with `- ` (dash + space). Headings,
  blockquotes, blank lines, and all other prose are exempt.
- Checks only the index entries a write **introduces or modifies** — the multiset delta
  between the write's result and the file's pre-existing `- ` lines (compared after
  `splitlines()`/`rstrip`, so the comparison is line-ending agnostic across CRLF and LF).
  A pre-existing, untouched over-cap line is not re-flagged by `Write` or `Edit`, so a
  compliant write that merely retains it is allowed — the index does not self-wedge through
  those tools. (`NotebookEdit` has no reliable pre-edit baseline and is checked whole, so a
  `NotebookEdit` that retains an over-cap line can still be denied.) A brand-new or unreadable
  file has an empty baseline, so a fresh over-cap write is still denied.
- The cap is **per entry** (default 200 characters per `- ` line). There is no limit on the
  number of entries or the index's total size yet.
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
