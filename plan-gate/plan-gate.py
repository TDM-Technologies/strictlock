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
  agent may only modify the exact files and run the exact commands an approved
  plan enumerates. Anything else is denied BEFORE it runs. The gate fails
  closed: when in doubt, deny.

HOOK CONTRACT (typical agentic-harness PreToolUse convention)
  - Exit 0 with JSON {"hookSpecificOutput": {"permissionDecision": "allow|deny", ...}}
  - Exit 1 + stderr  => non-blocking warning (fail-open for our OWN crashes only,
                        so a bug in the gate never wedges the operator).
  - Exit 2           => hard block (unused here; we express blocks as "deny" JSON).

CONFIGURATION (all via environment; no machine-specific defaults are baked in)
  PLAN_GATE                 "on" to enable; anything else makes the hook inert.
  PLAN_GATE_BYPASS          "on" is an emergency escape hatch.
  PLAN_GATE_PLANS_DIR       directory holding plan files (one active at a time).
  PLAN_GATE_ALWAYS_WRITABLE optional os.pathsep-separated list of directories
                            that are always writable regardless of plan state
                            (e.g. the plans dir itself, an agent-memory dir that
                            never ships). Same blast radius as plan files.
  PLAN_GATE_LOG_DIR         optional directory for the append-only decision log.
  PLAN_GATE_MCP_WRITE_TOOLS optional comma-separated list of MCP tool names that
                            modify external state (e.g. a cloud-drive create/copy
                            tool). Default-denied when set; see the MCP section.

RULES
  - If PLAN_GATE != "on" (case-insensitive), allow everything.
  - Exactly one plan file must have `status: active` in its YAML frontmatter
    (scoped per worktree — see find_active_plans).
  - Edit / Write / NotebookEdit:
      target file_path must EXACTLY match an entry in the plan's allowed_paths,
      OR sit under one of the PLAN_GATE_ALWAYS_WRITABLE directories.
  - Bash / PowerShell:
      command must start with an entry in allowed_commands,
      OR be a known read-only / navigational command (bypass lists below).
  - Bypass ordering: for Edit/Write the always-writable bypass runs BEFORE the
    active-plan check; for Bash/PowerShell the read-only bypass also runs BEFORE
    the active-plan check (so a session with no active plan can still inspect
    state with `git log`, `pwd`, `Get-ChildItem`, etc.). Plan-scoped checks run
    AFTER the active-plan check.
  - Every deny appends a JSONL row to the decision log. Best-effort; a logging
    failure NEVER blocks the gate.

PATH RESOLUTION
  - allowed_paths may be absolute OR repo-root-relative.
  - Relative entries resolve against the nearest repo root found by walking up
    from the session CWD. If CWD is under a git WORKTREE (.git is a file, not a
    dir), the WORKTREE ROOT is the anchor — not the main repo root. This is the
    fix for the silent-drift pattern where a worktree-CWD session wrote to the
    main repo because the plan listed main-repo absolute paths.
  - Hard guard: if CWD is under a worktree AND the plan has a non-empty
    allowed_paths AND zero of them resolve under that worktree, block every tool
    call. The plan is misaligned with the session location; fix the plan first.
  - Opt-out: a plan can declare `worktree_bypass: true` in frontmatter to skip
    the hard guard. Use when the work is deliberately cross-tree and the
    allowed_paths are absolute.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


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
DENY_LOG = (LOGS_DIR / "plan-gate-denies.log") if LOGS_DIR else None
PS_CALLS_LOG = (LOGS_DIR / "powershell-calls.log") if LOGS_DIR else None
MCP_FILE_WRITE_CALLS_LOG = (LOGS_DIR / "mcp-file-write-calls.log") if LOGS_DIR else None

# Commands that are always safe — investigation and navigation.
# `git config` is intentionally only allowed in `--get` form because the 2-arg
# form `git config <key> <value>` writes; require the explicit flag.
# `cd` is handled specially below: bare `cd <path>` bypasses; a compound
# `cd <path> [&&|||;] <rest>` is checked segment-wise against this list plus the
# plan's allowed_commands. A naive prefix match would let `cd anywhere && <rest>`
# slip the gate.
READ_ONLY_BASH_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git fetch",
    "git remote",
    "git config --get",
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
    "echo ",
    "cat ",
    "rg ",
    "grep ",
    "find ",
    "head ",
    "tail ",
    "wc ",
    "which ",
    "where ",
    "test ",
    "true",
    "false",
)

