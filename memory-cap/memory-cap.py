#!/usr/bin/env python3
"""memory-cap: a fail-closed PreToolUse hook that caps an agent's memory index size.

WHY THIS EXISTS
  Many agent harnesses auto-load a memory "index" file into the context of every
  session. Left unmanaged that file grows without bound, and it is frequently the
  single largest fixed context cost paid on every turn. The usual remedy is a
  written convention ("keep index entries to one line; move detail to topic
  files") — but conventions are advisory and quietly rot. This hook makes the
  convention STRUCTURAL: any Write/Edit to the index file whose new content
  contains an index entry (a `- ` line) longer than a configurable cap is REFUSED
  at the tool boundary, before it lands.

  Part of StrictLock (https://github.com/TDM-Technologies/strictlock). Standalone —
  no other module required.

WHAT COUNTS
  Only `- ` (dash + space) lines — the index-entry convention — are measured.
  Heading lines, blockquote callouts, blank lines, and any other prose pass
  unconditionally. Only the configured index file is checked; every other file
  (including topic files in the same directory) is exempt — those are the
  intended offload destination.

FAIL-CLOSED
  - Master switch MEMORY_CAP must be "on" (case-insensitive) or the hook is inert
    (allow-all).
  - A uniquely-named bypass MEMORY_CAP_BYPASS=1 skips the cap for a single tool
    call (logged on stderr). A distinct bypass per gate is deliberate: bypassing
    one fail-closed gate must never silently disable another.
  - On a stdin parse error or an unhandled crash the hook DENIES (fail-closed).

CONFIGURATION (all via environment)
  MEMORY_CAP            "on" to enable; anything else makes the hook inert.
  MEMORY_CAP_BYPASS     "1" to bypass for a single tool call (logged on use).
  MEMORY_CAP_CHARS      integer cap on a `- ` line, whole-line excluding trailing
                        whitespace (what the model actually loads). Default 200.
  MEMORY_CAP_PATH_RE    regex identifying the index file to cap, matched
                        case-insensitively against the forward-slash form of the
                        target path. Default matches a Claude Code per-project
                        memory index:
                            /.claude/projects/<project>/memory/memory.md
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

# Master gate + own uniquely-named bypass.
MASTER_GATE_ENV = "MEMORY_CAP"
OWN_BYPASS_ENV = "MEMORY_CAP_BYPASS"


def _cap_chars() -> int:
    """Configurable cap (whole line, excluding trailing whitespace). Default 200."""
    raw = os.environ.get("MEMORY_CAP_CHARS", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 200


CAP_CHARS = _cap_chars()

# Predicate matching the index file to cap. Configurable; the default targets a
# Claude Code per-project memory index. Falls back to the default on a bad regex
# so a misconfiguration can never silently disable the cap.
_DEFAULT_PATH_RE = r"/\.claude/projects/[^/]+/memory/memory\.md$"
try:
    MEMORY_MD_PATH_RE = re.compile(
        os.environ.get("MEMORY_CAP_PATH_RE", "").strip() or _DEFAULT_PATH_RE,
        re.IGNORECASE,
    )
except re.error:
    MEMORY_MD_PATH_RE = re.compile(_DEFAULT_PATH_RE, re.IGNORECASE)

# Only `- ` (dash + space) index entries are measured.
INDEX_LINE_PREFIX = "- "


def _emit(decision: str, reason: str) -> None:
    """Emit the PreToolUse hook decision JSON and exit 0."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow(reason: str) -> None:
    _emit("allow", reason)


def deny(reason: str) -> None:
    """Fail-closed dominant — every blocking path routes here."""
    _emit("deny", reason)


def is_index_target(p: str | None) -> bool:
    """Match the configured index path (Windows-separator tolerant, case-folded)."""
    if not p:
        return False
    return MEMORY_MD_PATH_RE.search(p.replace("\\", "/")) is not None


def find_violating_lines(content: str) -> list:
    """Return [(line_no_1based, line_stripped, char_count)] for over-cap `- ` lines.

    Lines starting with `- ` (dash + space) are the index-entry convention.
    Everything else (headings, blockquotes, blank lines, frontmatter) is exempt.
    The character count is the whole line including the `- ` prefix, excluding
    trailing whitespace.
    """
    violations = []
    for i, line in enumerate(content.splitlines(), start=1):
        if not line.startswith(INDEX_LINE_PREFIX):
            continue
        stripped = line.rstrip()
        n = len(stripped)
        if n > CAP_CHARS:
            violations.append((i, stripped, n))
    return violations


