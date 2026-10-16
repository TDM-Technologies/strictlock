#!/usr/bin/env python3
"""Plan-gate: a fail-closed PreToolUse hook for an agentic coding harness.

This is a SANITIZED reference extract of a gate running in production against a
regulated product. Machine-specific paths, usernames, tenant identifiers, and
internal tracker IDs have been removed; the enforcement logic is intact. It is
published as the reference implementation for the `plan-gate` paper (see
`paper.md`). Adapt the configuration to your own environment.

WHAT IT DOES
  Reads tool-use JSON from stdin, consults the single active "plan" file in a
  configured plans directory, and emits an allow/deny decision on stdout. An
  agent may only modify the exact files and run commands matching the command
  prefixes an approved plan enumerates. Anything else is denied BEFORE it runs.
  The gate fails closed on policy AND on its own faults: when in doubt, deny.

HOOK CONTRACT (typical agentic-harness PreToolUse convention)
  - Exit 0 with JSON {"hookSpecificOutput": {"permissionDecision": "allow|deny", ...}}
                        every decision, INCLUDING the gate's own faults.
  - Exit 1 + stderr  => non-blocking warning: the harness lets the action
                        proceed. Deliberately UNUSED — a gate that exits 1 on its
                        own crash fails open.
  - Exit 2           => hard block (unused here; we express blocks as "deny" JSON).

FAULT POSTURE (fail closed, deliberately)
  - A stdin payload that cannot be parsed, or any unhandled exception inside
    the gate, emits a DENY decision (JSON on stdout, exit 0) whose reason says
    what failed and how to bypass. The traceback goes to stderr and a
    best-effort `gate_error` row goes to the decision log (a logging failure
    never changes the decision).
  - PLAN_GATE_BYPASS=on is honored IN the crash path: the top-level handler
    re-checks PLAN_GATE and PLAN_GATE_BYPASS before it decides, so the
    documented escape hatch still works when the fault is in the gate itself.
    Every bypass use is logged (class emergency_bypass).
  - Accepted trade-off: if the harness changes its payload protocol, the gate
    denies until it is updated or bypassed. A visible, logged denial is the
    safe failure; a silent allow is not.

CONFIGURATION (all via environment; no machine-specific defaults are baked in)
  PLAN_GATE                 on/1/true/yes (case-insensitive) to enable; anything
                            else makes the hook inert.
  PLAN_GATE_BYPASS          on/1/true/yes is an emergency escape hatch.
  PLAN_GATE_PLANS_DIR       directory holding plan files (one active at a time).
  PLAN_GATE_ALWAYS_WRITABLE optional os.pathsep-separated list of directories
                            that are always writable regardless of plan state
                            (e.g. the plans dir itself, an agent-memory dir that
                            never ships). Same blast radius as plan files.
  PLAN_GATE_LOG_DIR         optional directory for the append-only, hash-chained
                            decision log (plan-gate-decisions.log).
  PLAN_GATE_LOG_DECISIONS   "all" (default) records every decision on a gated
                            surface, allow and deny; "deny" records denials plus
                            emergency-bypass rows only (volume opt-down).
  PLAN_GATE_WRITER_SCRIPTS  optional comma-separated name.py:argpos list of
                            writer scripts (built in: atomic-write.py:1). A
                            python/python3/py segment running one is a file write
                            to its argpos-th (1-based) positional argument, gated
                            like a `>` target. Malformed value fails closed.
  PLAN_GATE_MCP_WRITE_TOOLS optional comma-separated list of MCP tool names that
                            modify external state (e.g. a cloud-drive create/copy
                            tool). A bare name is default-denied. `tool:arg` or
                            `tool:arg1+arg2` names the argument(s) holding target
                            paths (a list of strings = every string a target):
                            each is checked like an Edit/Write file_path. A
                            missing/empty/non-string argument denies.

RULES
  - If PLAN_GATE is not on/1/true/yes (case-insensitive), allow everything.
  - Exactly one plan file must have `status: active` in its YAML frontmatter
    (scoped per worktree — see find_active_plans).
  - Edit / Write / NotebookEdit:
      target file_path must EXACTLY match an entry in the plan's allowed_paths,
      OR sit under one of the PLAN_GATE_ALWAYS_WRITABLE directories.
  - Bash:
      the command is split into SEGMENTS on && || ; | & and unquoted newlines,
      and the bodies of $(...), `...`, <(...), >(...) and ( ... ) are checked
      as segments too. EACH segment must independently be a known read-only
      command, a bare `cd`, or start with an entry in allowed_commands — a
      compound command is only as authorized as its least-authorized segment.
      Redirections that write (>, >>, >|, &>, N>) are file writes, gated
      exactly like Edit/Write (/dev/null is always fine; >&N fd dups and
      input redirection are not writes). Anything the splitter cannot
      confidently parse is denied (class unparseable_command).
      A segment running a writer script (python atomic-write.py TARGET, see
      PLAN_GATE_WRITER_SCRIPTS) is a file write to TARGET, gated the same way.
  - PowerShell:
      split on ; | || & && and newlines; EACH segment must be a known read-only
      command or start (at a word boundary) with an allowed_commands entry.
      Subexpressions, script blocks, backticks, redirection and file-writing
      cmdlets (Out-File, Set-Content, ...) are refused (fail closed).
  - Bypass ordering: for Edit/Write the always-writable bypass runs BEFORE the
    active-plan check; for Bash/PowerShell the read-only bypass also runs BEFORE
    the active-plan check (so a session with no active plan can still inspect
    state with `git log`, `pwd`, `Get-ChildItem`, etc.). Plan-scoped checks run
    AFTER the active-plan check.
  - Every decision on a gated surface (allow AND deny) appends a hash-chained
    JSONL row to the decision log; `plan-gate.py verify-log [path]` checks the
    chain. Best-effort; a logging failure NEVER changes a decision. The gate
    never authorizes a gated file-write into PLAN_GATE_LOG_DIR itself (log
    self-protection) — no plan entry or always-writable dir can override that.

PATH RESOLUTION
  - allowed_paths may be absolute OR repo-root-relative.
  - Relative entries resolve against the nearest repo root found by walking up
    from the session CWD. If CWD is under a git WORKTREE (.git is a file, not a
    dir), the WORKTREE ROOT is the anchor — not the main repo root. This is the
    fix for the silent-drift pattern where a worktree-CWD session wrote to the
    main repo because the plan listed main-repo absolute paths.
  - Worktree claim: if CWD is under a worktree, a plan is active for that
    session only if at least one ABSOLUTE allowed_paths entry resolves under the
    worktree (or it declares `worktree_bypass: true`) — claim-by-absolute, applied
    in find_active_plans. A misaligned plan is not active there, so gated writes
    and commands outside the read-only set are denied; read-only inspection and
    always-writable dirs stay available. When active plans exist elsewhere the
    deny says so ("N active plan(s) exist, none claims this worktree — likely
    drift", class silent_drift) instead of the generic no-active-plan text.
    Fix the plan first.
  - Opt-out: a plan can declare `worktree_bypass: true` in frontmatter to skip
    the guard. Use when the work is deliberately cross-tree and the
    allowed_paths are absolute.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


_TRUTHY = ("on", "1", "true", "yes")


def _env_truthy(name: str) -> bool:
    """True when the env var is on/1/true/yes (case-insensitive, whitespace-trimmed).
    Every other value, including unset and empty, is off. (Copied per module on
    purpose: each module is a standalone single file.)"""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name, "").strip()
    return Path(val) if val else None


# Test-only override: redirect the plans scan to a temp dir so a test harness
# can stage fake plan files without colliding with a real active plan. Not a
# security bypass — an attacker who can set this env can also just write a
# permissive plan to the real plans dir; the defense is workflow, not a token.
_plans_dir_override = os.environ.get("_PLAN_GATE_TEST_PLANS_DIR", "").strip()
if _plans_dir_override:
    PLANS_DIR: Path | None = Path(_plans_dir_override)
else:
    PLANS_DIR = _env_path("PLAN_GATE_PLANS_DIR")

# Directories that are always writable regardless of plan state. Intended for
# the plans dir itself and any agent-memory dir that never ships to production
# (identical blast radius to plan files). os.pathsep-separated.
ALWAYS_WRITABLE_DIRS: list[Path] = [
    Path(p.strip())
    for p in os.environ.get("PLAN_GATE_ALWAYS_WRITABLE", "").split(os.pathsep)
    if p.strip()
]

LOGS_DIR = _env_path("PLAN_GATE_LOG_DIR")
DECISIONS_LOG = (LOGS_DIR / "plan-gate-decisions.log") if LOGS_DIR else None

# "all" (default): every decision on a gated surface is recorded, allow and
# deny. "deny": denials + emergency-bypass rows only (volume opt-down).
LOG_DECISIONS_MODE = (
    os.environ.get("PLAN_GATE_LOG_DECISIONS", "").strip().lower() or "all"
)

# prev-hash of the first row in a fresh log.
_GENESIS = "0" * 64

# Commands that are always safe — investigation and navigation. Each entry is
# matched at a WORD BOUNDARY against ONE SEGMENT of a command (so `ls` does not
# admit `lsof`, and `cat x; rm y` is two segments, the second of which fails).
# Entries whose mutating forms exist carry an argument check (_RO_VALIDATORS):
#   git branch   listing forms only (no -d/-D/-m/-M/-c/-C/-f/-u/--delete/...,
#                no bare positional = create)
#   git remote   bare, -v, show, get-url only
#   git config   `--get*` forms only, and never with --add/--unset/--edit/...
#   git diff/log/show  no --output[=file] (writes a file)
#   find         no -delete/-exec/-execdir/-ok/-okdir/-fprint*/-fls
#   rg           no --pre/--hostname-bin (they execute a program)
# `git fetch` is NOT here (it writes refs/objects). Validated commands also
# refuse shell expansions in their arguments ($, `, {): an expansion could
# smuggle a mutating flag past the check.
# `cd` is handled separately: a `cd <path>` segment is a navigation no-op.
READ_ONLY_BASH_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git remote",
    "git config --get",
    "git config --get-all",
    "git config --get-regexp",
    "git config --get-urlmatch",
    "git config --get-color",
    "git config --get-colorbool",
    "git check-ignore",
    "git ls-files",
    "git ls-tree",
    "git for-each-ref",
    "git describe",
    "git name-rev",
    "git cat-file",
    "git worktree list",
    "ls",
    "pwd",
    "echo",
    "cat",
    "rg",
    "grep",
    "find",
    "head",
    "tail",
    "wc",
    "which",
    "where",
    "test",
    "true",
    "false",
)

# Conservative PowerShell read-only bypass. PS commands that don't mutate state.
# Matched per SEGMENT at a word boundary (see _ps_segments): a compound is
# read-only only if every segment is.
READ_ONLY_PS_PREFIXES = (
    "Get-ChildItem",
    "Get-Content",
    "Get-Command",
    "Get-Item",
    "Get-Location",
    "Get-Process",
    "Test-Path",
    "Resolve-Path",
)

# MCP tools that modify EXTERNAL state (e.g. a cloud-drive create/copy tool)
# without going through Edit/Write/NotebookEdit/Bash/PowerShell. Such tools
# would otherwise evade the gate entirely via the "tool not in gated set"
# early-allow in main(). When configured, they are DEFAULT-DENIED: the premise
# is that no external-state writes happen during a gated unit of work by design.
# Telemetry to the MCP log falsifies that premise — if legitimate usage appears,
# add an `allowed_mcp_tools` opt-in surface (left as a v2 exercise here).
#
# An entry may name the argument(s) that hold the target path: `tool:arg` or
# `tool:arg1+arg2`. A tool WITH an argument spec has every target (a string
# argument, or every string of a list-of-strings argument) checked exactly like
# an Edit/Write file_path: log self-protection, always-writable dirs, then the
# plan's allowed_paths; a missing/empty/non-string argument denies. A tool
# WITHOUT a spec (a bare name) stays default-denied. A bare entry wins over a
# spec entry for the same tool (the stricter reading).
def _parse_mcp_write_tools(raw: str):
    """(tool_names, {tool: (arg, ...)}) from PLAN_GATE_MCP_WRITE_TOOLS."""
    names: list[str] = []
    specs: dict = {}
    bare: set = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, spec = entry.partition(":")
        name = name.strip()
        if not name:
            continue
        if name not in names:
            names.append(name)
        args = tuple(a.strip() for a in spec.split("+") if a.strip()) if sep else ()
        if not args:
            bare.add(name)  # no (usable) spec: default-deny, as before
            continue
        merged = specs.get(name, ())
        specs[name] = merged + tuple(a for a in args if a not in merged)
    for name in bare:
        specs.pop(name, None)
    return tuple(names), specs


MCP_FILE_WRITE_TOOLS, MCP_TOOL_ARGS = _parse_mcp_write_tools(
    os.environ.get("PLAN_GATE_MCP_WRITE_TOOLS", "")
)


def _mcp_targets(tool: str, tool_input: dict):
    """(targets, problem) for an MCP tool that has an argument spec. Each named
    argument must be a non-empty string, or a non-empty list of non-empty
    strings (every string is a target). Anything else is a problem (deny)."""
    targets: list[str] = []
    for arg in MCP_TOOL_ARGS.get(tool, ()):
        val = tool_input.get(arg)
        if isinstance(val, str):
            vals = [val]
        elif isinstance(val, list) and val:
            vals = val
        else:
            return targets, (
                f"argument {arg!r} is missing, empty or not a string / list of strings"
            )
        for v in vals:
            if not isinstance(v, str) or not v.strip():
                return targets, (
                    f"argument {arg!r} holds an empty or non-string path"
                )
            targets.append(v)
    return targets, None


def _emit(decision: str, reason: str) -> None:
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
    _emit("deny", reason)


def no_decision() -> None:
    """Exit 0 with NO stdout JSON: the hook has no opinion, so the harness runs
    its normal permission flow (an "allow" decision would SKIP the user's
    permission prompt). Used for MCP tools plan-gate does not affirmatively
    check: unconfigured ones, and any MCP call while the gate is off or bypassed."""
    sys.exit(0)


def _peek_tool_name() -> str:
    """Best-effort tool_name from the stdin payload (used only where the gate is
    inert or bypassed and just needs to know whether the call is an MCP tool).
    Never raises; an interactive TTY stdin is not consumed."""
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            payload = json.load(sys.stdin)
            name = payload.get("tool_name", "") if isinstance(payload, dict) else ""
            return name if isinstance(name, str) else ""
    except Exception:
        pass
    return ""


def _allow_or_defer(reason: str, tool: str) -> None:
    """allow(), except an MCP tool gets no decision (see no_decision)."""
    if tool.startswith("mcp__"):
        no_decision()
    allow(reason)


def _canonical_row_hash(prev: str, row_without_h: dict) -> str:
    """sha256 over prev + newline + the canonical JSON of the row (minus `h`).

    Canonical form = sort_keys + compact separators + ensure_ascii=False — the
    exact serialization the writer emits, so a verifier can recompute the hash
    from the parsed row with byte parity. All row values are strings (or dicts
    of strings), so JSON round-tripping is exact.
    """
    canon = json.dumps(
        row_without_h, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256((prev + "\n" + canon).encode("utf-8")).hexdigest()


class _Flock:
    """Best-effort exclusive advisory lock so concurrent sessions can share one
    log: POSIX fcntl.flock, msvcrt.locking on Windows, silent no-op elsewhere.
    Lock failure never blocks — worst case is a detectable chain fork, which
    verify-log surfaces; a wedged gate would be worse.
    """

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
    """prev for the next row, from an open binary handle: `h` of the last row,
    or — if the last line is unparseable (partial write, external damage) —
    sha256 of its raw bytes, so the chain continues verifiably past the damage
    and verify-log can pinpoint the damaged line.
    """
    try:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return _GENESIS
        # Row fields are length-capped in _log_decision so a serialized row is
        # always far smaller than this window — the last line is guaranteed to
        # be complete. Split on b"\n" ONLY (never splitlines): the verifier
        # splits the same way, so a damaged line ending in \r or an exotic
        # break byte hashes identically on both sides.
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
    """Length-cap + strip un-encodable code points (lone surrogates arriving
    via \\uXXXX escapes in the stdin JSON) so a row ALWAYS serializes — the
    record must never lose a decision to its own input. Every string field
    goes through this: the caps bound each field, and _fit_row enforces the
    total serialized-row budget (MAX_ROW_BYTES) so a row can never outgrow
    _read_prev_hash's tail window.
    """
    s = str(v)[:cap]
    try:
        return s.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return s.encode("ascii", "replace").decode("ascii")


MAX_ROW_BYTES = 32 * 1024  # serialized row (incl. prev + h) is kept at or under this


def _row_bytes(row: dict) -> int:
    probe = dict(row)
    probe["prev"] = "0" * 64
    probe["h"] = "0" * 64
    return len(json.dumps(
        probe, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8"))


def _fit_row(row: dict) -> None:
    """Shrink `row` in place until its serialized size is <= MAX_ROW_BYTES, so a
    crafted payload (many long, control-character-heavy targets whose JSON
    escaping inflates 6x) can never outgrow the tail window _read_prev_hash
    reads — which would break the hash chain. Extra targets are dropped (with a
    count), then the longest string is halved with a marker."""
    marker = "...[truncated]"
    for _ in range(200):
        if _row_bytes(row) <= MAX_ROW_BYTES:
            return
        extra = row.get("extra")
        tl = extra.get("targets") if isinstance(extra, dict) else None
        if isinstance(tl, list) and len(tl) > 1:
            extra.setdefault("targets_n", len(tl))
            extra["targets_truncated"] = True
            del tl[len(tl) // 2:]
            continue
        # Halve the longest remaining string (a row field or the first target).
        cands = [(len(v), k) for k, v in row.items() if isinstance(v, str)]
        if isinstance(tl, list) and tl:
            cands.append((len(tl[0]), None))
        if not cands:
            break
        ln, key = max(cands, key=lambda c: c[0])
        if ln <= len(marker) + 1:
            break
        if key is None:
            tl[0] = tl[0][:ln // 2] + marker
        else:
            row[key] = row[key][:ln // 2] + marker


def _log_decision(
    decision: str,
    surface: str,
    class_: str,
    tool: str,
    tool_input: dict | None,
    reason: str,
    plan: str | None,
) -> None:
    """Append one hash-chained JSONL row to the unified decisions log.

    Best-effort; never raises — telemetry is fail-open on a fail-closed gate: a
    logging failure must never change a decision. This trail is the SOC 2 CC7 /
    ISO 42001 monitoring evidence, produced as exhaust of the gate doing its
    job. Privacy invariant: paths, truncated commands, and short field prefixes
    only — file contents and full payloads are NEVER logged.
    """
    if DECISIONS_LOG is None:
        return
    if (
        decision == "allow"
        and LOG_DECISIONS_MODE == "deny"
        and class_ != "emergency_bypass"
    ):
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        tin = tool_input or {}
        target = ""
        command = ""
        extra: dict = {}
        if surface == "file":
            target = _safe_str(tin.get("file_path") or tin.get("notebook_path") or "", 2048)
        elif surface in ("bash", "ps"):
            command = _safe_str(tin.get("command") or "", 500)
        elif surface == "mcp":
            # Tool name only (already in the row). Argument VALUES and names are
            # never logged for MCP calls (they can carry mail, chat, document
            # content); the one exception is the configured path argument(s) of a
            # tool with an argument spec, recorded as targets.
            if tool in MCP_TOOL_ARGS:
                try:
                    mt, _prob = _mcp_targets(tool, tin)
                    if mt:
                        target = _safe_str(mt[0], 2048)
                        extra["targets"] = [_safe_str(t, 2048) for t in mt[:20]]
                        if len(mt) > 20:
                            extra["targets_n"] = len(mt)
                except Exception:
                    extra["targets_error"] = True
        elif surface == "gate":
            # Gate-level rows (emergency bypass): best-effort context from
            # whatever payload shape was captured.
            target = _safe_str(tin.get("file_path") or tin.get("notebook_path") or "", 2048)
            command = _safe_str(tin.get("command") or "", 500)
        try:
            cwd_str = str(Path(os.getcwd()))
        except OSError:
            cwd_str = ""
        row: dict = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": decision,
            "surface": surface,
            "tool": _safe_str(tool, 200),
            "target": target,
            "command": command,
            "class": _safe_str(class_, 100),
            "reason": _safe_str(reason, 300),
            "plan": _safe_str(plan or "", 300),
            "cwd": _safe_str(cwd_str, 1024),
        }
        if extra:
            row["extra"] = extra
        _fit_row(row)
        with open(DECISIONS_LOG, "a+b") as fh:
            with _Flock(fh):
                row["prev"] = _read_prev_hash(fh)
                row["h"] = _canonical_row_hash(row["prev"], row)
                fh.seek(0, os.SEEK_END)
                fh.write(
                    (
                        json.dumps(
                            row,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                fh.flush()
    except Exception:
        pass  # Never let logging failure break the gate.


def verify_log(path_arg: str | None) -> int:
    """Walk a decisions log and verify the hash chain. Exit 0 = intact; exit 2
    = damage or tampering detected (report names the first bad lines). Mirrors
    the writer's chaining rules: a parseable row anchors the chain at its `h`;
    an unparseable line re-anchors at sha256 of its raw bytes; blank lines are
    never written by the gate and are flagged without moving the anchor.
    """
    p = Path(path_arg) if path_arg else DECISIONS_LOG
    if p is None:
        print(
            "verify-log: no path given and PLAN_GATE_LOG_DIR is unset.",
            file=sys.stderr,
        )
        return 2
    if not p.exists():
        print(f"verify-log: {p} does not exist.", file=sys.stderr)
        return 2
    raw_lines = p.read_bytes().split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()  # trailing newline
    expected = _GENESIS
    problems: list[str] = []
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
        # Mirror the writer's anchor rule exactly: a 64-char string `h` anchors
        # the chain; anything else re-anchors at the raw bytes.
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


def norm(p: str) -> str:
    return p.replace("\\", "/").lower().rstrip("/")


_WORD_END = " \t\n;&|<>()"
_MAX_NEST = 24


class _Parsed(object):
    """One parsed command list (the top level, or the body of one substitution).

    segments  simple-command texts, separators removed, redirection operators
              and heredoc bodies removed (so prefix / argument checks see only
              the command words)
    redirects literal TARGETS of write redirections (>, >>, >|, &>, N>, <>)
    subs      parsed bodies of $(...), `...`, <(...), >(...) and ( ... ) found
              in this list — they EXECUTE, so they are checked like the rest
    """

    def __init__(self):
        self.segments = []
        self.redirects = []
        self.subs = []

    def walk(self):
        yield self
        for sub in self.subs:
            for node in sub.walk():
                yield node


class _Scanner(object):
    """A conservative, fail-closed scanner for the shell subset an agent
    realistically emits. It is NOT a faithful shell parser: anything it cannot
    confidently place (unterminated quote or substitution, a redirection target
    built from an expansion, an ambiguous `$((` , too-deep nesting) is recorded
    in `problems`, and the gate denies on any problem.

    Separators (outside quotes): && || ; | |& & and newline. Handled contexts:
    '...' , "..." (with $( ) and backtick inside), $'...', backslash escapes,
    comments, heredocs (quoted tag = literal body; unquoted tag = the body can
    execute $( ) / backticks, so it is scanned for them), $(...), $((...)),
    backticks, <( ) / >( ), bare ( ... ) groups, and redirections.
    """

    def __init__(self, s, problems, depth=0):
        self.s = s
        self.n = len(s)
        self.i = 0
        self.problems = problems
        self.depth = depth

    def _problem(self, msg):
        if msg not in self.problems:
            self.problems.append(msg)

    # -- substitutions ------------------------------------------------------

    def _group(self, res, start):
        """Parse a ( ... ) body (self.i is just past the opening paren)."""
        self.depth += 1
        sub = self.parse_list(")")
        self.depth -= 1
        res.subs.append(sub)
        return self.s[start:self.i]

    def _dollar_paren(self, res):
        """self.i is at `$(`. Consumes through the matching `)`."""
        s, n = self.s, self.n
        if s.startswith("$((", self.i):
            j = self.i + 3
            depth = 0
            while j < n:
                c = s[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    if depth == 0:
                        if j + 1 < n and s[j + 1] == ")":
                            body = s[self.i + 3:j]
                            if re.search(r"[`'\"]|\$\(", body):
                                self._problem(
                                    "arithmetic expansion containing quotes "
                                    "or nested substitutions"
                                )
                            self.i = j + 2
                            return
                        self._problem("ambiguous `$((` ... `)` construct")
                        self.i = n
                        return
                    depth -= 1
                j += 1
            self._problem("unterminated `$((`")
            self.i = n
            return
        start = self.i
        self.i += 2
        self._group(res, start)

    def _backtick(self, res, in_dq):
        """self.i is at an opening backtick. Consumes through the closing one."""
        s, n = self.s, self.n
        j = self.i + 1
        while j < n and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        if j >= n:
            self._problem("unterminated backtick substitution")
            self.i = n
            return
        body = s[self.i + 1:j]
        self.i = j + 1
        body = re.sub(r"\\([\\`$])", r"\1", body)
        if in_dq:
            body = body.replace('\\"', '"')
        if self.depth + 1 > _MAX_NEST:
            self._problem("substitutions nested too deeply")
            return
        sub = _Scanner(body, self.problems, self.depth + 1).parse_list(None)
        res.subs.append(sub)

    def _scan_expansions(self, res, until_dq):
        """Walk text where only expansions matter (inside "..." , or a heredoc
        body when until_dq is False), recording any substitution found."""
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if until_dq and c == '"':
                self.i += 1
                return
            if c == "$" and s.startswith("$(", self.i):
                self._dollar_paren(res)
                continue
            if c == "`":
                self._backtick(res, until_dq)
                continue
            self.i += 1
        self.i = min(self.i, n)
        if until_dq:
            self._problem("unterminated double quote")

    # -- redirections -------------------------------------------------------

    def _read_operand(self, res):
        """Read one shell word (a redirection operand). Returns
        (raw, literal, expanded, quoted); `expanded` is True if the word needs
        a runtime expansion to resolve ($var, $(...), backticks)."""
        s, n = self.s, self.n
        while self.i < n and s[self.i] in " \t":
            self.i += 1
        start = self.i
        lit = []
        expanded = False
        quoted = False
        glob = False  # an UNQUOTED * ? [ { : the shell may expand it
        while self.i < n:
            c = s[self.i]
            if c in _WORD_END:
                break
            if c in "*?[{":
                glob = True
            if c == "\\":
                quoted = True
                if self.i + 1 < n:
                    if s[self.i + 1] != "\n":
                        lit.append(s[self.i + 1])
                    self.i += 2
                else:
                    self.i += 1
                continue
            if c == "'":
                quoted = True
                j = s.find("'", self.i + 1)
                if j == -1:
                    self._problem("unterminated single quote")
                    self.i = n
                    break
                lit.append(s[self.i + 1:j])
                self.i = j + 1
                continue
            if c == '"':
                quoted = True
                st = self.i + 1
                self.i += 1
                self._scan_expansions(res, True)
                inner = s[st:self.i - 1] if s[self.i - 1:self.i] == '"' else s[st:self.i]
                if "$" in inner or "`" in inner:
                    expanded = True
                lit.append(re.sub(r'\\(["\\$`])', r"\1", inner))
                continue
            if c == "`":
                expanded = True
                self._backtick(res, False)
                continue
            if c == "$":
                expanded = True
                if s.startswith("$(", self.i):
                    self._dollar_paren(res)
                else:
                    lit.append(c)
                    self.i += 1
                continue
            lit.append(c)
            self.i += 1
        self._last_glob = glob
        return s[start:self.i], "".join(lit), expanded, quoted

    def _record_write(self, res, operand):
        raw, lit, expanded, _quoted = operand
        if getattr(self, "_last_glob", False):
            self._problem(
                "write redirection target contains an unquoted glob or brace "
                f"character ({raw[:60]!r}); where it lands cannot be resolved "
                "statically"
            )
            return
        if not raw or not lit:
            self._problem("write redirection with no usable target")
            return
        if expanded:
            self._problem(
                "write redirection target uses a shell expansion "
                f"({raw[:60]!r}); it cannot be resolved statically"
            )
            return
        if raw.startswith("~"):
            lit = os.path.expanduser(lit)
        res.redirects.append(lit)

    def _strip_fd(self, buf):
        """Drop a trailing all-digit token (`2` of `2>file`) from the segment."""
        text = "".join(buf)
        m = re.search(r"(^|[ \t])(\d+)$", text)
        if m:
            del buf[:]
            buf.append(text[:m.start(2)])

    def _handle_redirect(self, res, buf, pending):
        """self.i is at `<` or `>` (outside quotes)."""
        s, n = self.s, self.n
        ch = s[self.i]
        nxt = s[self.i + 1] if self.i + 1 < n else ""
        if nxt == "(":  # process substitution: the inner command EXECUTES
            start = self.i
            self.i += 2
            buf.append(self._group(res, start))
            return
        if ch == "<":
            if s.startswith("<<<", self.i):  # here-string: not a write
                self.i += 3
                self._read_operand(res)
                return
            if nxt == "<":  # heredoc
                self.i += 2
                strip_tabs = False
                if self.i < n and s[self.i] == "-":
                    strip_tabs = True
                    self.i += 1
                raw, lit, _exp, quoted = self._read_operand(res)
                if not raw:
                    self._problem("heredoc without a delimiter")
                elif _exp or "$" in raw or "`" in raw:
                    # <<$'EOF' / <<$"EOF" / <<$X: the shell's idea of the
                    # delimiter differs from the literal text we would look
                    # for, so the body boundary cannot be placed. Fail closed.
                    self._problem(
                        "heredoc delimiter uses a `$` or backtick form "
                        f"({raw[:30]!r})"
                    )
                else:
                    pending.append((lit, quoted, strip_tabs))
                return
            if nxt == "&":  # <&N input fd dup: not a write
                self.i += 2
                self._read_operand(res)
                return
            if nxt == ">":  # <> opens read-write (creates): a write
                self.i += 2
                self._record_write(res, self._read_operand(res))
                return
            self.i += 1  # plain input redirection: not a write
            self._read_operand(res)
            return
        # ch == ">"
        self._strip_fd(buf)
        self.i += 1
        if nxt == ">" or nxt == "|":
            self.i += 1
        elif nxt == "&":
            self.i += 1
            op = self._read_operand(res)
            if not op[2] and re.match(r"^(\d+-?|-)$", op[1]):
                return  # >&N / >&- : fd dup or close, not a write
            self._record_write(res, op)  # >&file : stdout+stderr to a file
            return
        self._record_write(res, self._read_operand(res))

    def _read_heredoc_bodies(self, pending, res):
        s, n = self.s, self.n
        for tag, quoted, _strip in pending:
            body = []
            terminated = False
            while self.i < n:
                j = s.find("\n", self.i)
                line = s[self.i:] if j == -1 else s[self.i:j]
                self.i = n if j == -1 else j + 1
                # Terminate on a tag line leniently (strip all whitespace):
                # ending the body EARLY only means more text is checked as
                # commands, never less.
                if line.strip() == tag:
                    terminated = True
                    break
                body.append(line)
            if not terminated:
                self._problem("unterminated heredoc (no closing delimiter line)")
            if not quoted and body:
                if self.depth + 1 > _MAX_NEST:
                    self._problem("substitutions nested too deeply")
                    continue
                sub = _Scanner("\n".join(body), self.problems, self.depth + 1)
                sub._scan_expansions(res, False)

    # -- the command list ---------------------------------------------------

    def parse_list(self, closer):
        """Parse commands up to `closer` (None = end of input, ")" = the
        matching close paren, which is consumed)."""
        s, n = self.s, self.n
        res = _Parsed()
        buf = []
        pending = []  # heredocs whose body starts at the next newline
        closed = closer is None
        groups = set()  # exact texts of bare ( ... ) groups appended to buf
        # True only where bash would START A NEW WORD: input start, after an
        # unescaped blank/newline, or after a separator the loop consumed. A `#`
        # starts a comment only there; after anything else (an escape, a quote,
        # a closed $(...) / `...` / <(...), a plain character) it is part of a
        # word, and the rest of the line is real commands.
        ws = True

        def flush():
            seg = "".join(buf).strip()
            del buf[:]
            # A segment that is nothing but a ( ... ) group has no command of
            # its own: its body was parsed into `subs` and is checked there.
            if seg and seg not in groups:
                res.segments.append(seg)

        if self.depth > _MAX_NEST:
            self._problem("substitutions nested too deeply")
            self.i = n
            return res

        while self.i < n:
            ch = s[self.i]
            was_ws = ws
            ws = False
            if ch == "\n":
                flush()
                self.i += 1
                ws = True
                if pending:
                    self._read_heredoc_bodies(pending, res)
                    del pending[:]
                continue
            if ch == " " or ch == "\t":
                buf.append(ch)
                self.i += 1
                ws = True
                continue
            if ch == "\\":
                if self.i + 1 < n:
                    if s[self.i + 1] == "\n":
                        # Line continuation: bash deletes backslash-newline, so
                        # the text on both sides is ONE continuous word (and a
                        # `#` after it is a comment only if one would have been).
                        ws = was_ws
                    else:
                        buf.append(s[self.i:self.i + 2])
                    self.i += 2
                else:
                    buf.append(ch)
                    self.i += 1
                continue
            if ch == "#" and was_ws:
                j = s.find("\n", self.i)
                self.i = n if j == -1 else j
                continue
            if ch == "'":
                j = s.find("'", self.i + 1)
                if j == -1:
                    self._problem("unterminated single quote")
                    buf.append(s[self.i:])
                    self.i = n
                else:
                    buf.append(s[self.i:j + 1])
                    self.i = j + 1
                continue
            if ch == '"':
                start = self.i
                self.i += 1
                self._scan_expansions(res, True)
                buf.append(s[start:self.i])
                continue
            if ch == "`":
                start = self.i
                self._backtick(res, False)
                buf.append(s[start:self.i])
                continue
            if ch == "$":
                nxt = s[self.i + 1] if self.i + 1 < n else ""
                if nxt == "(":
                    start = self.i
                    self._dollar_paren(res)
                    buf.append(s[start:self.i])
                    continue
                if nxt == "'":  # $'...' ANSI-C quoting: \' does not close it
                    j = self.i + 2
                    while j < n and s[j] != "'":
                        j += 2 if s[j] == "\\" else 1
                    if j >= n:
                        self._problem("unterminated $'...' string")
                        buf.append(s[self.i:])
                        self.i = n
                    else:
                        buf.append(s[self.i:j + 1])
                        self.i = j + 1
                    continue
                buf.append(ch)
                self.i += 1
                continue
            if ch == ";":
                flush()
                self.i += 1
                ws = True
                continue
            if ch == "&":
                nxt = s[self.i + 1] if self.i + 1 < n else ""
                if nxt == "&":
                    flush()
                    self.i += 2
                    ws = True
                    continue
                if nxt == ">":  # &> / &>> : stdout+stderr to a file
                    self.i += 2
                    if self.i < n and s[self.i] == ">":
                        self.i += 1
                    self._record_write(res, self._read_operand(res))
                    continue
                flush()  # lone & : background separator
                self.i += 1
                ws = True
                continue
            if ch == "|":
                flush()
                self.i += 2 if s.startswith(("||", "|&"), self.i) else 1
                ws = True
                continue
            if ch == ")":
                if closer == ")":
                    flush()
                    self.i += 1
                    closed = True
                    break
                self._problem("unbalanced `)`")
                buf.append(ch)
                self.i += 1
                continue
            if ch == "(":
                start = self.i
                self.i += 1
                gtxt = self._group(res, start)
                groups.add(gtxt.strip())
                buf.append(gtxt)
                continue
            if ch == "<" or ch == ">":
                self._handle_redirect(res, buf, pending)
                continue
            buf.append(ch)
            self.i += 1

        flush()
        if pending:
            self._problem("heredoc with no body (input ends before a newline)")
        if not closed:
            self._problem("unterminated `(` or `$(`")
        return res


def _parse_command(cmd: str):
    """Parse a Bash command. Returns (parsed, problems); the gate denies on any
    problem (fail closed)."""
    problems: list[str] = []
    parsed = _Scanner(cmd, problems).parse_list(None)
    return parsed, problems


def _split_top_level(cmd: str) -> list[str]:
    """Top-level segments of cmd (separators, redirections and heredoc bodies
    removed). Kept as the simple splitting entry point; the gate itself uses
    _parse_command so it also sees substitution bodies and redirect targets."""
    return _parse_command(cmd)[0].segments


def _is_cd_noop(seg: str) -> bool:
    return seg == "cd" or seg.startswith(("cd ", "cd\t"))


def _is_cd_like(seg: str) -> bool:
    """A segment that changes the shell's working directory."""
    words = seg.split(None, 1)
    return bool(words) and words[0] in ("cd", "pushd", "popd")


