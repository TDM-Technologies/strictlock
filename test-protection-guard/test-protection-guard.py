#!/usr/bin/env python3
"""test-protection-guard — an ADVISORY co-commit-coupling detector.

A StrictLock module (the Gates family). It catches the one test-gaming move that
a "born-weak" linter and a vocabulary-weakening differ both miss: an agent (or a
human) faced with a test that is failing *correctly* — because it caught a real
bug — edits the test's EXISTING assertion so it goes green, instead of fixing the
code. That is not an assertion weakened to a smoke test and not a new minimal
test; it is the same assertion shape with a different expected value
(`expect(x).toBe(5)` -> `...toBe(4)`).

The signal that DOES catch it is **co-commit coupling**: an existing test
assertion was changed/removed in the SAME commit as a change to non-test source.
This guard flags exactly that and asks for an inline (or, in PreToolUse mode,
commit-message) `TEST-CORRECTNESS:` justification.

ADVISORY BY DESIGN — the deliberate departure from the fail-CLOSED gates in this
suite (generated-sink-*-gate, commit-msg-gate):
  * It ALWAYS allows. It NEVER blocks. A false positive on a legitimate refactor
    must not wedge work — that friction is what trains reflexive token use and
    turns the token into noise.
  * It FAILS OPEN on its own infra error (git failure, parse error, crash) — the
    opposite of the integrity gates. An advisory nudge that blocked on its own
    bug would be worse than useless.
  * Its teeth are NOT in the gate. Both the hook and the self-administered token
    are bypassable by the very agent they constrain. The real enforcement is a
    human's review of the decision log at close-out (the guard's only job is to
    make the coupling VISIBLE and LOGGED so that review has something to read).
    The git-mode logic is one `git diff <range>` away from a CI check — the
    natural home for real teeth, since local hooks are bypassable.

DUAL-MODE — one script, one analysis core, two interchangeable I/O shells:

  * PreToolUse mode (agentic-harness, e.g. Claude Code): reads a tool-call JSON
    payload on stdin, acts only on a `git commit` Bash/PowerShell command, and
    emits an `allow` decision on stdout (plus a loud stderr warning when
    flagged). The advisory lands IN the agent's context so it can self-correct.

  * git pre-commit mode (any repo / any harness / humans too): git runs the hook
    in-tree with no stdin; the guard analyses the staged diff directly and warns
    on stderr, exit 0 always.

The mode is auto-detected (JSON-with-a-tool-call on stdin -> PreToolUse; empty
stdin -> pre-commit) and can be forced with TEST_PROTECTION_GUARD_MODE.

Everything is configured by environment variables (see the README CONFIG table);
the repo root is resolved with `git rev-parse --show-toplevel` — no
machine-specific or project-specific defaults are baked in.

Requirements: Python 3.8+ (standard library only).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# --- environment variable names (consistently TEST_PROTECTION_GUARD_*-prefixed) -----
ENV_ENABLE = "TEST_PROTECTION_GUARD"                    # "on" enables; else inert.
ENV_MODE = "TEST_PROTECTION_GUARD_MODE"                 # auto (default) | pretooluse | precommit
ENV_TEST_GLOBS = "TEST_PROTECTION_GUARD_TEST_GLOBS"     # os.pathsep globs identifying test files.
ENV_SOURCE_GLOBS = "TEST_PROTECTION_GUARD_SOURCE_GLOBS"  # os.pathsep globs identifying non-test source.
ENV_ASSERTION_RE = "TEST_PROTECTION_GUARD_ASSERTION_RE"  # regex for an assertion on a changed line.
ENV_TOKEN = "TEST_PROTECTION_GUARD_TOKEN"               # the justification token.
ENV_LOG_DIR = "TEST_PROTECTION_GUARD_LOG_DIR"           # dir for the append-only decision log.
ENV_LOG_FILE = "TEST_PROTECTION_GUARD_LOG"              # explicit log file path (overrides LOG_DIR; test seam).

DEFAULT_TOKEN = "TEST-CORRECTNESS:"

# Resolved once in main(); the top-level crash handler reads it to emit a
# mode-appropriate fail-open response (valid allow JSON for PreToolUse, exit 0
# for pre-commit) even if main() dies mid-flight.
_CURRENT_MODE = "precommit"

# Cross-language defaults. A glob WITHOUT a "/" is matched against the basename;
# a glob WITH a "/" is matched against the repo-relative path. All overridable.
DEFAULT_TEST_GLOBS = [
    "*.test.js", "*.test.jsx", "*.test.ts", "*.test.tsx", "*.test.mjs", "*.test.cjs",
    "*.spec.js", "*.spec.jsx", "*.spec.ts", "*.spec.tsx",
    "test_*.py", "*_test.py",
    "*_test.go", "*_test.rs", "*_spec.rb", "*Test.java", "*Tests.cs",
    "*__tests__/*",  # the JS/React `__tests__/foo.ts` directory convention (path glob).
]
DEFAULT_SOURCE_GLOBS = [
    "*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs", "*.mts", "*.cts",
    "*.py", "*.go", "*.rs", "*.rb", "*.java", "*.cs", "*.kt", "*.swift",
    "*.c", "*.cc", "*.cpp", "*.h", "*.hpp", "*.php", "*.scala",
]
# A removed/changed line carrying a recognisable assertion, across common stacks.
# Matched against the changed line's CODE (the leading '-' and indentation are
# stripped first), and each alternative requires assertion *syntax* — a call `(`,
# a method `.`, a `!` macro, or `assert` at statement start — so the bare English
# word "assert" in a comment or docstring does NOT match (a recall-over-precision
# heuristic; override it for an exotic stack — and keep the override linear, no
# nested quantifiers, since it runs on diff text without a timeout).
DEFAULT_ASSERTION_RE = (
    r"\bexpect\s*\("             # JS/Jest/Vitest/Chai: expect(...)
    r"|\bassert(?:_eq|_ne)?!"   # Rust: assert!/assert_eq!/assert_ne!
    r"|\bassert\w*\s*\("        # junit/unittest/xunit: assertEqual( / assertTrue( / assert(
    r"|\b(?:assert|require)\s*\."  # Go testify: assert.Equal / require.NoError
    r"|^assert\b"               # Python/pytest: `assert <expr>` at statement start
    r"|\brequire\s*\("          # Go testify: require(...)
    r"|\.should\b"              # Chai/RSpec: x.should...
)

# ------------------------------------------------------------------------------
# Mode + enablement
# ------------------------------------------------------------------------------
def _enabled() -> bool:
    return os.environ.get(ENV_ENABLE, "").strip().lower() == "on"


def _read_stdin() -> str:
    """Read stdin once. A pre-commit hook gets no stdin (empty); a PreToolUse
    call gets a JSON payload. Guard against a blocking read on an interactive tty."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
    except (ValueError, OSError):
        return ""
    try:
        return sys.stdin.read()
    except (OSError, ValueError):
        return ""


