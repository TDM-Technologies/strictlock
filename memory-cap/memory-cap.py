#!/usr/bin/env python3
"""memory-cap: a fail-closed PreToolUse hook that caps the size of an agent's memory index.

WHY THIS EXISTS
  Many agent harnesses auto-load a memory "index" file into the context of every
  session. Left unmanaged that file grows without bound, and it is frequently the
  single largest fixed context cost paid on every turn. The usual remedy is a
  written convention ("keep index entries to one line; move detail to topic
  files") — but conventions are advisory and quietly rot. This hook makes the
  convention STRUCTURAL: any Write/Edit to the index file whose new content
  contains an index entry (a `- ` line) longer than a configurable cap is REFUSED
  at the tool boundary, before it lands. Optional total caps (entry count, summed
  entry characters) bound the index as a whole. Only the lines a write introduces
  or modifies are judged (a pre-existing over-cap line is never re-flagged), so
  the index cannot self-wedge through Write, Edit or NotebookEdit.

  Part of StrictLock (https://github.com/TDM-Technologies/strictlock). Standalone —
  no other module required.

WHAT COUNTS
  Only `- ` (dash + space) lines — the index-entry convention — are measured.
  Heading lines, blockquote callouts, blank lines, and any other prose pass
  unconditionally. Only the configured index file is checked; every other file
  (including topic files in the same directory) is exempt — those are the
  intended offload destination.

FAIL-CLOSED
  - Master switch MEMORY_CAP must be on/1/true/yes (case-insensitive) or the hook is inert
    (allow-all).
  - A uniquely-named bypass MEMORY_CAP_BYPASS=on (or 1/true/yes) skips the cap. It is single-use
    by convention: set in the harness environment (the env Claude Code was started with, or the
    settings.json env block), not as a prefix on the agent's command; every use is announced on
    stderr, so a lingering bypass is visible in the trail. A distinct bypass per gate is
    deliberate: bypassing one fail-closed gate must never silently disable another.
  - On a stdin parse error or an unhandled crash the hook DENIES (fail-closed).

CONFIGURATION (all via environment)
  MEMORY_CAP            on/1/true/yes (case-insensitive) to enable; anything else
                        makes the hook inert.
  MEMORY_CAP_BYPASS     on/1/true/yes to bypass the cap (single-use by convention: set in the
                        harness environment; every use is announced on stderr).
  MEMORY_CAP_CHARS      integer cap on a `- ` line, whole-line excluding trailing
                        whitespace (what the model actually loads). Default 200.
  MEMORY_CAP_PATH_RE    regex identifying the index file to cap, matched
                        case-insensitively against the forward-slash form of the
                        target path. Default matches a Claude Code per-project
                        memory index:
                            /.claude/projects/<project>/memory/memory.md
  MEMORY_CAP_MAX_ENTRIES  opt-in cap on the number of `- ` entries in the index.
  MEMORY_CAP_TOTAL_CHARS  opt-in cap on the summed length of the `- ` entries.
                        Both are judged in the same changed-region pass: a write is
                        refused only if the result is over the cap AND larger than
                        the pre-existing file, so an index already over a cap is
                        never wedged (shrinking / level edits pass). Not evaluated
                        for NotebookEdit (one cell's source is not the whole file).
  MEMORY_CAP_DECISION_LOG on/1/true/yes to record every decision (allow, deny, bypass) on the
                        capped index to a hash-chained JSONL log (best-effort,
                        never blocks; paths/sizes/reasons only). Requires:
  MEMORY_CAP_LOG_DIR    directory for memory-cap-decisions.log.
  `memory-cap.py verify-log [path]` verifies the chain (exit 0 intact, 2 damaged);
  the chain format mirrors plan-gate's.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Master gate + own uniquely-named bypass.
MASTER_GATE_ENV = "MEMORY_CAP"
OWN_BYPASS_ENV = "MEMORY_CAP_BYPASS"

_TRUTHY = ("on", "1", "true", "yes")


def _env_truthy(name: str) -> bool:
    """True when the env var is on/1/true/yes (case-insensitive, whitespace-trimmed).
    Every other value, including unset and empty, is off. (Copied per module on
    purpose: each module is a standalone single file.)"""
    return os.environ.get(name, "").strip().lower() in _TRUTHY



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


def _opt_in_limit(name: str):
    """Optional positive-integer limit from the environment; None (feature off)
    when unset, non-numeric, or <= 0."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        val = 0
    if val <= 0:
        # Keep the cap OFF, but say so: a typo must not silently disable a requested gate.
        print(f"memory-cap: {name}={raw!r} is not a positive integer; this total cap is OFF.",
              file=sys.stderr)
        return None
    return val