def reconstruct_edit_content(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> str:
    """Apply Edit-tool semantics to the existing file content.

    Returns the new content string, or None if the file cannot be read.
    replace_all=True swaps every occurrence; False swaps only the first.
    """
    try:
        existing = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if replace_all:
        return existing.replace(old_string, new_string)
    return existing.replace(old_string, new_string, 1)


def main() -> None:
    # Master gate — inert unless explicitly turned on.
    if os.environ.get(MASTER_GATE_ENV, "").strip().lower() != "on":
        allow(f"memory-cap: {MASTER_GATE_ENV} != 'on' — gate disabled.")

    # Uniquely-named single-tool-call bypass; logged on use.
    if os.environ.get(OWN_BYPASS_ENV, "").strip() == "1":
        print(
            f"memory-cap: {OWN_BYPASS_ENV}=1 — cap DELIBERATELY bypassed for this "
            f"tool call (logged on use).",
            file=sys.stderr,
        )
        allow(f"memory-cap: {OWN_BYPASS_ENV}=1 — explicit single-call bypass (logged).")

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        deny(f"memory-cap: could not parse stdin JSON ({e}) — fail-closed.")
        return

    tool_name = payload.get("tool_name")
    if tool_name not in ("Write", "Edit", "NotebookEdit"):
        allow(f"memory-cap: not a Write/Edit/NotebookEdit call (tool_name={tool_name!r}).")

    tool_input = payload.get("tool_input") or {}

    # Extract the target path per-tool.
    if tool_name in ("Write", "Edit"):
        target_path = tool_input.get("file_path")
    else:  # NotebookEdit
        target_path = tool_input.get("notebook_path")

    if not is_index_target(target_path):
        allow(f"memory-cap: target path {target_path!r} is not the capped index file.")

    # Reconstruct the intended new content per-tool.
    if tool_name == "Write":
        new_content = tool_input.get("content") or ""
    elif tool_name == "Edit":
        old_s = tool_input.get("old_string")
        new_s = tool_input.get("new_string")
        replace_all = bool(tool_input.get("replace_all", False))
        if old_s is None or new_s is None:
            allow("memory-cap: Edit payload missing old_string/new_string — letting Edit validate.")
        new_content = reconstruct_edit_content(target_path, old_s, new_s, replace_all)
        if new_content is None:
            allow(f"memory-cap: could not read {target_path!r} for Edit reconstruction — letting Edit handle.")
    else:
        # NotebookEdit on a .md index is structurally unlikely; check defensively.
        new_content = tool_input.get("new_source") or ""

    if not new_content:
        allow("memory-cap: empty payload — nothing to check.")

    violations = find_violating_lines(new_content)
    if not violations:
        allow(f"memory-cap: no `- ` line exceeds {CAP_CHARS} chars.")

    # Build a clear, actionable deny message naming the violators. Cap the detail
    # at 3 entries to keep the message scannable.
    first_n = violations[:3]
    lines_report = "\n".join(
        f"  line {ln}: {n} chars (cap {CAP_CHARS}): {line[:80]}{'…' if len(line) > 80 else ''}"
        for ln, line, n in first_n
    )
    overflow_note = (
        f"\n  ...and {len(violations) - 3} more `- ` line(s) over the cap."
        if len(violations) > 3 else ""
    )
    deny(
        f"memory-cap: write refused — {len(violations)} `- ` line(s) exceed the "
        f"{CAP_CHARS}-char cap (whole line excluding trailing whitespace).\n"
        f"{lines_report}{overflow_note}\n"
        f"Compress each violating entry to <= {CAP_CHARS} chars and move detail into a "
        f"topic file (the index should stay a one-line-per-entry table of contents). "
        f"To bypass for one tool call only, set {OWN_BYPASS_ENV}=1 (logged on use)."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        try:
            deny("memory-cap: unhandled exception — fail-closed.")
        except SystemExit:
            raise