def _resolve_mode(raw: str) -> str:
    """'pretooluse' | 'precommit'. Env override wins; else detect from stdin: a
    JSON object with a tool-call shape => PreToolUse, anything else => pre-commit."""
    forced = os.environ.get(ENV_MODE, "").strip().lower()
    if forced in ("pretooluse", "pre-tool-use", "hook"):
        return "pretooluse"
    if forced in ("precommit", "pre-commit", "git"):
        return "precommit"
    s = raw.strip()
    if not s:
        return "precommit"
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return "precommit"
    if isinstance(obj, dict) and ("tool_name" in obj or "tool_input" in obj):
        return "pretooluse"
    return "precommit"


# ------------------------------------------------------------------------------
# git helpers (advisory => every failure returns None, the caller fails OPEN)
# ------------------------------------------------------------------------------
def _clean_git_env() -> dict:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return env


def _git(args, cwd):
    """Run git; return stdout, or None on any failure (advisory => fail open)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            env=_clean_git_env(),
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def _repo_root(cwd):
    out = _git(["rev-parse", "--show-toplevel"], cwd)
    if not out or not out.strip():
        return None
    return Path(out.strip())


# ------------------------------------------------------------------------------
# cd-prefix parsing (PreToolUse mode) — verbatim shape from the source guard
# ------------------------------------------------------------------------------
def _parse_cd_prefix(cmd):
    """Parse a leading `cd <path> [&&|||;] <rest>`; returns (cd_target|None, rest)."""
    if not cmd.startswith("cd "):
        return None, cmd
    m = re.compile(r"\s*(?:&&|\|\||;)\s*").search(cmd)
    if m is None:
        cd_segment, rest = cmd.strip(), ""
    else:
        cd_segment, rest = cmd[: m.start()].strip(), cmd[m.end():].strip()
    if not cd_segment.startswith("cd "):
        return None, cmd
    target = cd_segment[3:].strip()
    if not target:
        return None, cmd
    if len(target) >= 2 and target[0] == target[-1] and target[0] in ('"', "'"):
        target = target[1:-1]
    return target, rest


# git global options that take a following value, to skip when finding the subcommand.
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _is_git_commit(core_cmd):
    """True iff core_cmd invokes the `git commit` subcommand. Recognises env-assignment
    prefixes (`FOO=1 git commit`) and git global options (`git -C <dir> commit`,
    `git -c k=v commit`), and rejects same-prefix subcommands (`git commit-tree`,
    `git commitmsg`). Tokenisation failure falls back to a conservative prefix check."""
    try:
        import shlex
        toks = shlex.split(core_cmd, posix=(os.name != "nt"))
    except ValueError:
        return core_cmd == "git commit" or core_cmd.startswith("git commit ")
    i = 0
    while i < len(toks) and "=" in toks[i] and not toks[i].startswith("-"):
        i += 1  # leading VAR=value environment assignments
    if i >= len(toks) or toks[i] != "git":
        return False
    i += 1
    while i < len(toks):
        t = toks[i]
        if t in _GIT_OPTS_WITH_VALUE:
            i += 2  # option + its value
            continue
        if t.startswith("-"):
            i += 1  # `--opt=val`, `-c k=v` (joined), or a valueless flag
            continue
        return t == "commit"  # the first non-option token is the subcommand
    return False


# ------------------------------------------------------------------------------
# Classification + config
# ------------------------------------------------------------------------------
def _globs(env_name, default):
    raw = os.environ.get(env_name, "")
    items = [g.strip().replace("\\", "/") for g in raw.split(os.pathsep) if g.strip()]
    return items or list(default)


def _match_globs(path, globs):
    norm = path.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    for g in globs:
        target = norm if "/" in g else base
        if fnmatch.fnmatch(target, g):
            return True
    return False


def _is_test(path, test_globs):
    return _match_globs(path, test_globs)


def _is_source(path, test_globs, source_globs):
    return (not _is_test(path, test_globs)) and _match_globs(path, source_globs)


def _assertion_re():
    raw = os.environ.get(ENV_ASSERTION_RE, "").strip()
    pattern = raw or DEFAULT_ASSERTION_RE
    try:
        return re.compile(pattern)
    except re.error:
        # A misconfigured regex must not crash an advisory guard; fall back loudly.
        sys.stderr.write(
            f"test-protection-guard: {ENV_ASSERTION_RE} is not a valid regex; "
            f"using the built-in default.\n"
        )
        return re.compile(DEFAULT_ASSERTION_RE)


def _token():
    return os.environ.get(ENV_TOKEN, "").strip() or DEFAULT_TOKEN


# ------------------------------------------------------------------------------
# Logging (best-effort, append-only JSONL; never affects the decision)
# ------------------------------------------------------------------------------
def _log_path():
    explicit = os.environ.get(ENV_LOG_FILE, "").strip()
    if explicit:
        return Path(explicit)
    log_dir = os.environ.get(ENV_LOG_DIR, "").strip()
    if log_dir:
        return Path(log_dir) / "test-protection-guard.log"
    return None


def _log(row):
    p = _log_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            # Compact JSONL: smaller, and makes the documented grep patterns
            # (`'"decision":"flagged"'`) match exactly — no separator spaces.
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------------------
# The shared coupling-analysis core (mode-independent)
# ------------------------------------------------------------------------------
def analyze(root, mode, extra_token_haystack=""):
    """The brain. Returns a verdict dict:
        {"decision": "clean"|"flagged"|"justified", "reason": str, ...}
    'clean' = nothing to flag (no coupling, or test edits only ADD assertions).
    'flagged' = existing assertion changed + source staged, no justification token.
    'justified' = same coupling, but a TEST-CORRECTNESS token is present.
    Logs a row for flagged/justified only. Never raises for an expected git/IO
    failure — it returns a 'clean' verdict (fail open)."""
    test_globs = _globs(ENV_TEST_GLOBS, DEFAULT_TEST_GLOBS)
    source_globs = _globs(ENV_SOURCE_GLOBS, DEFAULT_SOURCE_GLOBS)

    out = _git(["diff", "--cached", "--name-only"], root)
    if out is None:
        return {"decision": "clean", "reason": "staged-file list unavailable — advisory, allowing."}
    staged = [p for p in out.splitlines() if p.strip()]
    test_files = [p for p in staged if _is_test(p, test_globs)]
    source_files = [p for p in staged if _is_source(p, test_globs, source_globs)]

    # No coupling possible unless BOTH a test and a non-test source file are staged.
    if not test_files or not source_files:
        return {"decision": "clean", "reason": "no test+source co-commit."}

    diff = _git(["diff", "--cached", "-U0", "--", *test_files], root)
    if diff is None:
        return {"decision": "clean", "reason": "staged test diff unavailable — advisory, allowing."}

    # An EXISTING assertion changed/removed = a '-' line (not the '---' header)
    # carrying an assertion. Pure '+' additions are new assertions (legitimate).
    # Match against the line's CODE (leading '-' + indentation stripped) so the
    # regex's syntax anchors (`^assert`) behave, and cap the scanned length as
    # cheap defence-in-depth against a pathological operator-supplied regex (the
    # built-in default is linear-safe; real assertion lines are far under the cap).
    assertion_re = _assertion_re()
    removed = []
    for ln in diff.splitlines():
        if not ln.startswith("-") or ln.startswith("---"):
            continue
        code = ln[1:].lstrip()[:4096]
        if assertion_re.search(code):
            removed.append(ln)
    if not removed:
        return {"decision": "clean", "reason": "test edits add assertions only (no change/removal)."}

    token = _token()
    token_present = (token in diff) or (bool(extra_token_haystack) and token in extra_token_haystack)
    decision = "justified" if token_present else "flagged"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "root": str(root),
        "test_files": test_files,
        "source_files": source_files,
        "removed_assertions": [ln.strip() for ln in removed[:10]],
        "token_present": token_present,
        "decision": decision,
    }
    _log(row)
    return {
        "decision": decision,
        "reason": (
            "existing assertion changed alongside source; a TEST-CORRECTNESS "
            "justification is present (logged)."
            if token_present else
            "existing assertion changed alongside source with no TEST-CORRECTNESS "
            "justification (FLAGGED, advisory; logged for close-out review)."
        ),
        "removed": removed,
        "test_files": test_files,
        "source_files": source_files,
    }


def _warning_text(verdict, token):
    removed = verdict.get("removed", [])
    body = "".join(f"    {ln.strip()}\n" for ln in removed[:5])
    return (
        "\n⚠ test-protection-guard: this commit changes/removes an EXISTING test "
        "assertion AND edits source in the same commit, with no "
        f"`{token}` justification.\n"
        "  Changed-out assertion(s):\n"
        + body
        + "  If you fixed the CODE (and the test was right), the test should not need "
        f"changing. If the TEST was genuinely wrong, add an inline `{token} <why>` "
        "comment (or, in an agentic harness, put it in the commit message).\n"
        "  This is ADVISORY (the commit proceeds) — it is logged for the close-out review.\n\n"
    )


# ------------------------------------------------------------------------------
# PreToolUse shell
# ------------------------------------------------------------------------------
def _emit_allow(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def run_pretooluse(raw):
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        _emit_allow("test-protection-guard: unparseable stdin — advisory, allowing.")
        return

    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        _emit_allow("test-protection-guard: not a Bash/PowerShell call.")

    # Coerce defensively: a structurally-valid payload may still carry a non-dict
    # tool_input or a non-string command — handle it as input, not as a crash.
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    raw_cmd = tool_input.get("command")
    cmd = (raw_cmd if isinstance(raw_cmd, str) else "").strip()
    cd_target, rest = _parse_cd_prefix(cmd)
    core_cmd = rest if cd_target is not None else cmd
    if not _is_git_commit(core_cmd):
        _emit_allow("test-protection-guard: not a `git commit`.")

    try:
        if cd_target is not None:
            target = Path(cd_target)
            if not target.is_absolute():
                target = Path(os.getcwd()) / target
            cwd = target.resolve()
        else:
            cwd = Path(os.getcwd()).resolve()
    except OSError:
        _emit_allow("test-protection-guard: CWD unresolved — advisory, allowing.")
        return

    root = _repo_root(cwd)
    if root is None:
        _emit_allow("test-protection-guard: not inside a git repo — advisory, allowing.")
        return

    # The command line itself is a valid token haystack in this mode (commit -m).
    verdict = analyze(root, "pretooluse", extra_token_haystack=cmd)
    if verdict["decision"] == "flagged":
        sys.stderr.write(_warning_text(verdict, _token()))
    _emit_allow(f"test-protection-guard: {verdict['reason']}")


# ------------------------------------------------------------------------------
# git pre-commit shell
# ------------------------------------------------------------------------------
def run_precommit():
    try:
        cwd = Path(os.getcwd()).resolve()
    except OSError:
        sys.exit(0)  # advisory: never block on our own inability to resolve cwd.
    root = _repo_root(cwd)
    if root is None:
        sys.exit(0)
    verdict = analyze(root, "precommit")  # no command line => inline token only.
    if verdict["decision"] == "flagged":
        sys.stderr.write(_warning_text(verdict, _token()))
    sys.exit(0)


# ------------------------------------------------------------------------------
# Entry
# ------------------------------------------------------------------------------
def main():
    global _CURRENT_MODE
    raw = _read_stdin()
    _CURRENT_MODE = _resolve_mode(raw)

    if not _enabled():
        if _CURRENT_MODE == "pretooluse":
            _emit_allow(f"test-protection-guard: {ENV_ENABLE} != 'on' — inert.")
        sys.exit(0)  # pre-commit: silent allow.

    if _CURRENT_MODE == "pretooluse":
        run_pretooluse(raw)
    else:
        run_precommit()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Advisory => fail OPEN on our own crash (the opposite of the fail-closed
        # gates). A nudge that wedges work on its own bug is worse than useless.
        traceback.print_exc(file=sys.stderr)
        try:
            # Best-effort: emit the mode-appropriate allow. PreToolUse expects valid
            # allow JSON on stdout; pre-commit just needs exit 0.
            if _CURRENT_MODE == "pretooluse":
                _emit_allow("test-protection-guard: unhandled exception — advisory, allowing.")
        except SystemExit:
            raise
        sys.exit(0)