# Opt-in total-size caps over the index's `- ` entries (both off by default).
MAX_ENTRIES = _opt_in_limit("MEMORY_CAP_MAX_ENTRIES")
TOTAL_CHARS = _opt_in_limit("MEMORY_CAP_TOTAL_CHARS")

# Predicate matching the index file to cap. Configurable; the default targets a
# Claude Code per-project memory index. Falls back to the default on an invalid
# regex. (A valid-but-wrong pattern still matches the wrong file; that is not
# detectable here.)
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


# --- Opt-in decision log -----------------------------------------------------
# Hash-chained JSONL decision log. The row shape, chain algorithm, append lock,
# tail recovery and verifier below MIRROR plan-gate's chain (plan-gate/plan-gate.py
# `_log_decision` / `_Flock` / `_read_prev_hash` / `verify_log`) so every gate in
# the suite has the same tamper-evident trail. COPIED, not imported: modules stay
# standalone. Keep the two in step by hand if the chain format ever changes.
#
# Opt-in: MEMORY_CAP_DECISION_LOG=1 AND MEMORY_CAP_LOG_DIR set. Best-effort — a
# logging failure NEVER changes a decision. Privacy: paths, sizes and reasons
# only; file contents / payloads are never logged. Bypass uses are logged too.
# Not logged: gate disabled (MEMORY_CAP != on) and calls that are not Write/Edit/
# NotebookEdit or do not target the capped index file (not decisions of this gate).

DECISION_LOG_FLAG_ENV = "MEMORY_CAP_DECISION_LOG"
LOG_DIR_ENV = "MEMORY_CAP_LOG_DIR"
DECISION_LOG_NAME = "memory-cap-decisions.log"
_GENESIS = "0" * 64

# Context for the row about to be written; filled in as main() learns more.
_CTX = {"tool": "", "target": "", "surface": "file"}


def _decision_log_path():
    """Path of the decision log, or None when logging is not enabled."""
    if not _env_truthy(DECISION_LOG_FLAG_ENV):
        return None
    d = os.environ.get(LOG_DIR_ENV, "").strip()
    if not d:
        return None
    return Path(d) / DECISION_LOG_NAME


def _canonical_row_hash(prev: str, row_without_h: dict) -> str:
    """sha256 over prev + newline + canonical JSON of the row (minus `h`)."""
    canon = json.dumps(
        row_without_h, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256((prev + "\n" + canon).encode("utf-8")).hexdigest()


class _Flock:
    """Best-effort exclusive advisory lock (POSIX fcntl.flock, Windows
    msvcrt.locking, silent no-op elsewhere). Lock failure never blocks."""

    def __init__(self, fh):
        self._fh = fh
        self._impl = None

    def __enter__(self):
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            self._impl = "fcntl"
        except Exception:
            try:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                self._impl = "msvcrt"
            except Exception:
                self._impl = None
        return self

    def __exit__(self, *exc):
        try:
            if self._impl == "fcntl":
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            elif self._impl == "msvcrt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        return False


def _read_prev_hash(fh) -> str:
    """prev for the next row: `h` of the last row, or sha256 of the raw last line
    if it is unparseable (chain continues verifiably past damage). Split on
    b"\\n" only, exactly like the verifier."""
    try:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return _GENESIS
        back = min(size, 262144)
        fh.seek(size - back)
        chunk = fh.read(back)
        lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
        if not lines:
            return _GENESIS
        last = lines[-1]
        try:
            row = json.loads(last.decode("utf-8"))
            h = row.get("h") if isinstance(row, dict) else None
            if isinstance(h, str) and len(h) == 64:
                return h
        except Exception:
            pass
        return hashlib.sha256(last).hexdigest()
    except Exception:
        return _GENESIS


def _safe_str(v, cap: int) -> str:
    """Length-cap and strip un-encodable code points so a row always serializes."""
    s = str(v)[:cap]
    try:
        return s.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return s.encode("ascii", "replace").decode("ascii")


def _log_decision(decision: str, class_: str, reason: str) -> None:
    """Append one hash-chained row. Best-effort; never raises."""
    try:
        path = _decision_log_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cwd_str = str(Path(os.getcwd()))
        except OSError:
            cwd_str = ""
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": decision,
            "surface": _safe_str(_CTX.get("surface", "file"), 20),
            "tool": _safe_str(_CTX.get("tool", ""), 200),
            "target": _safe_str(_CTX.get("target", ""), 2048),
            "command": "",
            "class": _safe_str(class_, 100),
            "reason": _safe_str(reason, 300),
            "plan": "",
            "cwd": _safe_str(cwd_str, 1024),
        }
        with open(str(path), "a+b") as fh:
            with _Flock(fh):
                row["prev"] = _read_prev_hash(fh)
                row["h"] = _canonical_row_hash(row["prev"], row)
                fh.seek(0, os.SEEK_END)
                fh.write(
                    (
                        json.dumps(
                            row, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False,
                        ) + "\n"
                    ).encode("utf-8")
                )
                fh.flush()
    except Exception:
        pass  # Never let logging failure change a decision.