# --- read-only segment checks ------------------------------------------------

def _has_unsafe_expansion(seg: str) -> bool:
    """True if `seg` has text the shell would expand at run time, so the
    token-level argument checks cannot be trusted. Quote-aware:
      - inside '...' nothing expands (a literal `$`, `{`, `*` is fine);
      - inside "..." only `$` and a backtick expand;
      - outside quotes `$`, a backtick, a glob character (* ? [) and a real
        brace expansion ({a,b} / {a..b}) are refused. A `{...}` with no comma
        and no `..` (git ref syntax `@{u}`, `stash@{0}`) is literal and passes.
      - a backslash-escaped character is literal.
    """
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = seg.find("'", i + 1)
            if j == -1:
                return True
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and seg[i] != '"':
                if seg[i] == "\\":
                    i += 2
                    continue
                if seg[i] in "$`":
                    return True
                i += 1
            if i >= n:
                return True
            i += 1
            continue
        if c in "$`*?[":
            return True
        if c == "{":
            j = seg.find("}", i + 1)
            if j == -1:
                return True
            inner = seg[i + 1:j]
            if "," in inner or ".." in inner or any(ch in inner for ch in "{$`\\'\"*?["):
                return True
            i = j + 1
            continue
        i += 1
    return False


def _v_no_output(args: list[str]) -> bool:
    """git diff/log/show: `--output[=file]` (any unambiguous abbreviation) writes
    a file."""
    for a in args:
        if a == "--":
            break
        name = a.split("=", 1)[0]
        if name.startswith("--output-indicator-"):
            continue  # a display flag, not --output
        if len(name) >= 3 and name.startswith("--") and (
            "--output".startswith(name) or name.startswith("--output")
        ):
            return False
    return True