# Conservative PowerShell read-only bypass. PS commands that don't mutate state.
# NO compound parsing for PS: if you need `Get-Foo; Get-Bar`, either use Bash or
# list both shapes in the plan's allowed_commands explicitly. (PS has its own
# compound semantics — `;` chains, `&&`/`||` in PS7+, the `Set-Location` alias,
# here-strings, backtick escapes — that the Bash splitter below does not model.)
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
MCP_FILE_WRITE_TOOLS = tuple(
    t.strip()
    for t in os.environ.get("PLAN_GATE_MCP_WRITE_TOOLS", "").split(",")
    if t.strip()
)


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


def _log_deny(tool: str, tool_input: dict, reason: str, plan: str | None) -> None:
    """Append a JSONL row to the decision log. Best-effort; never raises.

    Lets a retro step surface deny patterns so a recurring block can be promoted
    into READ_ONLY_BASH_PREFIXES or a plan template. This append-only trail is
    also the SOC 2 CC7 / ISO 42001 monitoring evidence — produced as a byproduct
    of the gate doing its job, not as a separate audit feature.
    """
    if DENY_LOG is None:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        target = ""
        command = ""
        if tool in ("Edit", "Write", "NotebookEdit"):
            target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        elif tool in ("Bash", "PowerShell"):
            command = str(tool_input.get("command") or "")[:500]
        try:
            cwd_str = str(Path(os.getcwd()))
        except OSError:
            cwd_str = ""
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool,
            "command": command,
            "target": target,
            "reason": reason,
            "plan": plan or "",
            "cwd": cwd_str,
        }
        with open(DENY_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass  # Never let logging failure break the gate.


def _log_mcp_file_write_call(
    decision: str,
    bypass_class: str,
    tool_name: str,
    tool_input: dict,
    reason: str,
    plan: str | None,
) -> None:
    """Per-MCP-write-call telemetry. Best-effort; a log failure never blocks.

    Logs every gated MCP write tool call (allow OR deny) so a retro can decide
    whether the default-deny stance needs an opt-in surface. The summary records
    only which fields a call carried plus a short prefix of any text content —
    full payloads (especially file contents) are NOT logged.
    """
    if MCP_FILE_WRITE_CALLS_LOG is None:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        try:
            cwd_str = str(Path(os.getcwd()))
        except OSError:
            cwd_str = ""
        summary_fields = ("title", "parentId", "fileId", "mimeType", "newTitle")
        summary: dict = {}
        try:
            for key in summary_fields:
                if key in tool_input:
                    val = tool_input.get(key)
                    summary[key] = str(val)[:80] if val is not None else None
            summary["_keys"] = sorted(list(tool_input.keys()))[:20]
        except Exception:
            summary = {"_keys_error": True}
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": decision,
            "bypass_class": bypass_class,
            "tool": tool_name,
            "tool_input_summary": summary,
            "reason": reason[:300],
            "plan": plan or "",
            "cwd": cwd_str,
        }
        with open(MCP_FILE_WRITE_CALLS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never block on logging failure


def _log_ps_call(
    decision: str,
    bypass_class: str,
    cmd: str,
    reason: str,
    plan: str | None,
) -> None:
    """Per-PowerShell-call telemetry. Best-effort; a log failure never blocks.

    Gives a retro hard data on PS usage so the no-compound-parser decision can
    be revisited empirically. bypass_class is one of: read_only, allowed_command,
    denied, empty_command, matcher_no_active.
    """
    if PS_CALLS_LOG is None:
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        try:
            cwd_str = str(Path(os.getcwd()))
        except OSError:
            cwd_str = ""
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": decision,
            "bypass_class": bypass_class,
            "command_prefix": cmd[:200],
            "reason": reason[:300],
            "plan": plan or "",
            "cwd": cwd_str,
        }
        with open(PS_CALLS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never block on logging failure


def norm(p: str) -> str:
    return p.replace("\\", "/").lower().rstrip("/")


def _split_top_level(cmd: str) -> list[str]:
    """Split cmd on top-level &&/||/; separators, respecting quoted contexts.

    A separator is "top-level" only if it appears outside ALL of:
      - single-quoted strings ('...') — no escapes per POSIX shell
      - double-quoted strings ("...") — \\" escape supported
      - $(...) subshells — balanced paren depth, entered only via the $( token
      - heredoc bodies (<<TAG / <<-TAG / <<'TAG' / <<"TAG") — body runs from the
        next newline through the line that consists of just the tag

    The state machine is intentionally NOT a faithful shell parser. It does not
    track $(...) interpolation inside "..." or backtick substitution. The goal is
    to stop false-splitting on metacharacters obviously inside quoted contexts.
    Cases not covered (bare `(...)` subshells, backtick substitution) keep the
    over-splitting behavior, which fails toward a SAFE deny rather than an unsafe
    allow — fake segments simply get checked against allowed_commands and lose.

    Returns top-level segments with separator chars removed and whitespace
    trimmed. Empty/whitespace-only segments are filtered.
    """
    parts: list[str] = []
    buf: list[str] = []

    in_single = False
    in_double = False
    subshell_depth = 0
    heredoc_tag: str | None = None

    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]

        # Heredoc body: consume until a line consisting of just the tag.
        if heredoc_tag is not None:
            line_end = cmd.find("\n", i)
            if line_end == -1:
                buf.append(cmd[i:])
                i = n
                continue
            line = cmd[i:line_end]
            buf.append(cmd[i:line_end + 1])
            if line.strip() == heredoc_tag:
                heredoc_tag = None
            i = line_end + 1
            continue

        # Single-quoted string: no escapes; closes at the next '
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue

        # Double-quoted string: \" escape; closes at the next unescaped "
        if in_double:
            if ch == "\\" and i + 1 < n:
                buf.append(ch)
                buf.append(cmd[i + 1])
                i += 2
                continue
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        # Inside $(...): track paren depth; also recognize quote starts.
        if subshell_depth > 0:
            buf.append(ch)
            if ch == "(":
                subshell_depth += 1
            elif ch == ")":
                subshell_depth -= 1
            elif ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            i += 1
            continue

        # Top level — detect quote/subshell/heredoc starts and separators.

        # Heredoc start: <<TAG / <<-TAG / <<'TAG' / <<"TAG"
        if ch == "<" and i + 1 < n and cmd[i + 1] == "<":
            j = i + 2
            if j < n and cmd[j] == "-":
                j += 1
            while j < n and cmd[j] in " \t":
                j += 1
            quote_char = ""
            if j < n and cmd[j] in ("'", '"'):
                quote_char = cmd[j]
                j += 1
            tag_start = j
            if quote_char:
                while j < n and cmd[j] != quote_char:
                    j += 1
                tag = cmd[tag_start:j]
                if j < n:
                    j += 1  # consume closing quote
            else:
                while j < n and (cmd[j].isalnum() or cmd[j] == "_"):
                    j += 1
                tag = cmd[tag_start:j]
            buf.append(cmd[i:j])
            line_end = cmd.find("\n", j)
            if line_end == -1 or not tag:
                buf.append(cmd[j:])
                i = n
                continue
            buf.append(cmd[j:line_end + 1])
            heredoc_tag = tag
            i = line_end + 1
            continue

        # $(...) subshell start
        if ch == "$" and i + 1 < n and cmd[i + 1] == "(":
            buf.append("$(")
            subshell_depth = 1
            i += 2
            continue

        # Quote starts
        if ch == "'":
            buf.append(ch)
            in_single = True
            i += 1
            continue
        if ch == '"':
            buf.append(ch)
            in_double = True
            i += 1
            continue

        # 2-char separator: && or ||
        if i + 1 < n and cmd[i:i + 2] in ("&&", "||"):
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 2
            continue

        # 1-char separator: ;
        if ch == ";":
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    seg = "".join(buf).strip()
    if seg:
        parts.append(seg)
    return parts