def verify_log(path_arg) -> int:
    """Verify the hash chain. Exit 0 = intact; 2 = damage/tampering/no log."""
    p = Path(path_arg) if path_arg else _default_log_path()
    if p is None:
        print(f"verify-log: no path given and {LOG_DIR_ENV} is unset.", file=sys.stderr)
        return 2
    if not p.exists():
        print(f"verify-log: {p} does not exist.", file=sys.stderr)
        return 2
    raw_lines = p.read_bytes().split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    expected = _GENESIS
    problems = []
    rows = 0
    for i, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            problems.append(f"line {i}: blank line (never written by the gate)")
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise ValueError("not an object")
        except Exception:
            problems.append(
                f"line {i}: unparseable — damage detected; chain re-anchors after it"
            )
            expected = hashlib.sha256(raw).hexdigest()
            continue
        rows += 1
        prev = row.get("prev")
        h = row.get("h")
        if prev != expected:
            problems.append(
                f"line {i}: prev-hash mismatch (expected {expected[:12]}…, "
                f"found {str(prev)[:12]}…) — a row was altered, inserted, or removed"
            )
        row_without_h = {k: v for k, v in row.items() if k != "h"}
        if _canonical_row_hash(str(prev), row_without_h) != h:
            problems.append(f"line {i}: row hash invalid — row content was altered")
        if isinstance(h, str) and len(h) == 64:
            expected = h
        else:
            expected = hashlib.sha256(raw).hexdigest()
    if problems:
        print(f"verify-log: {p}: {len(problems)} problem(s) across {rows} row(s):")
        for msg in problems[:50]:
            print("  " + msg)
        return 2
    print(f"verify-log: {p}: chain intact — {rows} row(s) verified.")
    return 0


def _default_log_path():
    d = os.environ.get(LOG_DIR_ENV, "").strip()
    return (Path(d) / DECISION_LOG_NAME) if d else None


def allow(reason: str, class_=None) -> None:
    """Allow. A row is logged only when *class_* is given (decisions of this
    gate on the capped index); pass-throughs and the inert gate pass none."""
    if class_:
        _log_decision("allow", class_, reason)
    _emit("allow", reason)


def deny(reason: str, class_: str = "denied", log_reason=None) -> None:
    """Fail-closed dominant — every blocking path routes here. *log_reason*, when
    given, replaces the user-facing text in the log row (the deny message can
    quote an excerpt of the offending line; the log must not)."""
    _log_decision("deny", class_, log_reason or reason)
    _emit("deny", reason)


def is_index_target(p: str | None) -> bool:
    """Match the configured index path (Windows-separator tolerant, case-folded)."""
    if not p:
        return False
    return MEMORY_MD_PATH_RE.search(p.replace("\\", "/")) is not None


def find_violating_lines(content: str) -> list:
    """Return [(line_no_1based, line_stripped, char_count)] for over-cap `- ` lines.

    Whole-content scan: every over-cap `- ` (dash + space) index entry, with no
    pre-existing baseline. Equivalent to ``introduced_violating_lines("", content)``
    (an empty baseline makes every `- ` line "introduced"). The changed-region
    check in ``main`` uses ``introduced_violating_lines`` so a write is judged only
    on the lines it introduces or modifies; this whole-file form remains the
    primitive for callers that genuinely want every over-cap line.
    """
    return introduced_violating_lines("", content)