def _v_branch(args: list[str]) -> bool:
    """git branch: listing forms only. A bare positional creates a branch and
    any -d/-D/-m/-M/-c/-C/-f/-u/... mutates, so only a closed set of flags and
    (with --list) patterns are accepted."""
    value_flags = (
        "--merged", "--no-merged", "--contains", "--no-contains",
        "--points-at", "--sort", "--format",
    )
    plain_flags = (
        "--all", "--remotes", "--verbose", "--show-current", "--ignore-case",
        "--color", "--no-color", "--column", "--no-column", "--abbrev",
        "--no-abbrev", "--list",
    )
    list_mode = False
    positional = 0
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a.startswith("--"):
            name, eq, _val = a.partition("=")
            if name in value_flags:
                if not eq and i < len(args) and not args[i].startswith("-"):
                    i += 1
                continue
            if name in plain_flags:
                if name == "--list":
                    list_mode = True
                continue
            return False
        if a.startswith("-") and len(a) > 1:
            if all(c in "arvi" for c in a[1:]):
                continue
            return False
        positional += 1
    return positional == 0 or list_mode


def _v_remote(args: list[str]) -> bool:
    if not args or args in (["-v"], ["--verbose"]):
        return True
    if args[0] == "show":
        return all(a == "-n" or not a.startswith("-") for a in args[1:])
    if args[0] == "get-url":
        return all(a in ("--push", "--all") or not a.startswith("-") for a in args[1:])
    return False