def _has_compound_separator(cmd: str) -> bool:
    """True if cmd contains a top-level &&, ||, or ; separator (quote-aware).

    Used to decide whether `cd <path>` is bare navigation or a compound that
    needs segment-level checks.
    """
    return len(_split_top_level(cmd)) > 1


def _is_read_only_git_c(seg: str) -> bool:
    """True if `seg` is `git -C <path> <subcommand>` where <subcommand> is one of
    the read-only git subcommands from READ_ONLY_BASH_PREFIXES.

    Extends the read-only bypass family to the `git -C <path> ...` shape so
    cross-tree read-only commands don't require an explicit allowed_commands
    entry or a `cd <path> && git status` workaround. The subcommand allowlist is
    derived from READ_ONLY_BASH_PREFIXES so future read-only additions get
    automatic `git -C` coverage. Non-git prefixes (`ls`, `pwd`, ...) are not
    eligible — they don't take `-C`.
    """
    m = re.match(r"^git\s+-C\s+\S+\s+(.+)$", seg)
    if not m:
        return False
    rest = m.group(1)
    for prefix in READ_ONLY_BASH_PREFIXES:
        if not prefix.startswith("git "):
            continue
        sub = prefix[4:]  # strip "git "
        sub_stripped = sub.rstrip()
        if rest == sub_stripped or rest.startswith(sub):
            return True
    return False