def _index_line_counts(content: str) -> dict:
    """Multiset (Counter-like dict) of rstrip-normalized `- ` lines in *content*.

    Lines are split with ``splitlines()`` (which treats CRLF, CR, and LF
    uniformly) and ``rstrip``-ed, so the same logical entry compares equal
    regardless of the line ending the file was authored with. Only `- ` lines
    are counted; all other prose is ignored. This is what makes the changed-region
    scan OS-agnostic: a CRLF-authored index never reads as "all lines introduced"
    against an LF-normalized write.
    """
    counts: dict = {}
    for line in content.splitlines():
        if not line.startswith(INDEX_LINE_PREFIX):
            continue
        key = line.rstrip()
        counts[key] = counts.get(key, 0) + 1
    return counts


def introduced_violating_lines(existing_content: str, new_content: str) -> list:
    """Over-cap `- ` lines that *new_content* INTRODUCES or MODIFIES vs *existing_content*.

    Pure function of (existing_content, new_content) — knows nothing about the
    harness, the hook payload, or the filesystem, so it stays reusable and
    testable in isolation, and keeps the changed-region logic harness-agnostic.
    The "changed region" is the multiset delta of rstrip-normalized `- ` lines:
    an index entry that appears more times in the result than it did in the
    pre-existing file is treated as introduced/modified for that surplus; an
    entry present in equal-or-greater count in the original is pre-existing and
    untouched, and is NEVER flagged — that is what removes the self-wedge. A
    brand-new or unreadable file is modeled as an empty *existing_content*, so
    every `- ` line counts as introduced and a fresh over-cap write is still
    correctly denied.

    Returns the same shape as ``find_violating_lines`` —
    ``[(line_no_1based, line_stripped, char_count)]`` — using line numbers from
    *new_content* so the deny message points at the result the write would land.
    """
    remaining = _index_line_counts(existing_content)  # one credit per untouched occurrence
    violations = []
    for i, line in enumerate(new_content.splitlines(), start=1):
        if not line.startswith(INDEX_LINE_PREFIX):
            continue
        stripped = line.rstrip()
        if remaining.get(stripped, 0) > 0:
            # Matches a pre-existing occurrence — untouched, never flagged.
            remaining[stripped] -= 1
            continue
        # Introduced or modified by this write — subject it to the cap.
        n = len(stripped)
        if n > CAP_CHARS:
            violations.append((i, stripped, n))
    return violations


def _index_totals(content: str) -> tuple:
    """(entry_count, total_chars) over the `- ` lines of *content*; each entry's
    length is the rstripped line, the same measure as the per-line cap."""
    n = 0
    chars = 0
    for line in content.splitlines():
        if line.startswith(INDEX_LINE_PREFIX):
            n += 1
            chars += len(line.rstrip())
    return n, chars


def total_cap_violations(existing_content: str, new_content: str) -> list:
    """Opt-in total-size caps, judged on the same delta as the per-line cap.

    A cap is violated only when the result is over it AND larger than the
    pre-existing file on that measure: an index already over a cap is never
    wedged — edits that keep it level or shrink it pass, so the way out
    (compressing / removing entries) always stays open. A write that newly
    exceeds the cap, or grows an already-over index, is refused. Returns a list
    of human-readable violation strings (empty when both caps are off or met).
    """
    out = []
    old_n, old_c = _index_totals(existing_content)
    new_n, new_c = _index_totals(new_content)
    if MAX_ENTRIES is not None and new_n > MAX_ENTRIES and new_n > old_n:
        out.append(
            f"{new_n} `- ` entries (cap {MAX_ENTRIES}; index had {old_n} before this write)"
        )
    if TOTAL_CHARS is not None and new_c > TOTAL_CHARS and new_c > old_c:
        out.append(
            f"{new_c} total chars across `- ` entries (cap {TOTAL_CHARS}; "
            f"index had {old_c} before this write)"
        )
    return out