def _v_config_get(args: list[str]) -> bool:
    bad = (
        "--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e",
        "--rename-section", "--remove-section", "--set",
    )
    return not any(a.split("=", 1)[0] in bad for a in args)


def _v_find(args: list[str]) -> bool:
    bad = (
        "-delete", "-exec", "-execdir", "-ok", "-okdir",
        "-fprint", "-fprint0", "-fprintf", "-fls",
    )
    return not any(a in bad for a in args)


def _v_rg(args: list[str]) -> bool:
    return not any(a.split("=", 1)[0] in ("--pre", "--hostname-bin") for a in args)


_RO_VALIDATORS = {
    "git diff": _v_no_output,
    "git log": _v_no_output,
    "git show": _v_no_output,
    "git branch": _v_branch,
    "git remote": _v_remote,
    "git config --get": _v_config_get,
    "git config --get-all": _v_config_get,
    "git config --get-regexp": _v_config_get,
    "git config --get-urlmatch": _v_config_get,
    "git config --get-color": _v_config_get,
    "git config --get-colorbool": _v_config_get,
    "find": _v_find,
    "rg": _v_rg,
}


def _ro_prefix(seg: str):
    """The READ_ONLY_BASH_PREFIXES entry `seg` starts with, at a word boundary."""
    for prefix in READ_ONLY_BASH_PREFIXES:
        if seg == prefix or (seg.startswith(prefix) and seg[len(prefix)] in " \t"):
            return prefix
    return None


def _is_read_only_plain(seg: str) -> bool:
    prefix = _ro_prefix(seg)
    if prefix is None:
        return False
    check = _RO_VALIDATORS.get(prefix)
    if check is None:
        return True
    if _has_unsafe_expansion(seg):
        return False  # an expansion could smuggle a mutating flag past the check
    try:
        tokens = shlex.split(seg)
    except ValueError:
        return False
    words = prefix.split()
    if tokens[:len(words)] != words:
        return False
    return check(tokens[len(words):])


def _is_read_only_git_c(seg: str) -> bool:
    """True if `seg` is `git -C <path> <subcommand>` where <subcommand> is a
    read-only git subcommand (same argument checks as without -C).

    Extends the read-only bypass family to the `git -C <path> ...` shape so
    cross-tree read-only commands don't require an explicit allowed_commands
    entry or a `cd <path> && git status` workaround. The subcommand allowlist is
    derived from READ_ONLY_BASH_PREFIXES so future read-only additions get
    automatic `git -C` coverage. Non-git prefixes (`ls`, `pwd`, ...) are not
    eligible — they don't take `-C`.
    """
    m = re.match(r"""^git\s+-C\s+("[^"]*"|'[^']*'|\S+)\s+(.+)$""", seg)
    if not m:
        return False
    # The path must be a LITERAL: an unquoted `$(...)`, `${IFS}`, glob or brace
    # there is word-split / expanded by the shell into extra arguments (e.g.
    # `git -C $(cat<<<R${IFS}commit) status` runs `git -C R commit status`).
    path = m.group(1)
    if path[0] == "'":
        if len(path) < 2 or path[-1] != "'" or "'" in path[1:-1]:
            return False  # single-quoted and fully literal, or refuse
    elif path[0] == '"' and path[-1] == '"' and len(path) >= 2:
        if "$" in path or "`" in path or "\\" in path:
            return False
    elif _has_unsafe_expansion(path) or "'" in path or '"' in path:
        return False
    rest = "git " + m.group(2)
    return _is_read_only_plain(rest) or _is_read_only_git_c(rest)


def _is_read_only_segment(seg: str) -> bool:
    return _is_read_only_plain(seg) or _is_read_only_git_c(seg)


def _segment_class(seg: str, allowed_cmds: list[str]):
    """Why one segment is permitted: "navigation" (cd no-op), "read_only",
    "allowed_command" (an allowed_commands prefix) — or None (not permitted)."""
    if _is_cd_noop(seg):
        return "navigation"
    if _is_read_only_segment(seg):
        return "read_only"
    for ac in allowed_cmds:
        ac = str(ac)
        if ac.strip() and seg.startswith(ac):
            return "allowed_command"
    return None


def _first_bad_segment(segments: list[str], allowed_cmds: list[str]):
    for seg in segments:
        if _segment_class(seg, allowed_cmds) is None:
            return seg
    return None


def _strip_scalar(raw_val: str) -> str:
    """Strip surrounding quotes and a trailing inline comment from a YAML scalar.

    Conservative, mirroring the rest of this lightweight parser:
      - Quoted value (starts with ' or "):
          * fully quoted (opening quote also closes the string) -> the inner
            span, exactly as a plain `value[1:-1]` would (no behavior change);
          * quoted value followed by trailing content (an inline comment after
            the closing quote) -> the quoted span only; the trailing content is
            discarded. A '#' INSIDE the quotes is preserved.
          * unterminated opening quote -> returned as-is (never raises).
      - Unquoted value: a trailing ' #...' (a '#' preceded by whitespace, per
        the YAML rule) is an inline comment and is discarded; a '#' NOT preceded
        by whitespace (embedded in a token, e.g. C:\\foo#bar.md) is preserved.

    Without this, a trailing `# comment` is taken as part of the value:
    `worktree_bypass: true  # note` reads as the literal `true  # note` (not
    `true`, so the flag silently fails) and an allowed_paths entry with a
    comment fails the exact-match check with a spurious deny.
    """
    v = raw_val.strip()
    if not v:
        return v
    if v[0] in ('"', "'"):
        q = v[0]
        if len(v) >= 2 and v[-1] == q:
            return v[1:-1]  # fully quoted (no behavior change)
        end = v.find(q, 1)
        if end != -1:
            return v[1:end]  # quoted value + trailing inline comment
        return v  # unterminated quote -> leave literal (conservative)
    m = re.search(r"\s#", v)
    if m:
        v = v[:m.start()]
    return v.rstrip()