def _check_cd_compound(cmd: str, allowed_cmds: list[str]) -> tuple[bool, str]:
    """Validate a `cd <path> [&& | || | ;] <rest>` compound (quote-aware).

    First segment must be `cd <path>`; each subsequent segment must independently
    match either READ_ONLY_BASH_PREFIXES or one of `allowed_cmds`. Additional
    `cd <path>` segments are accepted as navigation no-ops. Returns (allowed,
    reason).
    """
    parts = _split_top_level(cmd)
    if not parts or not parts[0].startswith("cd "):
        return False, "internal: _check_cd_compound called on non-cd command"
    if len(parts) == 1:
        return True, "bare cd (navigation)"
    for seg in parts[1:]:
        if seg.startswith("cd ") or seg == "cd":
            continue
        bypass_hit = False
        for prefix in READ_ONLY_BASH_PREFIXES:
            p = prefix.rstrip()
            if seg == p or seg.startswith(prefix):
                bypass_hit = True
                break
        if bypass_hit:
            continue
        if _is_read_only_git_c(seg):
            continue
        allowed_hit = any(seg.startswith(ac) for ac in allowed_cmds)
        if allowed_hit:
            continue
        return False, (
            f"cd-compound segment not allowed: {seg[:120]!r}. "
            f"Must match read-only bypass or active plan's allowed_commands."
        )
    return True, f"cd-compound allowed; {len(parts) - 1} non-cd segment(s) checked"


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