def read_existing_or_empty(file_path: str | None) -> str:
    """Best-effort read of the current on-disk content; "" for new/unreadable.

    Used by the Write path to obtain the pre-existing `- ` multiset. A file that
    does not exist yet (brand-new write) or cannot be read returns "", which the
    delta treats as "every `- ` line is introduced" — so a fresh over-cap write
    is still denied, and an unreadable file degrades to the original whole-file
    behavior (strictly safe: it can only flag MORE, never wedge a real edit,
    because the Write path always supplies the full intended content).
    """
    if not file_path:
        return ""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def reconstruct_edit_content(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, str] | None:
    """Apply Edit-tool semantics to the existing file and return ``(existing, result)``.

    Returns ``(existing_content, post_edit_content)`` — the pre-existing file
    content (the baseline for the changed-region delta) paired with the
    reconstructed result — or **None to signal "defer to the Edit tool"** when the
    hook CANNOT faithfully reconstruct what the Edit tool will actually produce.
    None is returned when ANY of:

      * the file cannot be read (transient IO error / race), OR
      * ``old_string`` is empty, OR
      * ``old_string`` does not occur in the file — the Edit tool will reject
        with "string not found", OR
      * ``replace_all`` is False and ``old_string`` occurs more than once — the
        Edit tool will reject with "string not unique".

    In every None case the edit will NOT apply as specified, so the file's
    content does not change. Evaluating that unchanged content and DENYING would
    misattribute a PRE-EXISTING over-cap line to the user's edit: a compressing
    edit whose ``old_string`` did not byte-exactly match would be refused with a
    cap message about a line the edit doesn't even touch — wedging the index
    against every edit, including the edit meant to fix the offending line.
    Deferring (the caller allows on None) lets the Edit tool surface its own
    accurate "string not found / not unique" error instead.

    When reconstruction IS faithful (old_string present, and unique unless
    replace_all): replace_all=True swaps every occurrence; replace_all=False
    swaps the single unique occurrence — a mirror of the Edit tool. The caller
    diffs the RESULT against the returned ``existing`` (not the stale file alone)
    so a genuine compressing edit passes and only an edit that INTRODUCES a new
    over-cap line is refused; a pre-existing over-cap line the edit merely retains
    is untouched and never flagged.
    """
    try:
        existing = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    # An empty old_string is malformed (the Edit tool rejects it); defer.
    if not old_string:
        return None
    count = existing.count(old_string)
    if count == 0:
        # Edit tool will reject: string not found. The edit won't apply, so the
        # post-edit content is indeterminate from the hook's side — defer.
        return None
    if not replace_all and count > 1:
        # Edit tool will reject: string not unique (replace_all is False). We
        # cannot know which occurrence the user means — defer.
        return None
    if replace_all:
        return existing, existing.replace(old_string, new_string)
    return existing, existing.replace(old_string, new_string, 1)