def parse_frontmatter(text: str) -> dict:
    """Tiny YAML subset: top-level scalars + list-of-strings under a key."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    result: dict = {}
    current_key: str | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if stripped.startswith("- ") and current_key is not None:
            result.setdefault(current_key, []).append(_strip_scalar(stripped[2:]))
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            value = _strip_scalar(value)
            if value == "" or value == "[]":
                # Empty scalar OR inline empty array -> a REAL empty list.
                # Without the "[]" case, `allowed_paths: []` mis-parses as the
                # scalar string "[]" and then iterates as 1-char paths
                # ['[', ']'] which can accidentally match a target.
                current_key = key
                result[key] = []
            else:
                result[key] = value
                current_key = None
    return result


def find_active_plans(worktree_root: Path | None = None) -> list[tuple[Path, dict]]:
    """Return active plans, optionally filtered by which sit under worktree_root.

    Per-worktree active invariant: "exactly one active plan per worktree" instead
    of "globally." This unblocks N concurrent agent sessions in different
    worktrees.

    When worktree_root is None (session outside any git worktree), returns all
    `status: active` plans — legacy global semantics.

    When worktree_root is set:
      - Plans with `worktree_bypass: true` are ALWAYS included (they deliberately
        work cross-tree, so they count against every worktree's single-active
        invariant).
      - Other plans are included only if at least one ABSOLUTE allowed_paths entry
        resolves under worktree_root (claim-by-absolute). Relative entries do NOT
        contribute to the claim check — they name file targets within the claimed
        worktree but cannot identify WHICH worktree the plan claims (relatives are
        anchored at the CALLING session's worktree, which would universally claim
        every caller). The rotation: absolute paths = worktree-anchor (location
        identity); relative paths = file identity within the claimed worktree.
        This aligns with Git's own model (the `.git` file carries an absolute
        `gitdir:` anchor).
      - A plan with no absolute allowed_paths and no `worktree_bypass: true`
        cannot claim any worktree; it is visible only to sessions outside all
        worktrees.
    """
    if PLANS_DIR is None or not PLANS_DIR.is_dir():
        return []
    actives: list[tuple[Path, dict]] = []
    wt_norm = (norm(str(worktree_root)) + "/") if worktree_root is not None else None
    for md in PLANS_DIR.glob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        status = str(fm.get("status", "")).strip().lower()
        if status != "active":
            continue
        if worktree_root is None:
            actives.append((md, fm))
            continue
        if str(fm.get("worktree_bypass", "")).strip().lower() == "true":
            actives.append((md, fm))
            continue
        allowed_paths = fm.get("allowed_paths") or []
        for ap in allowed_paths:
            if not Path(str(ap)).is_absolute():
                continue
            resolved = resolve_allowed_path(str(ap), repo_root=None, worktree_root=worktree_root)
            if norm(resolved).startswith(wt_norm):
                actives.append((md, fm))
                break
    return actives


def find_repo_and_worktree_roots(start: Path) -> tuple[Path | None, Path | None]:
    """Walk up from `start` to find the nearest `.git` file or directory.

    Returns (repo_root, worktree_root):
      - .git is a DIRECTORY at ancestor X:   (X, None)            — X is the main repo root.
      - .git is a FILE at ancestor X:         (main_repo, X)      — X is a worktree root.
      - no .git found:                        (None, None).

    A worktree's `.git` file contains `gitdir: <path>/.git/worktrees/<name>`. The
    main repo is the directory that path's `.git/worktrees/<name>` lives under.
    """
    try:
        cur = start.resolve()
    except OSError:
        return None, None
    while True:
        git_path = cur / ".git"
        if git_path.is_file():
            try:
                content = git_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return None, cur
            if content.startswith("gitdir:"):
                gitdir_str = content.split(":", 1)[1].strip()
                gitdir = Path(gitdir_str)
                # gitdir = <mainrepo>/.git/worktrees/<name>
                try:
                    main_repo = gitdir.parent.parent.parent
                    if main_repo.is_dir():
                        return main_repo, cur
                except Exception:
                    pass
            return None, cur
        if git_path.is_dir():
            return cur, None
        parent = cur.parent
        if parent == cur:
            return None, None
        cur = parent


def resolve_allowed_path(ap: str, repo_root: Path | None, worktree_root: Path | None) -> str:
    """Resolve an allowed_paths entry to an absolute path.

    - Absolute entries are returned as-is.
    - Relative entries are anchored at worktree_root if present, else repo_root.
      If neither is available, the entry is returned as-is (won't match any
      absolute target — effectively a no-op).
    """
    p = Path(ap)
    if p.is_absolute():
        return str(p)
    anchor = worktree_root or repo_root
    if anchor is None:
        return str(p)
    return str(anchor / p)


def validate_disjoint_paths(
    my_plan_path: Path,
    my_resolved_paths: list[str],
    global_actives: list[tuple[Path, dict]],
) -> tuple[bool, str]:
    """Disjoint-allowed_paths validation across globally-active plans.

    Layered on top of the per-worktree active-plan filter. The filter handles
    "one active plan per worktree" but does NOT catch the cross-worktree
    collision where two non-bypass plans in different worktrees both list an
    overlapping ABSOLUTE path (typically a shared file outside both worktrees, or
    a worktree-absolute path one session crosses into another's tree).

    For each OTHER active plan, collect its absolute allowed_paths (relatives are
    skipped — they can't be resolved without knowing the other plan's worktree,
    and treating them as "lives elsewhere" avoids false positives between two
    legitimate parallel sessions). Any exact-file match or directory-prefix
    relationship is a collision; the first collision wins and names both plans so
    the operator can choose which to archive or re-scope.

    Returns (ok, reason). ok=True → no overlap.
    """
    if not my_resolved_paths:
        return True, ""
    my_norm = [(norm(p), p) for p in my_resolved_paths]
    for other_path, other_fm in global_actives:
        if other_path == my_plan_path:
            continue
        other_abs: list[str] = []
        for ap in (other_fm.get("allowed_paths") or []):
            ap_p = Path(str(ap))
            if ap_p.is_absolute():
                other_abs.append(str(ap_p))
        if not other_abs:
            continue
        for op in other_abs:
            op_n = norm(op)
            for mp_n, mp_orig in my_norm:
                if op_n == mp_n:
                    return False, (
                        f"plan-gate: allowed_paths collision with active plan "
                        f"`{other_path.name}`: `{op}` overlaps your `{mp_orig}` "
                        f"(exact file). Two concurrent sessions both targeting "
                        f"this file would step on each other. Archive one plan "
                        f"or split the path so only one plan owns it."
                    )
                if op_n.startswith(mp_n + "/") or mp_n.startswith(op_n + "/"):
                    return False, (
                        f"plan-gate: allowed_paths collision with active plan "
                        f"`{other_path.name}`: `{op}` overlaps your `{mp_orig}` "
                        f"(directory-prefix relationship). Archive one plan "
                        f"or scope down so the directory is owned by exactly one."
                    )
    return True, ""


def _under_any(target_n: str, dirs: list[Path]) -> bool:
    """True if normalized target path sits under (or equals) any of `dirs`."""
    for d in dirs:
        d_n = norm(str(d))
        if target_n == d_n or target_n.startswith(d_n + "/"):
            return True
    return False


def _is_under_log_dir(target: str) -> bool:
    """True if `target` sits under PLAN_GATE_LOG_DIR. Checked in BOTH the raw and
    the fully RESOLVED forms (realpath on target and log dir): a `..`-laundered
    or symlinked target that the OS would open inside the log dir cannot slip a
    lexical-only comparison."""
    if LOGS_DIR is None:
        return False
    log_dirs = [LOGS_DIR]
    try:
        log_dirs.append(Path(os.path.realpath(str(LOGS_DIR))))
    except (OSError, ValueError):
        pass
    target_n = norm(str(target))
    try:
        target_resolved_n = norm(os.path.realpath(str(target)))
    except (OSError, ValueError):
        target_resolved_n = target_n
    return _under_any(target_n, log_dirs) or _under_any(target_resolved_n, log_dirs)


def _classify_redirect(target: str, has_cd: bool, what: str = "redirection target"):
    """Classify one write-redirection target of a Bash command, mirroring the
    Edit/Write sequence (log self-protection, then always-writable dirs, then
    the plan's allowed_paths). Returns (kind, value):
      ("null", None)    /dev/null (NUL on Windows) — never a real write
      ("error", msg)    cannot be resolved safely -> deny
      ("log", msg)      under PLAN_GATE_LOG_DIR -> deny (log_self_protect)
      ("always", None)  under an always-writable directory -> allowed
      ("plan", path)    absolute path that must match an allowed_paths entry
    A RELATIVE target resolves against the session CWD — but not after a
    `cd`/`pushd`/`popd` in the same command, whose destination is not known
    statically, so that case is an error (use an absolute path).
    """
    if target in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty",
                  "/dev/fd/0", "/dev/fd/1", "/dev/fd/2") or (
            os.name == "nt" and target.lower() == "nul"):
        return "null", None
    t = target
    if not os.path.isabs(t):
        if has_cd:
            return "error", (
                f"plan-gate: {what} {target!r} is relative, but the "
                f"command also changes directory, so where it lands cannot be "
                f"resolved statically. Use an absolute path."
            )
        try:
            base = os.getcwd()
        except OSError:
            return "error", (
                f"plan-gate: {what} {target!r} is relative and the "
                f"working directory cannot be determined."
            )
        t = os.path.join(base, t)
    t_raw = t
    t = os.path.normpath(t)
    # Check BOTH the raw and the lexically normalized form (each is also checked
    # resolved by _is_under_log_dir): normalizing first would erase a `..` that,
    # after a symlink in front of it, leads into the log directory.
    if _is_under_log_dir(t_raw) or _is_under_log_dir(t):
        return "log", (
            f"plan-gate: {what} {t} is under PLAN_GATE_LOG_DIR; the "
            f"gate never authorizes gated writes to its own decision log."
        )
    if _under_any(norm(t), ALWAYS_WRITABLE_DIRS):
        return "always", None
    return "plan", t


# --- writer scripts (a script whose job is to write a file) -----------------------
# `python3 atomic-write.py STATE.md` is a Bash command, and allowed_commands matches
# it by prefix, so without more it would let the script write ANYWHERE. A segment
# whose program is python/python3/py (or a path ending in one, `py -3`, python3.12,
# python.exe, behind env assignments / env / nohup / time / ...) running a script on
# the writer list is treated as a file write to the script's target argument, gated
# like a `>` redirect target: log self-protection, always-writable dirs, then
# allowed_paths. The segment must STILL be admitted by allowed_commands.
#
# Built-in list: atomic-write.py, target = its 1st (only) positional argument.
# Extend with PLAN_GATE_WRITER_SCRIPTS=name.py:argpos[,name2.py:argpos2...] where
# argpos is the 1-BASED position among the script's positional arguments (words that
# do not start with '-'; everything after a bare `--` is positional). A flag's value
# counts as a positional, so prefer the `--flag=value` form. A malformed value fails
# CLOSED (see _WRITER_CFG_ERROR). Script names match by lowercased basename.
_BUILTIN_WRITER_SCRIPTS = {"atomic-write.py": 1}


_MAX_ARGPOS = 64
_WRITER_NAME_RE = re.compile(r"^[^\s:;/\\,]+\.py$", re.IGNORECASE)


def _parse_writer_scripts(raw: str):
    """(writers, error). `writers` maps lowercased script basename -> 1-based
    positional index of its target; `error` is a message when `raw` is malformed
    (writers is then just the built-ins). A name must be a bare file name ending
    in .py (no ':', ';', whitespace or path separators), so a separator typo such
    as `a.py:1 b.py:2` or `a.py:2;b.py:1` is malformed rather than silently
    leaving a writer ungated."""
    writers = dict(_BUILTIN_WRITER_SCRIPTS)
    if not raw.strip():
        return writers, None
    extra: dict = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, pos = entry.rpartition(":")
        name = name.strip()
        pos = pos.strip()
        if not sep or not name:
            return dict(_BUILTIN_WRITER_SCRIPTS), (
                f"entry {entry[:80]!r} is not name.py:argpos"
            )
        if not _WRITER_NAME_RE.match(name):
            return dict(_BUILTIN_WRITER_SCRIPTS), (
                f"entry {entry[:80]!r}: the script must be a bare file name ending "
                f"in .py (no ':', ';', spaces or path separators; matched by "
                f"basename); entries are separated by ','"
            )
        if not re.match(r"^[0-9]{1,3}$", pos) or not (1 <= int(pos) <= _MAX_ARGPOS):
            return dict(_BUILTIN_WRITER_SCRIPTS), (
                f"entry {entry[:80]!r}: argpos must be an integer from 1 to "
                f"{_MAX_ARGPOS}; it is 1-based (1 = the script's first positional "
                f"argument)"
            )
        extra[name.lower()] = int(pos)
    writers.update(extra)
    return writers, None


def _load_writer_scripts():
    # Any fault while parsing the config fails CLOSED (it must not escape at
    # import time, outside the crash handler).
    try:
        return _parse_writer_scripts(os.environ.get("PLAN_GATE_WRITER_SCRIPTS", ""))
    except Exception as exc:  # pragma: no cover - defensive
        return dict(_BUILTIN_WRITER_SCRIPTS), f"could not parse ({type(exc).__name__})"


WRITER_SCRIPTS, _WRITER_CFG_ERROR = _load_writer_scripts()

# Interpreters: python, python3, python3.12, python3.13t, python3-dbg, pythonw(.exe),
# pypy, pypy3 (a basename; any directory prefix is ignored), and the py launcher.
_INTERP_RE = re.compile(
    r"^(?:(?:python|pythonw|pypy)[0-9.]*[a-z\-]*(?:\.exe)?|py(?:\.exe)?)$"
)
_PY_LAUNCHER_FLAG_RE = re.compile(r"^-(?:V:\S*|[0-9][0-9.a-z\-]*)$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers that may sit in front of the interpreter without changing what runs,
# with the options of each that take a separate value word.
_WRAPPER_VALUE_OPTS = {
    "env": ("-u", "-C", "-S", "-P", "--unset", "--chdir", "--split-string"),
    "time": ("-f", "-o", "--format", "--output"),
    "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"),
    "nice": ("-n", "--adjustment"),
    "timeout": ("-k", "-s", "--kill-after", "--signal"),
    "exec": ("-a",),
    "nohup": (),
    "command": (),
}
_PS_CD_WORDS = ("cd", "chdir", "sl", "set-location", "pushd", "popd")
# Interpreter short options that take a value (separate word, or the rest of the
# cluster), and long ones that take a separate value word.
_INTERP_VALUE_SHORT = "WXQ"
_INTERP_VALUE_LONG = ("--check-hash-based-pycs",)


def _basename_lc(tok: str) -> str:
    return tok.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _is_python_prog(tok: str) -> bool:
    return bool(_INTERP_RE.match(_basename_lc(tok)))


def _skip_wrappers(tokens: list[str]) -> int:
    """Index of the first token that is not a VAR=val, call operator, or a
    wrapper (with its options)."""
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if _ENV_ASSIGN_RE.match(t) or t in ("&", "."):
            i += 1
            continue
        w = _basename_lc(t)
        if w not in _WRAPPER_VALUE_OPTS:
            break
        i += 1
        valued = _WRAPPER_VALUE_OPTS[w]
        while i < n:
            o = tokens[i]
            if o == "--":
                i += 1
                break
            if o.startswith("-") and len(o) > 1:
                i += 2 if o in valued else 1
                continue
            if w == "env" and _ENV_ASSIGN_RE.match(o):
                i += 1
                continue
            break
        if w == "timeout" and i < n:
            i += 1  # the DURATION word
    return i


def _writer_invocation(tokens: list[str]):
    """If `tokens` (one simple command) runs a listed writer script (via a python
    interpreter, or by executing it directly), return (script_basename_lc,
    args_after_script); else None. `python -m mod`, `python -c code`, a non-listed
    script, or a program that is not python are all None (unaffected)."""
    n = len(tokens)
    i = _skip_wrappers(tokens)
    if i >= n:
        return None
    prog = tokens[i]
    if _basename_lc(prog) in WRITER_SCRIPTS:
        return _basename_lc(prog), tokens[i + 1:]  # direct execution
    if not _is_python_prog(prog):
        return None
    launcher = _basename_lc(prog).startswith("py") and not _basename_lc(prog).startswith(
        ("python", "pypy"))
    j = i + 1
    while j < n:
        t = tokens[j]
        if t == "-":
            return None  # script on stdin
        if t.startswith("--"):
            j += 2 if (t in _INTERP_VALUE_LONG) else 1
            continue
        if t.startswith("-"):
            if launcher and _PY_LAUNCHER_FLAG_RE.match(t):
                j += 1
                continue
            skip = 1
            for k, ch in enumerate(t[1:], start=1):
                if ch in "mc":
                    return None  # -m module / -c code: not a script file
                if ch in _INTERP_VALUE_SHORT:
                    skip = 1 if k < len(t) - 1 else 2  # value attached, or next word
                    break
            j += skip
            continue
        break
    if j >= n:
        return None
    base = _basename_lc(tokens[j])
    if base not in WRITER_SCRIPTS:
        return None
    return base, tokens[j + 1:]


def _writer_target(base: str, args: list[str]):
    """(True, target) or (False, reason) for one recognized writer invocation.
    Only ONE target per writer is checked (the argpos-th positional). So that an
    option value cannot shift the positionals, any option (a `-`-prefixed word
    with no `=`, other than a lone `--`) BEFORE the target denies: write options
    as --flag=value, or after the target."""
    argpos = WRITER_SCRIPTS[base]
    positionals: list[tuple[int, str]] = []
    after_dd = False
    for idx, a in enumerate(args):
        if not after_dd and a == "--":
            after_dd = True
            continue
        if not after_dd and a.startswith("-") and a != "-":
            if "=" not in a and len(positionals) < argpos:
                return False, (
                    f"{base} has option {a[:40]!r} before its target; an option "
                    f"may take a separate value word and so shift which argument "
                    f"is the target. Write options as --flag=value (or after the "
                    f"target)"
                )
            continue
        positionals.append((idx, a))
    if len(positionals) < argpos:
        return False, (
            f"{base} was run without its target (needs positional argument "
            f"#{argpos}, found {len(positionals)})"
        )
    idx, target = positionals[argpos - 1]
    # An expansion at or before the target could change which word lands in the
    # target slot (word splitting), or be the target itself: unresolvable.
    for a in args[:idx + 1]:
        if any(ch in a for ch in "$`*?[{"):
            return False, (
                f"{base} has a shell expansion, glob or brace in its arguments "
                f"({a[:60]!r}); where it writes cannot be resolved statically"
            )
    if not target.strip():
        return False, f"{base} has an empty target argument"
    if target.startswith("~"):
        target = os.path.expanduser(target)
    return True, target


def _tokenize_ps(cmd: str) -> list[list[str]]:
    """Split a PowerShell command into simple-command token lists. Minimal and
    conservative: honors '...' and "..." quoting including the doubled-quote
    escape ('' inside '...', "" inside "..."); a backtick inside "..." raises
    (PowerShell's escape character is not modeled); treats ; | & ( ) { } and
    newlines outside quotes as command separators. Raises ValueError on an
    unterminated quote or a backtick inside double quotes. Redirections (>) and
    Out-File / Set-Content style writes are NOT parsed."""
    segs: list[list[str]] = []
    cur: list[str] = []
    buf: list[str] = []
    have = False
    i, n = 0, len(cmd)

    def end_tok():
        nonlocal have
        if have:
            cur.append("".join(buf))
        del buf[:]
        have = False

    def end_seg():
        end_tok()
        if cur:
            segs.append(list(cur))
        del cur[:]

    while i < n:
        c = cmd[i]
        if c in "'\"":
            i += 1
            while True:
                j = cmd.find(c, i)
                if j == -1:
                    raise ValueError("unterminated quote")
                chunk = cmd[i:j]
                if c == '"' and "`" in chunk:
                    raise ValueError("backtick escape inside double quotes")
                buf.append(chunk)
                if cmd[j + 1:j + 2] == c:  # doubled quote = one literal quote
                    buf.append(c)
                    i = j + 2
                    continue
                i = j + 1
                break
            have = True
            continue
        if c in " \t":
            end_tok()
        elif c in ";|&(){}\n\r":
            end_seg()
        else:
            buf.append(c)
            have = True
        i += 1
    end_seg()
    return segs


# --- PowerShell segments ------------------------------------------------------
# Mirrors the Bash path: the command is split into simple commands and EACH must
# independently be read-only or start with an allowed_commands entry. Anything the
# splitter cannot place with confidence is refused (fail closed).
_PS_UNCHECKED_WRITERS = (
    "out-file", "set-content", "add-content", "new-item", "copy-item", "move-item",
    "tee-object", "export-csv", "export-clixml",
    "sc", "ac", "ni", "cpi", "cp", "copy", "mi", "mv", "move", "tee",
)


def _ps_segments(cmd: str):
    """(segments, problem). Segments are the raw simple-command texts, split on
    ; | || & && and newlines outside quotes (quotes, including the doubled-quote
    escape, are honored). A leading `&` (call operator) stays in its segment.
    `problem` is a message when the command contains something the gate cannot
    check: a backtick, $( or @( , a script block or parenthesis, a redirection
    (> >> 2> <), or an unterminated quote."""
    segs: list[str] = []
    start = 0
    i, n = 0, len(cmd)

    def push(end):
        t = cmd[start:end].strip()
        if t:
            segs.append(t)

    while i < n:
        c = cmd[i]
        if c in "'\"":
            i += 1
            while True:
                j = cmd.find(c, i)
                if j == -1:
                    return [], "unterminated quote"
                chunk = cmd[i:j]
                if "`" in chunk:
                    return [], "a backtick escape"
                if c == '"' and "$(" in chunk:
                    return [], "a $( ) subexpression inside double quotes"
                if cmd[j + 1:j + 2] == c:  # doubled quote = one literal quote
                    i = j + 2
                    continue
                i = j + 1
                break
            continue
        if c == "`":
            return [], "a backtick escape"
        if c in "(){}":
            return [], f"{c!r} (a subexpression, grouping or script block)"
        if c in "<>":
            return [], f"a redirection ({c!r})"
        if c in ";\n\r":
            push(i)
            start = i + 1
        elif c == "|":
            push(i)
            i += 2 if cmd[i + 1:i + 2] == "|" else 1
            start = i
            continue
        elif c == "&":
            nxt = cmd[i + 1:i + 2]
            if not cmd[start:i].strip() and nxt in (" ", "\t", "'", '"', "$"):
                i += 1  # call operator at the start of a command
                continue
            push(i)
            i += 2 if nxt == "&" else 1
            start = i
            continue
        i += 1
    push(n)
    return segs, None


def _ps_prefix_match(seg: str, prefix: str) -> bool:
    p = str(prefix).strip()
    if not p:
        return False
    if seg == p:
        return True
    return seg.startswith(p) and (p[-1] in " \t" or seg[len(p)] in " \t")


def _ps_read_only(seg: str) -> bool:
    return any(_ps_prefix_match(seg, pre) for pre in READ_ONLY_PS_PREFIXES)


def _ps_unchecked_writer(seg: str):
    """The cmdlet name if `seg` runs a file-writing cmdlet whose target the gate
    cannot check, else None."""
    try:
        segs = _tokenize_ps(seg)
    except ValueError:
        return None
    for toks in segs[:1]:
        words = [t for t in toks if t not in ("&", ".")]
        if words and words[0].lower() in _PS_UNCHECKED_WRITERS:
            return words[0]
    return None


def _extra_write_target_checks(target: str):
    """Hook point for additional per-target write checks. Returns a deny reason
    or None. Runs for every resolved writer-script target (and, when added, any
    other file-write target) AFTER log self-protection and BEFORE the allowed_paths
    check. The scope-lease holder check (spec section 3) plugs in here."""
    return None


def _collect_writer_targets(raw_segs: list[str], shell: str, has_cd: bool):
    """Find writer-script invocations among `raw_segs` (Bash segments, or for
    shell == "ps" the whole command) and classify each target like a redirect.
    Returns (plan_targets, problem): plan_targets is [(abs_target, script)] that
    must be in allowed_paths; problem is None or (deny_reason, log_class)."""
    plan_targets: list[tuple[str, str]] = []
    token_segs: list[list[str]] = []
    for raw in raw_segs:
        try:
            if shell == "ps":
                token_segs.extend(_tokenize_ps(raw))
            else:
                token_segs.append(shlex.split(raw))
        except ValueError:
            low = raw.lower()
            hit = [w for w in WRITER_SCRIPTS if w in low]
            if hit:
                return plan_targets, (
                    f"plan-gate: cannot tokenize a command that mentions writer "
                    f"script {hit[0]} ({raw[:120]!r}); where it writes cannot be "
                    f"resolved statically. Failing closed.",
                    "writer_script",
                )
    for toks in token_segs:
        if shell == "ps" and toks and toks[0].lower() in _PS_CD_WORDS:
            has_cd = True
    for toks in token_segs:
        inv = _writer_invocation(toks)
        if inv is None:
            continue
        base, args = inv
        ok, val = _writer_target(base, args)
        if not ok:
            return plan_targets, (f"plan-gate: writer script: {val}. Failing closed.",
                                  "writer_script")
        what = f"{base} write target"
        kind, v = _classify_redirect(val, has_cd, what)
        if kind == "error":
            return plan_targets, (v, "writer_script")
        if kind == "log":
            return plan_targets, (v, "log_self_protect")
        if kind in ("always", "plan"):
            abs_t = v if kind == "plan" else os.path.normpath(
                val if os.path.isabs(val) else os.path.join(os.getcwd(), val))
            extra = _extra_write_target_checks(abs_t)
            if extra:
                return plan_targets, (extra, "writer_script")
            if kind == "plan":
                plan_targets.append((v, base))
    return plan_targets, None


def _writer_cfg_deny_reason() -> str:
    return (
        f"plan-gate: PLAN_GATE_WRITER_SCRIPTS is malformed ({_WRITER_CFG_ERROR}). "
        f"Expected name.py:argpos[,name2.py:argpos2] with argpos the 1-based "
        f"position of the target among the script's positional arguments. "
        f"Failing closed: Bash/PowerShell commands that are not read-only are "
        f"denied until it is fixed. " + _BYPASS_HOWTO
    )


# What the top-level handler knows about the call in flight, so a gate_error row
# can say which tool/command was being decided when the fault hit.
_CALL_CTX: dict = {"tool": "", "tool_input": {}}

_BYPASS_HOWTO = (
    "To proceed anyway, set PLAN_GATE_BYPASS=on inline for that command (in the "
    "environment the hook runs in); every bypass use is logged as an "
    "emergency_bypass row."
)


def _fail_closed(what: str, detail: str) -> None:
    """The gate itself faulted (unparseable payload, unhandled exception): DENY.
    JSON on stdout + exit 0 per the hook contract — never exit 1, which the
    harness treats as a non-blocking warning (the action would proceed)."""
    reason = (
        f"plan-gate: INTERNAL ERROR — {what}: {detail[:300]}. The gate fails "
        f"CLOSED on its own faults, so this tool call is DENIED, not allowed. "
        f"{_BYPASS_HOWTO} The traceback (if any) is on stderr; please report it."
    )
    _log_decision(
        "deny", "gate", "gate_error", _CALL_CTX["tool"], _CALL_CTX["tool_input"],
        reason, None,
    )
    deny(reason)


def _handle_crash(exc: BaseException) -> None:
    """Top-level handler for an unhandled exception. Re-checks the master
    switch and PLAN_GATE_BYPASS BEFORE deciding, so the documented escape hatch
    works even when the fault is in the gate itself."""
    try:
        traceback.print_exc(file=sys.stderr)
    except Exception:
        pass
    if not _env_truthy("PLAN_GATE"):
        _allow_or_defer("plan-gate: PLAN_GATE is not on/1/true/yes — gate disabled.",
                        _CALL_CTX["tool"])
    if _env_truthy("PLAN_GATE_BYPASS"):
        reason = (f"plan-gate: PLAN_GATE_BYPASS={os.environ.get('PLAN_GATE_BYPASS', '').strip()} "
                  f"— emergency bypass (gate fault path).")
        _log_decision(
            "allow", "gate", "emergency_bypass", _CALL_CTX["tool"],
            _CALL_CTX["tool_input"], reason, None,
        )
        _allow_or_defer(reason, _CALL_CTX["tool"])
    _fail_closed("unhandled exception in the gate", f"{type(exc).__name__}: {exc}")


def main() -> None:
    # Test-only crash injection (see the test suite): proves the top-level
    # handler denies, honors PLAN_GATE_BYPASS, and still allows when the gate is
    # off, on a gate fault. "early" fires before anything else; "late" fires
    # after the payload is parsed. Fail-closed direction only — setting it can
    # make the gate deny, never allow.
    _inject = os.environ.get("_PLAN_GATE_TEST_CRASH", "").strip().lower()
    if _inject == "early":
        raise RuntimeError("injected test crash (early)")

    # Master gate switch. If off or unset, this hook is inert.
    if not _env_truthy("PLAN_GATE"):
        _allow_or_defer("plan-gate: PLAN_GATE is not on/1/true/yes — gate disabled.",
                        _peek_tool_name())

    # Emergency bypass. Logged (class=emergency_bypass) even in the deny-only
    # log mode — bypass usage is exception-class audit evidence. The payload
    # parse here is best-effort context ONLY: any failure still allows (the
    # bypass must never wedge), and an interactive TTY stdin is not consumed.
    if _env_truthy("PLAN_GATE_BYPASS"):
        b_tool, b_input = "", {}
        try:
            if sys.stdin is not None and not sys.stdin.isatty():
                b_payload = json.load(sys.stdin)
                b_tool = b_payload.get("tool_name", "") or ""
                b_input = b_payload.get("tool_input") or {}
        except Exception:
            pass
        b_reason = (f"plan-gate: PLAN_GATE_BYPASS={os.environ.get('PLAN_GATE_BYPASS', '').strip()} "
                    f"— emergency bypass.")
        _log_decision("allow", "gate", "emergency_bypass", b_tool, b_input, b_reason, None)
        _allow_or_defer(b_reason, b_tool if isinstance(b_tool, str) else "")

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(f"plan-gate: could not parse stdin JSON ({e}); denying.", file=sys.stderr)
        _fail_closed("could not parse the hook payload on stdin as JSON", str(e))

    if not isinstance(payload, dict):
        _fail_closed(
            "hook payload is not a JSON object",
            f"got {type(payload).__name__}",
        )
    tool = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        _fail_closed(
            "hook payload has an unexpected shape",
            f"tool_name is {type(tool).__name__}, tool_input is {type(tool_input).__name__}",
        )
    _CALL_CTX["tool"] = tool
    _CALL_CTX["tool_input"] = tool_input
    if _inject == "late":
        raise RuntimeError("injected test crash (late)")
    plan_name: str | None = None

    def _surface() -> str:
        if tool in ("Edit", "Write", "NotebookEdit"):
            return "file"
        if tool == "Bash":
            return "bash"
        if tool == "PowerShell":
            return "ps"
        if tool in MCP_FILE_WRITE_TOOLS or tool.startswith("mcp__"):
            return "mcp"
        return "gate"

    def gated_deny(reason: str, class_: str = "denied") -> None:
        """deny() + a unified-log row. Plan name is set after the active-plan
        check; denies before it record plan=""."""
        _log_decision("deny", _surface(), class_, tool, tool_input, reason, plan_name)
        deny(reason)

    def gated_allow(reason: str, class_: str) -> None:
        """allow() + a unified-log row (subject to PLAN_GATE_LOG_DECISIONS)."""
        _log_decision("allow", _surface(), class_, tool, tool_input, reason, plan_name)
        allow(reason)

    if tool not in ("Edit", "Write", "NotebookEdit", "Bash", "PowerShell") + MCP_FILE_WRITE_TOOLS:
        if tool.startswith("mcp__"):
            # An MCP tool the operator has not configured: not blocked (default-
            # denying unknown tools is a separate, backlogged item), but a row
            # is logged so every MCP call the hook sees is on the record.
            # NO decision (not "allow"): an allow would skip the harness's own
            # permission prompt for a tool this gate did not actually check.
            _log_decision("allow", "mcp", "mcp_unconfigured", tool, tool_input,
                          f"plan-gate: MCP tool {tool!r} is not in "
                          f"PLAN_GATE_MCP_WRITE_TOOLS.", None)
            no_decision()
        allow(f"plan-gate: tool {tool!r} is outside the gated set.")

    # === Bypasses BEFORE active-plan check ===
    # For Edit/Write/NotebookEdit: the always-writable bypass runs before the
    # active-plan check (bootstrap catch-22 fix — you must be able to write a
    # plan before a plan exists). For Bash/PowerShell: the read-only bypass also
    # runs first, so a session with no active plan can still inspect state. The
    # read-only commands are safe regardless of plan state.
    if tool in ("Edit", "Write", "NotebookEdit"):
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not target:
            gated_deny(
                f"plan-gate: no file_path in tool_input for {tool}.",
                class_="empty_input",
            )
        # Collapse `..` / `.` lexically so `<always-writable>/../x` cannot ride
        # the directory prefix out of it (same treatment as redirect targets).
        target_n = norm(os.path.normpath(str(target)))
        # Log self-protection — runs BEFORE the always-writable bypass and
        # before allowed_paths matching, so neither can shadow it: the gate
        # never authorizes a gated write to its own decision log (raw AND
        # resolved forms; see _is_under_log_dir).
        if _is_under_log_dir(str(target)):
            gated_deny(
                f"plan-gate: {target} is under PLAN_GATE_LOG_DIR; the gate "
                f"never authorizes gated writes to its own decision log.",
                class_="log_self_protect",
            )
        if _under_any(target_n, ALWAYS_WRITABLE_DIRS):
            gated_allow(
                "plan-gate: target is under an always-writable directory.",
                "always_writable",
            )

    if tool == "Bash":
        cmd = (tool_input.get("command") or "").strip()
        if not cmd:
            gated_deny(
                "plan-gate: Bash tool_input has no 'command'.",
                class_="empty_command",
            )
        # Parse the WHOLE command: segments (&& || ; | & newline), the bodies of
        # $(...) / `...` / <(...) / ( ... ) which execute, and write
        # redirections. Anything the scanner cannot confidently parse is denied.
        parsed, problems = _parse_command(cmd)
        if problems:
            gated_deny(
                "plan-gate: cannot safely parse this Bash command "
                f"({'; '.join(problems[:3])}). Failing closed. Rewrite it as "
                "simple commands (one per call), or enumerate it differently in "
                "the plan. Command: " + cmd[:200],
                class_="unparseable_command",
            )
        nodes = list(parsed.walk())
        all_segs = [sg for nd in nodes for sg in nd.segments]
        all_redirects = [rd for nd in nodes for rd in nd.redirects]
        if not all_segs and not all_redirects:
            gated_deny(
                "plan-gate: no executable command found in this Bash command "
                "after parsing. Failing closed. Command: " + cmd[:200],
                class_="unparseable_command",
            )
        has_cd = any(_is_cd_like(sg) for sg in all_segs)
        # Redirection-as-write: each target is a file write, gated like
        # Edit/Write (log self-protection -> always-writable -> allowed_paths).
        plan_targets: list[str] = []
        for rd in all_redirects:
            r_kind, r_val = _classify_redirect(rd, has_cd)
            if r_kind == "error":
                gated_deny(r_val)
            elif r_kind == "log":
                gated_deny(r_val, class_="log_self_protect")
            elif r_kind == "plan":
                plan_targets.append(r_val)
        # Writer scripts (python atomic-write.py TARGET ...): the target is a
        # file write, gated like a redirect target.
        if _WRITER_CFG_ERROR and _first_bad_segment(all_segs, []) is not None:
            gated_deny(_writer_cfg_deny_reason(), class_="writer_script")
        writer_plan_targets, w_problem = _collect_writer_targets(
            all_segs, "bash", has_cd)
        if w_problem:
            gated_deny(w_problem[0], class_=w_problem[1])
        # Pre-plan read-only bypass: a compound is read-only only if EVERY
        # segment is (a `cd` segment is a navigation no-op), and every write
        # redirection is /dev/null or lands in an always-writable directory.
        if not plan_targets and _first_bad_segment(all_segs, []) is None:
            if all_segs and all(_is_cd_noop(sg) for sg in all_segs):
                gated_allow("plan-gate: bare cd (navigation).", "navigation")
            if not all_segs:
                gated_allow(
                    "plan-gate: redirection-only command into an always-writable "
                    "directory.",
                    "always_writable",
                )
            gated_allow(
                f"plan-gate: read-only bash bypass ({len(all_segs)} segment(s), "
                f"all read-only).",
                "read_only_bypass",
            )

    if tool == "PowerShell":
        cmd = (tool_input.get("command") or "").strip()
        if not cmd:
            gated_deny(
                "plan-gate: PowerShell tool_input has no 'command'.",
                class_="empty_command",
            )
        # Split into simple commands (like the Bash path). Anything the splitter
        # cannot place is refused outright.
        ps_segs, ps_problem = _ps_segments(cmd)
        if ps_problem:
            gated_deny(
                f"plan-gate: cannot safely check this PowerShell command "
                f"({ps_problem}). Failing closed. Rewrite it as simple commands "
                f"(one per call, separated by ; | && || or newlines), without "
                f"subexpressions, script blocks, backticks or redirection. "
                f"Command: {cmd[:200]}",
                class_="unparseable_command",
            )
        if not ps_segs:
            gated_deny(
                "plan-gate: no executable command found in this PowerShell "
                "command after parsing. Failing closed.",
                class_="unparseable_command",
            )
        for sg in ps_segs:
            cmdlet = _ps_unchecked_writer(sg)
            if cmdlet:
                gated_deny(
                    f"plan-gate: PowerShell {cmdlet} writes a file whose target "
                    f"the gate cannot check; use Edit/Write (gated by "
                    f"allowed_paths) instead. Command: {cmd[:200]}",
                    class_="ps_unchecked_write",
                )
        # Read-only fast path: every segment is a known read-only command.
        if all(_ps_read_only(sg) for sg in ps_segs):
            gated_allow(
                f"plan-gate: read-only PS bypass ({len(ps_segs)} segment(s), all "
                f"read-only).",
                "read_only_bypass",
            )
        # Writer scripts (python atomic-write.py TARGET ...): the target is a
        # file write, gated like a redirect target.
        if _WRITER_CFG_ERROR:
            gated_deny(_writer_cfg_deny_reason(), class_="writer_script")
        ps_writer_plan_targets, w_problem = _collect_writer_targets(
            ps_segs, "ps", False)
        if w_problem:
            gated_deny(w_problem[0], class_=w_problem[1])

    mcp_plan_targets: list[str] = []
    if tool in MCP_FILE_WRITE_TOOLS:
        # Distinguish "empty payload" from "non-empty, also denied" in telemetry.
        # No read-only equivalent: every external-state write is mutating.
        if not tool_input:
            gated_deny(
                f"plan-gate: MCP tool {tool!r} has empty tool_input.",
                class_="empty_input",
            )
        if tool in MCP_TOOL_ARGS:
            # Tool with an argument spec: every target is checked exactly like
            # an Edit/Write file_path (log self-protection, always-writable,
            # then allowed_paths below). All must pass.
            mcp_targets, mcp_problem = _mcp_targets(tool, tool_input)
            if mcp_problem:
                gated_deny(
                    f"plan-gate: MCP tool {tool!r}: {mcp_problem} (configured "
                    f"path argument(s): {', '.join(MCP_TOOL_ARGS[tool])}). "
                    f"Failing closed.",
                    class_="mcp_target",
                )
            for mt in mcp_targets:
                if _is_under_log_dir(mt):
                    gated_deny(
                        f"plan-gate: {mt} is under PLAN_GATE_LOG_DIR; the gate "
                        f"never authorizes gated writes to its own decision log.",
                        class_="log_self_protect",
                    )
            for mt in mcp_targets:
                extra_reason = _extra_write_target_checks(mt)
                if extra_reason:
                    gated_deny(extra_reason, class_="mcp_target")
            mcp_plan_targets = [
                mt for mt in mcp_targets
                if not _under_any(norm(os.path.normpath(mt)), ALWAYS_WRITABLE_DIRS)
            ]
            if not mcp_plan_targets:
                gated_allow(
                    f"plan-gate: all {len(mcp_targets)} MCP target(s) are under "
                    f"an always-writable directory.",
                    "always_writable",
                )

    # === Session-CWD-aware roots ===
    # Hoisted above the active-plan check so find_active_plans() can filter by
    # worktree_root (per-worktree active invariant).
    try:
        cwd = Path(os.getcwd())
    except OSError:
        cwd = None
    repo_root, worktree_root = (None, None)
    if cwd is not None:
        repo_root, worktree_root = find_repo_and_worktree_roots(cwd)

    # === Active-plan check ===
    actives = find_active_plans(worktree_root=worktree_root)
    if not actives:
        if worktree_root is not None:
            # The REAL no-claim path: find_active_plans filtered every active
            # plan out because none claims this worktree. If active plans exist
            # elsewhere, say so — that is the silent-drift pattern.
            elsewhere = find_active_plans(worktree_root=None)
            if elsewhere:
                shown = ", ".join(p.name for p, _ in elsewhere[:5])
                if len(elsewhere) > 5:
                    shown += ", ..."
                gated_deny(
                    f"plan-gate: {len(elsewhere)} active plan(s) exist ({shown}), "
                    f"but none claims this worktree ({worktree_root}) — likely "
                    f"drift: the plan's allowed_paths point at another tree. A "
                    f"plan claims a worktree only through an ABSOLUTE "
                    f"allowed_paths entry under it (or `worktree_bypass: true` "
                    f"for deliberate cross-tree work). Add an absolute path "
                    f"under {worktree_root} to the plan you meant to use.",
                    class_="silent_drift",
                )
            gated_deny(
                "plan-gate: no plan file has `status: active` with allowed_paths "
                f"under worktree {worktree_root} in the plans directory. Write one "
                "(or add `worktree_bypass: true` for a cross-tree plan) and set "
                "status: active.",
                class_="no_active_plan",
            )
        gated_deny(
            "plan-gate: no plan file has `status: active` in the plans directory. "
            "Write one and set status: active.",
            class_="no_active_plan",
        )
    if len(actives) > 1:
        name_parts: list[str] = []
        bypass_count = 0
        for p, f in actives:
            if str(f.get("worktree_bypass", "")).strip().lower() == "true":
                name_parts.append(f"{p.name} (worktree_bypass: true)")
                bypass_count += 1
            else:
                name_parts.append(p.name)
        names = ", ".join(name_parts)
        scope_hint = f" under worktree {worktree_root}" if worktree_root is not None else ""
        bypass_hint = ""
        if bypass_count >= 1 and (len(actives) - bypass_count) >= 1:
            bypass_hint = (
                " Bypass plans are cross-tree exclusive — they participate in "
                "every worktree's single-active invariant by design."
            )
        gated_deny(
            f"plan-gate: multiple active plans{scope_hint} ({names}).{bypass_hint} "
            "Set all but one to `status: archived`.",
            class_="multiple_active_plans",
        )

    plan_path, fm = actives[0]
    plan_name = plan_path.name
    allowed_paths = fm.get("allowed_paths") or []
    allowed_cmds = fm.get("allowed_commands") or []

    resolved_paths: list[str] = []
    if allowed_paths:
        resolved_paths = [
            resolve_allowed_path(str(ap), repo_root, worktree_root)
            for ap in allowed_paths
        ]

    # Directory-entry warning. allowed_paths matches by EXACT equality, so a
    # directory entry does NOT authorize files inside it. A non-blocking stderr
    # warning surfaces the mistake at the first tool call rather than mid-work as
    # a confusing "not in allowed_paths" deny.
    for rp in resolved_paths:
        try:
            if os.path.isdir(rp):
                print(
                    f"plan-gate WARNING: allowed_paths entry {rp!r} is a "
                    f"directory; directory entries do NOT expand to children. "
                    f"List individual files inside.",
                    file=sys.stderr,
                )
        except OSError:
            pass

    # (The worktree "hard guard" that used to sit here was unreachable and has
    # been removed: find_active_plans() above already admits a plan for a
    # worktree session only if it has `worktree_bypass: true` or an ABSOLUTE
    # allowed_paths entry resolving under worktree_root, so any plan that
    # reaches this point already satisfies the guard's condition. The silent-
    # drift diagnostic now fires from the no-claim path above.)
    bypass = str(fm.get("worktree_bypass", "")).strip().lower() == "true"

    # === Disjoint paths check ===
    # Skipped when MY plan is `worktree_bypass: true` (bypass plans are cross-tree
    # exclusive — the multi-active deny above already fires if any other plan is
    # active globally).
    if not bypass:
        global_actives = find_active_plans(worktree_root=None)
        ok, reason = validate_disjoint_paths(plan_path, resolved_paths, global_actives)
        if not ok:
            gated_deny(reason, class_="disjoint_paths")

    if tool in ("Edit", "Write", "NotebookEdit"):
        # target / target_n already computed above (early-bypass block).
        for rp in resolved_paths:
            if norm(rp) == target_n:
                gated_allow(
                    f"plan-gate: {target} matches allowed_paths entry {rp!r}.",
                    "allowed_paths",
                )
        gated_deny(
            f"plan-gate: {target} is NOT in allowed_paths of active plan "
            f"({plan_path.name}). Update the plan or stop."
        )

    if tool == "Bash":
        # cmd / all_segs / plan_targets were computed in the pre-plan block
        # (entirely read-only commands were already allowed there). Here EVERY
        # segment — top level and substitution bodies — must independently be a
        # read-only command, a cd no-op, or start with an allowed_commands
        # entry, and every write-redirection target must be in allowed_paths.
        bad_seg = _first_bad_segment(all_segs, allowed_cmds)
        if bad_seg is not None:
            gated_deny(
                f"plan-gate: Bash command not allowed by active plan "
                f"({plan_path.name}): segment {bad_seg[:120]!r} matches neither "
                f"the read-only set, a bare cd, nor an allowed_commands prefix. "
                f"Compound commands (&& || ; | & newline, $(...), backticks, "
                f"<(...)) are checked segment by segment. Command: {cmd[:200]}"
            )
        for tgt in plan_targets:
            if not any(norm(rp) == norm(tgt) for rp in resolved_paths):
                gated_deny(
                    f"plan-gate: redirection target {tgt} is NOT in "
                    f"allowed_paths of active plan ({plan_path.name}). A "
                    f"redirection (>, >>, >|, &>) is a file write and is gated "
                    f"like Edit/Write. Command: {cmd[:200]}"
                )
        for tgt, script in writer_plan_targets:
            if not any(norm(rp) == norm(tgt) for rp in resolved_paths):
                gated_deny(
                    f"plan-gate: {script} target {tgt} is NOT in allowed_paths "
                    f"of active plan ({plan_path.name}). A writer script's "
                    f"target is a file write and is gated like Edit/Write. "
                    f"Command: {cmd[:200]}"
                )
        used_plan_cmd = any(
            _segment_class(sg, allowed_cmds) == "allowed_command" for sg in all_segs
        )
        gated_allow(
            f"plan-gate: all {len(all_segs)} segment(s) permitted"
            + (f"; {len(plan_targets)} redirection target(s) in allowed_paths"
               if plan_targets else "")
            + (f"; {len(writer_plan_targets)} writer-script target(s) in "
               f"allowed_paths" if writer_plan_targets else "")
            + ".",
            "allowed_command" if used_plan_cmd else "allowed_paths",
        )

    if tool == "PowerShell":
        # EVERY segment must be read-only or start (at a word boundary) with an
        # allowed_commands entry, exactly like the Bash path.
        used_ps_cmd = False
        for sg in ps_segs:
            if _ps_read_only(sg):
                continue
            if not any(_ps_prefix_match(sg, ac) for ac in allowed_cmds):
                gated_deny(
                    f"plan-gate: PowerShell command not allowed by active plan "
                    f"({plan_path.name}): segment {sg[:120]!r} is neither "
                    f"read-only nor starts with an allowed_commands entry. "
                    f"Compound commands (; | && || & newline) are checked segment "
                    f"by segment. Command: {cmd[:200]}"
                )
            used_ps_cmd = True
        for tgt, script in ps_writer_plan_targets:
            if not any(norm(rp) == norm(tgt) for rp in resolved_paths):
                gated_deny(
                    f"plan-gate: {script} target {tgt} is NOT in "
                    f"allowed_paths of active plan ({plan_path.name}). "
                    f"A writer script's target is a file write and is "
                    f"gated like Edit/Write. Command: {cmd[:200]}"
                )
        gated_allow(
            f"plan-gate: all {len(ps_segs)} PowerShell segment(s) permitted.",
            "allowed_command" if used_ps_cmd else "allowed_paths",
        )

    if tool in MCP_TOOL_ARGS:
        resolved_n = {norm(rp) for rp in resolved_paths}
        for mt in mcp_plan_targets:
            if norm(os.path.normpath(mt)) not in resolved_n:
                gated_deny(
                    f"plan-gate: MCP tool {tool!r} target {mt} is NOT in "
                    f"allowed_paths of active plan ({plan_path.name}). An MCP "
                    f"write tool's configured path arguments are checked like "
                    f"Edit/Write. Update the plan or stop."
                )
        gated_allow(
            f"plan-gate: all {len(mcp_plan_targets)} MCP target(s) of {tool!r} "
            f"match allowed_paths.",
            "allowed_paths",
        )

    if tool in MCP_FILE_WRITE_TOOLS:
        # Conservative default-DENY (a tool with no argument spec). No opt-in surface here; the premise is no
        # external-state writes during a gated unit of work. Telemetry falsifies
        # that premise — if legitimate usage appears, add an `allowed_mcp_tools`
        # opt-in surface (v2). The empty-input early-deny was applied above.
        gated_deny(
            f"plan-gate: MCP file-write tool {tool!r} is default-denied under "
            f"active plan ({plan_path.name}). Use a non-MCP write path or add an "
            f"opt-in surface."
        )


if __name__ == "__main__":
    # Argv mode: `plan-gate.py verify-log [path]` checks the decision-log hash
    # chain and exits 0 (intact) / 2 (damage or tampering). Hook mode (no args)
    # reads tool-use JSON from stdin as usual.
    if len(sys.argv) >= 2 and sys.argv[1] == "verify-log":
        sys.exit(verify_log(sys.argv[2] if len(sys.argv) > 2 else None))
    try:
        main()
    except SystemExit:
        raise
    except Exception as _exc:
        # Fail CLOSED: a gate fault denies (exit 0 + deny JSON); it never exits
        # 1, which the harness treats as a non-blocking warning. The handler
        # re-checks PLAN_GATE / PLAN_GATE_BYPASS first.
        try:
            _handle_crash(_exc)
        except SystemExit:
            raise
        except Exception:
            # Last resort: the handler itself faulted. Still deny.
            try:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "plan-gate: INTERNAL ERROR in the crash handler. The gate "
                        "fails CLOSED. " + _BYPASS_HOWTO
                    ),
                }}))
            except Exception:
                pass
            sys.exit(0)