def main() -> None:
    # Master gate switch. If off or unset, this hook is inert.
    gate_env = os.environ.get("PLAN_GATE", "").strip().lower()
    if gate_env != "on":
        allow("plan-gate: PLAN_GATE != 'on' — gate disabled.")

    # Emergency bypass.
    if os.environ.get("PLAN_GATE_BYPASS", "").strip().lower() == "on":
        allow("plan-gate: PLAN_GATE_BYPASS=on — emergency bypass.")

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(f"plan-gate: could not parse stdin JSON ({e}); allowing.", file=sys.stderr)
        sys.exit(1)

    tool = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input") or {}
    plan_name: str | None = None

    def gated_deny(reason: str) -> None:
        """deny() + JSONL deny-log row. Plan name is set after active-plan check."""
        _log_deny(tool, tool_input, reason, plan_name)
        deny(reason)

    if tool not in ("Edit", "Write", "NotebookEdit", "Bash", "PowerShell") + MCP_FILE_WRITE_TOOLS:
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
            gated_deny(f"plan-gate: no file_path in tool_input for {tool}.")
        target_n = norm(str(target))
        if _under_any(target_n, ALWAYS_WRITABLE_DIRS):
            allow("plan-gate: target is under an always-writable directory.")

    if tool == "Bash":
        cmd = (tool_input.get("command") or "").strip()
        if not cmd:
            gated_deny("plan-gate: Bash tool_input has no 'command'.")
        for prefix in READ_ONLY_BASH_PREFIXES:
            p = prefix.rstrip()
            if cmd == p or cmd.startswith(prefix):
                allow(f"plan-gate: read-only bash bypass ({p!r}).")
        if _is_read_only_git_c(cmd):
            allow("plan-gate: read-only bash bypass (git -C <path> <subcmd>).")
        # Bare `cd <path>` (no compound) is pure navigation. A compound
        # `cd <path> [&&|||;] <rest>` requires allowed_cmds; deferred below.
        if cmd.startswith("cd ") and not _has_compound_separator(cmd):
            allow("plan-gate: bare cd (navigation).")

    if tool == "PowerShell":
        cmd = (tool_input.get("command") or "").strip()
        if not cmd:
            _log_ps_call("deny", "empty_command", "", "no command", plan_name)
            gated_deny("plan-gate: PowerShell tool_input has no 'command'.")
        for prefix in READ_ONLY_PS_PREFIXES:
            p = prefix.rstrip()
            if cmd == p or cmd.startswith(prefix):
                _log_ps_call("allow", "read_only", cmd, f"read-only PS bypass ({p!r})", plan_name)
                allow(f"plan-gate: read-only PS bypass ({p!r}).")
        # No bare-cd / Set-Location bypass for PS: the quote-aware Bash splitter
        # does not model PS compound semantics. Any PS compound needs an explicit
        # allowed_commands entry.

    if tool in MCP_FILE_WRITE_TOOLS:
        # Distinguish "empty payload" from "non-empty, also denied" in telemetry.
        # No read-only equivalent: every external-state write is mutating.
        if not tool_input:
            _log_mcp_file_write_call(
                "deny", "empty_input", tool, tool_input,
                "MCP file-write tool_input is empty.", plan_name,
            )
            gated_deny(f"plan-gate: MCP tool {tool!r} has empty tool_input.")

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
            gated_deny(
                "plan-gate: no plan file has `status: active` with allowed_paths "
                f"under worktree {worktree_root} in the plans directory. Write one "
                "(or add `worktree_bypass: true` for a cross-tree plan) and set "
                "status: active."
            )
        gated_deny(
            "plan-gate: no plan file has `status: active` in the plans directory. "
            "Write one and set status: active."
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
            "Set all but one to `status: archived`."
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

    # Hard guard — if CWD is under a worktree, at least one allowed_path must
    # resolve under that worktree. Catches the silent-drift pattern (allowed_paths
    # pointed at the main repo while CWD was the worktree). Opt-out: the plan
    # declares `worktree_bypass: true` for deliberately cross-tree work.
    bypass = str(fm.get("worktree_bypass", "")).strip().lower() == "true"
    if worktree_root is not None and allowed_paths and not bypass:
        wt_n = norm(str(worktree_root)) + "/"
        under_worktree = [p for p in resolved_paths if norm(p).startswith(wt_n)]
        if not under_worktree:
            gated_deny(
                f"plan-gate: session CWD {cwd} is under worktree {worktree_root}, "
                f"but zero allowed_paths resolve under it (plan: {plan_path.name}). "
                f"This matches the silent-drift pattern. Root allowed_paths at the "
                f"worktree or use repo-root-relative paths."
            )

    # === Disjoint paths check ===
    # Skipped when MY plan is `worktree_bypass: true` (bypass plans are cross-tree
    # exclusive — the multi-active deny above already fires if any other plan is
    # active globally).
    if not bypass:
        global_actives = find_active_plans(worktree_root=None)
        ok, reason = validate_disjoint_paths(plan_path, resolved_paths, global_actives)
        if not ok:
            gated_deny(reason)

    if tool in ("Edit", "Write", "NotebookEdit"):
        # target / target_n already computed above (early-bypass block).
        for rp in resolved_paths:
            if norm(rp) == target_n:
                allow(f"plan-gate: {target} matches allowed_paths entry {rp!r}.")
        gated_deny(
            f"plan-gate: {target} is NOT in allowed_paths of active plan "
            f"({plan_path.name}). Update the plan or stop."
        )

    if tool == "Bash":
        # cmd already computed above. Read-only bypass and bare `cd <path>` were
        # handled there. Here: the `cd <path> [&&|||;] <rest>` compound (needs
        # allowed_cmds) and the plain allowed_commands check.
        if cmd.startswith("cd ") and _has_compound_separator(cmd):
            ok, reason = _check_cd_compound(cmd, allowed_cmds)
            if ok:
                allow(f"plan-gate: {reason}")
            gated_deny(f"plan-gate: {reason}")
        for ac in allowed_cmds:
            if cmd.startswith(ac):
                allow(f"plan-gate: matches allowed_command prefix {ac!r}.")
        gated_deny(
            f"plan-gate: Bash command not allowed by active plan "
            f"({plan_path.name}). Command: {cmd[:200]}"
        )

    if tool == "PowerShell":
        # Conservative: startswith match against allowed_commands only. NO
        # compound parsing. The read-only PS bypass was applied in the early
        # branch.
        for ac in allowed_cmds:
            if cmd.startswith(ac):
                _log_ps_call("allow", "allowed_command", cmd,
                             f"matches allowed_command prefix {ac!r}", plan_name)
                allow(f"plan-gate: matches allowed_command prefix {ac!r}.")
        reason = (
            f"plan-gate: PowerShell command not allowed by active plan "
            f"({plan_path.name}). Command: {cmd[:200]}"
        )
        _log_ps_call("deny", "denied", cmd, reason, plan_name)
        gated_deny(reason)

    if tool in MCP_FILE_WRITE_TOOLS:
        # Conservative default-DENY. No opt-in surface here; the premise is no
        # external-state writes during a gated unit of work. Telemetry falsifies
        # that premise — if legitimate usage appears, add an `allowed_mcp_tools`
        # opt-in surface (v2). The empty-input early-deny was applied above.
        reason = (
            f"plan-gate: MCP file-write tool {tool!r} is default-denied under "
            f"active plan ({plan_path.name}). Use a non-MCP write path or add an "
            f"opt-in surface."
        )
        _log_mcp_file_write_call("deny", "denied", tool, tool_input, reason, plan_name)
        gated_deny(reason)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        print("plan-gate: unhandled exception; allowing. Trace:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