def main() -> None:
    # Master gate — inert unless explicitly turned on.
    if not _env_truthy(MASTER_GATE_ENV):
        allow(f"memory-cap: {MASTER_GATE_ENV} is not on/1/true/yes — gate disabled.")

    # Uniquely-named bypass; single-use by convention (set inline per command),
    # announced on stderr on every use.
    if _env_truthy(OWN_BYPASS_ENV):
        print(
            f"memory-cap: {OWN_BYPASS_ENV}={os.environ.get(OWN_BYPASS_ENV, '').strip()} — cap DELIBERATELY bypassed for this "
            f"tool call (every use is logged on stderr; single-use by convention).",
            file=sys.stderr,
        )
        # Best-effort payload parse (only when the decision log is on) to enrich
        # the bypass row with tool/target; a parse failure never blocks a bypass.
        if _decision_log_path() is not None:
            try:
                bp = json.load(sys.stdin)
                _CTX["surface"] = "gate"
                _CTX["tool"] = str(bp.get("tool_name") or "")
                bti = bp.get("tool_input") or {}
                _CTX["target"] = str(bti.get("file_path") or bti.get("notebook_path") or "")
            except Exception:
                _CTX["surface"] = "gate"
        allow(
            f"memory-cap: {OWN_BYPASS_ENV}={os.environ.get(OWN_BYPASS_ENV, '').strip()} — explicit bypass (set in the harness "
            f"environment; every use is logged).",
            "emergency_bypass",
        )

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
    except Exception as e:
        _CTX["surface"] = "gate"
        deny(f"memory-cap: could not parse stdin JSON ({e}) — fail-closed.", "parse_error")
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

    _CTX["tool"] = str(tool_name)
    _CTX["target"] = str(target_path)

    # Reconstruct the intended new content AND establish the pre-existing baseline
    # per-tool. The cap is then applied only to the `- ` lines the write
    # introduces or modifies (the multiset delta), never to pre-existing untouched
    # lines — this is what stops the index from self-wedging.
    if tool_name == "Write":
        new_content = tool_input.get("content") or ""
        # The current on-disk file is the baseline; a brand-new or unreadable file
        # yields "" so every `- ` line counts as introduced (fresh over-cap denied).
        existing_content = read_existing_or_empty(target_path)
    elif tool_name == "Edit":
        old_s = tool_input.get("old_string")
        new_s = tool_input.get("new_string")
        replace_all = bool(tool_input.get("replace_all", False))
        if old_s is None or new_s is None:
            allow("memory-cap: Edit payload missing old_string/new_string — letting Edit validate.",
                  "edit_deferred")
        reconstructed = reconstruct_edit_content(target_path, old_s, new_s, replace_all)
        if reconstructed is None:
            # Cannot faithfully reconstruct the post-edit content (file
            # unreadable, or old_string empty / absent / non-unique). The Edit
            # tool will apply or reject the edit on its own; deferring avoids
            # misattributing a PRE-EXISTING over-cap line to this edit. The Edit
            # tool surfaces its own accurate "string not found / not unique" error.
            allow(
                f"memory-cap: cannot faithfully reconstruct the Edit result for "
                f"{target_path!r} (file unreadable, or old_string empty / absent "
                f"/ non-unique) — deferring to the Edit tool's own validation.",
                "edit_deferred",
            )
        # Reuse the content the reconstruction already read as the delta baseline
        # (no second disk read).
        existing_content, new_content = reconstructed
    else:
        # NotebookEdit on a .md index is structurally unlikely; check defensively.
        # Baseline against the on-disk content (like Write) so a source that merely
        # retains a pre-existing over-cap line is never re-flagged — it cannot
        # self-wedge. new_source is one cell's text, not the whole file, so the
        # opt-in TOTAL caps (which need the whole result) are not evaluated here.
        new_content = tool_input.get("new_source") or ""
        existing_content = read_existing_or_empty(target_path)

    if not new_content:
        allow("memory-cap: empty payload — nothing to check.", "empty_payload")

    # Scan only the changed region: lines this write introduces or modifies.
    violations = introduced_violating_lines(existing_content, new_content)
    total_violations = (
        total_cap_violations(existing_content, new_content)
        if tool_name != "NotebookEdit" else []
    )
    if total_violations and not violations:
        deny(
            f"memory-cap: write refused — the index would exceed a total-size cap: "
            f"{'; '.join(total_violations)}.\n"
            f"Remove or merge entries (a write that keeps the index level or shrinks it "
            f"is allowed), and move detail into topic files. To bypass, set "
            f"{OWN_BYPASS_ENV}=1 in the environment the harness starts the hook with "
            f"(the env Claude Code was launched with, or the settings.json env block) — "
            f"it cannot be passed as a prefix on the agent's command; every use is "
            f"logged, so a lingering bypass is visible in the trail.",
            "total_cap",
        )
    if not violations:
        allow(
            f"memory-cap: no INTRODUCED/MODIFIED `- ` line exceeds {CAP_CHARS} chars "
            f"(pre-existing lines are not re-checked).",
            "within_cap",
        )

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
    if total_violations:
        overflow_note += f"\n  Also over a total cap: {'; '.join(total_violations)}."
    deny(
        f"memory-cap: write refused — {len(violations)} INTRODUCED/MODIFIED `- ` "
        f"line(s) exceed the {CAP_CHARS}-char cap (whole line excluding trailing "
        f"whitespace).\n"
        f"{lines_report}{overflow_note}\n"
        f"Compress each violating entry to <= {CAP_CHARS} chars and move detail into a "
        f"topic file (the index should stay a one-line-per-entry table of contents). "
        f"To bypass, set {OWN_BYPASS_ENV}=1 in the environment the harness starts the "
        f"hook with (the env Claude Code was launched with, or the settings.json env "
        f"block) — it cannot be passed as a prefix on the agent's command; every use is "
        f"logged, so a lingering bypass is visible in the trail.",
        "over_cap",
        log_reason=(
            f"memory-cap: {len(violations)} introduced/modified `- ` line(s) exceed the "
            f"{CAP_CHARS}-char cap (longest {max(n for _, _, n in violations)} chars)."
        ),
    )


if __name__ == "__main__":
    # Argv mode: `memory-cap.py verify-log [path]` checks the decision-log hash
    # chain (exit 0 intact / 2 damage). Hook mode (no args) reads stdin as usual.
    if len(sys.argv) >= 2 and sys.argv[1] == "verify-log":
        sys.exit(verify_log(sys.argv[2] if len(sys.argv) > 2 else None))
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        try:
            deny("memory-cap: unhandled exception — fail-closed.", "crash")
        except SystemExit:
            raise
