#!/usr/bin/env python3
"""spill-gate — a fail-closed pre-commit / pre-push gate against non-public spill.

A StrictLock module. It refuses to let private keys, API tokens, stray UUIDs and
machine-specific paths (a home directory with a user name in it) cross the commit
or push boundary — catching a leak BEFORE it is in a public history, not post-hoc
in CI.

Three modes (one single file, standard library only):

  pre-commit   scans the lines ADDED in the git INDEX (what is staged — not the
               working tree). Wire it as the `pre-commit` hook.
  pre-push     reads git's pre-push stdin and scans every commit that is about to
               be pushed, one commit at a time (so a secret added then removed in a
               later commit is still caught; merge commits are scanned for what
               the merge itself introduced). A brand-new branch with no remote
               base scans the commits not reachable from any ref of that remote.
  scan-tree    scans every tracked file of a revision (default HEAD) in full —
               for dogfooding / auditing a repo that already exists.

It is FAIL-CLOSED: a git failure, an unreadable or malformed allowlist, or any
internal error BLOCKS (exit 1). It is a regex gate: it REDUCES spill risk, it does
not eliminate it. "Is this content private?" stays a human judgment call.

The patterns are a superset of the `secret-scan` job in StrictLock's CI.

Install by copying this file to `.git/hooks/pre-commit` and/or `.git/hooks/pre-push`
(the mode is taken from the file name), or call it with an explicit mode.

Exit codes: 0 allow, 1 block, 2 usage error (also blocks when used as a hook).

Requirements: Python 3.8+ (standard library only).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

ENV_ENABLE = "SPILL_GATE"                  # on/1/true/yes enables the gate; else inert.
ENV_ALLOWLIST = "SPILL_GATE_ALLOWLIST"     # explicit allowlist file override (trusted, on disk).
ENV_LOG_DIR = "SPILL_GATE_LOG_DIR"         # optional dir for the append-only decision log.
ENV_BYPASS = "SPILL_GATE_BYPASS"           # pre-commit bypass (on/1/true/yes).
ENV_PUSH_BYPASS = "SPILL_GATE_PUSH_BYPASS"  # pre-push bypass - distinct on purpose.

ALLOWLIST_NAME = ".spill-gate-allow"
EXIT_ALLOW = 0
EXIT_BLOCK = 1
EXIT_USAGE = 2
ZERO_SHA = "0" * 40
_TRUTHY = ("on", "1", "true", "yes")


class ConfigError(Exception):
    """Bad allowlist / marker content: always a block, never a pass."""


# --- rules -------------------------------------------------------------------
# The first group mirrors CI's secret-scan job (private keys, AWS key id, GitHub
# token, UUID, machine path) and drops word boundaries exactly where CI has none,
# so a token glued to an identifier (`AWS_KEY_AKIA...`) is still caught. The rest
# are token shapes with a distinctive prefix + length, chosen for a low
# false-positive rate.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_USER = r"[A-Za-z0-9_.@-]*"
_SEP = r"(?:\\\\|\\|/)"
RULES = [
    ("private-key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH |PGP |DSA |ENCRYPTED )?PRIVATE KEY")),
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,})")),
    ("slack-token", re.compile(r"xox[abprs]-[0-9A-Za-z-]{10,}")),
    ("api-key-sk", re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)[0-9A-Za-z_-]{20,}")),
    ("stripe-live-key", re.compile(r"(?<![A-Za-z0-9])[sr]k_live_[0-9A-Za-z]{20,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("uuid", re.compile(_UUID)),
    # A machine path WITH its whole user segment: /Users/<name>, <Drive>:\Users[\<name>],
    # /home/<name>/. (So a `literal` for one user cannot exempt a longer name.)
    ("machine-path", re.compile(
        r"(?:/Users/[A-Za-z]" + _USER
        + r"|[A-Za-z]:" + _SEP + r"Users(?:" + _SEP + r"[^\\/\s\"'`<>]*)?"
        + r"|/home/[A-Za-z_]" + _USER + r"/)")),
]
RULE_NAMES = {n for n, _ in RULES}
# Exact marker: `spill-gate: allow` or `spill-gate: allow=rule[,rule]`, nothing glued after it.
_MARKER = re.compile(r"spill-gate: allow(?:=([A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*))?(?![A-Za-z0-9_=,-])")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# --- logging / exits ---------------------------------------------------------
def _log(repo_root, decision: str, reason: str) -> None:
    log_dir = os.environ.get(ENV_LOG_DIR, "").strip()
    if not log_dir:
        return
    try:
        import datetime
        import json as _json

        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "gate": "spill-gate",
            "decision": decision,
            "reason": reason,
            "repo_root": str(repo_root) if repo_root else None,
        }
        with (d / "spill-gate.log").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record) + "\n")
    except OSError:
        pass  # logging must never weaken the gate


def allow(repo_root, reason: str) -> None:
    print(f"spill-gate: ALLOW - {reason}", file=sys.stderr)
    _log(repo_root, "allow", reason)
    sys.exit(EXIT_ALLOW)


def bypass(repo_root, var: str) -> None:
    print(f"spill-gate: BYPASSED - {var} is on; gate skipped for this run "
          f"(logged only if {ENV_LOG_DIR} is set).", file=sys.stderr)
    _log(repo_root, "bypass", f"{var} explicit bypass")
    sys.exit(EXIT_ALLOW)


def block(repo_root, reason: str) -> None:
    print(f"spill-gate: BLOCKED - {reason}", file=sys.stderr)
    _log(repo_root, "block", reason)
    sys.exit(EXIT_BLOCK)


# --- git ---------------------------------------------------------------------
def _clean_git_env() -> dict:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return env


# User diff config must not change what we parse.
_GIT_NEUTRAL = [
    "-c", "core.quotepath=off", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.suppressBlankEmpty=false", "-c", "diff.interHunkContext=0",
]
# --text: never let a `-diff`/`binary` attribute (or a NUL heuristic) hide a file's content.
_DIFF_ARGS = [
    "-U0", "--inter-hunk-context=0", "--text", "--no-color", "--no-ext-diff", "--no-textconv",
    "--no-renames", "--diff-filter=ACMRTUX", "--src-prefix=a/", "--dst-prefix=b/",
]


def _git_raw(args, cwd, keep_index_env: bool = False, stdin: bytes = None):
    """Run git; return (returncode, stdout bytes). OS failure -> fail-closed."""
    try:
        r = subprocess.run(
            ["git", *_GIT_NEUTRAL, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            input=stdin,
            timeout=120,
            env=dict(os.environ) if keep_index_env else _clean_git_env(),
        )
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        block(cwd, f"`git {' '.join(args)}` failed ({e}) - fail-closed.")
        raise


def _git_ok(args, cwd, keep_index_env: bool = False, stdin: bytes = None) -> bytes:
    rc, out = _git_raw(args, cwd, keep_index_env, stdin)
    if rc != 0:
        block(cwd, f"`git {' '.join(args)}` exited {rc} - fail-closed.")
    return out


def resolve_repo_root() -> Path:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30, env=_clean_git_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        block(None, f"cannot run git ({e}) - fail-closed.")
        raise
    if r.returncode != 0 or not r.stdout.strip():
        block(None, "not inside a git repository (or git unavailable) - fail-closed.")
    return Path(r.stdout.strip())


# --- allowlist ---------------------------------------------------------------
def _glob_to_regex(glob: str):
    """gitignore-flavoured glob -> regex on repo-relative posix paths.
    `**` crosses '/', `*` and `?` do not, a trailing '/' means 'everything below',
    a pattern with no '/' matches the file name at any depth."""
    g = glob.strip().replace("\\", "/")
    if g.endswith("/"):
        g += "**"
    anchored = "/" in g
    i, out = 0, []
    while i < len(g):
        c = g[i]
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    if not anchored:
        body = "(?:.*/)?" + body
    return re.compile("^" + body + "$")


class Allowlist:
    """`path <glob> [rules=a,b]` | `literal <text>` | `# comment`."""

    def __init__(self):
        self.paths = []      # (regex, frozenset(rules) or None for all rules)
        self.literals = set()

    def path_allowed(self, path: str, rule: str) -> bool:
        for rx, rules in self.paths:
            if rx.match(path) and (rules is None or rule in rules):
                return True
        return False

    def literal_allowed(self, text: str) -> bool:
        return text in self.literals


def parse_allowlist(repo_root, text: str, label: str) -> Allowlist:
    al = Allowlist()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"allowlist {label} line {n}"
        if line.startswith("literal ") or line.startswith("literal\t"):
            lit = line[len("literal"):].strip()
            if not lit:
                block(repo_root, f"{where}: empty literal - fail-closed.")
            al.literals.add(lit)
            continue
        parts = line.split()
        if parts[0] == "path" and len(parts) in (2, 3):
            rules = None
            if len(parts) == 3:
                if not parts[2].startswith("rules="):
                    block(repo_root, f"{where}: expected rules=a,b, got {parts[2]!r} - fail-closed.")
                rules = frozenset(r for r in parts[2][6:].split(",") if r)
                unknown = rules - RULE_NAMES
                if not rules or unknown:
                    block(repo_root, f"{where}: unknown rule(s) {sorted(unknown) or '(none given)'} - fail-closed.")
            al.paths.append((_glob_to_regex(parts[1]), rules))
            continue
        block(repo_root, f"{where}: cannot parse {line!r} - fail-closed.")
    return al


def load_allowlist(repo_root: Path, spec: str) -> Allowlist:
    """The allowlist the gate honors comes from GIT, not the working tree:
    `spec` is the index entry (":0:.spill-gate-allow") or a commit's copy
    ("<sha>:.spill-gate-allow"). Absent there means no allowlist - an untracked or
    unstaged file on disk cannot loosen the gate. SPILL_GATE_ALLOWLIST is an explicit,
    trusted override that reads a file from disk instead."""
    override = os.environ.get(ENV_ALLOWLIST, "").strip()
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = repo_root / p
        if not p.exists():
            block(repo_root, f"allowlist {p} named by {ENV_ALLOWLIST} does not exist - fail-closed.")
        try:
            return parse_allowlist(repo_root, p.read_text(encoding="utf-8"), str(p))
        except (OSError, UnicodeDecodeError) as e:
            block(repo_root, f"cannot read allowlist {p} ({e}) - fail-closed.")
            raise
    rc, _ = _git_raw(["cat-file", "-e", spec], repo_root, keep_index_env=True)
    if rc != 0:
        return Allowlist()  # not in git -> no allowlist (the strict direction)
    raw = _git_ok(["show", spec], repo_root, keep_index_env=True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        block(repo_root, f"allowlist {spec} is not valid UTF-8 ({e}) - fail-closed.")
        raise
    return parse_allowlist(repo_root, text, spec)


# --- scanning ----------------------------------------------------------------
def scan_line(path: str, lineno: int, line: str, al: Allowlist, where: str = ""):
    """Return findings [(where, path, lineno, rule, matched)] for one line.

    A line holding NULs is matched in two forms - NULs deleted (finds ASCII inside
    UTF-16/UTF-32 data) and NULs turned into spaces (so a NUL cannot defeat a
    boundary such as the one in front of `sk-`). Findings are de-duplicated."""
    if "\0" in line:
        out, seen = [], set()
        for variant in (line.replace("\0", ""), line.replace("\0", " ")):
            for f in _scan_text(path, lineno, variant, al, where):
                if (f[3], f[4]) not in seen:
                    seen.add((f[3], f[4]))
                    out.append(f)
        return out
    return _scan_text(path, lineno, line, al, where)


def _scan_text(path: str, lineno: int, line: str, al: Allowlist, where: str):
    findings = []
    marker_rules = None   # None = no marker; empty set = all rules
    m = _MARKER.search(line)
    if m:
        marker_rules = set(r for r in (m.group(1) or "").split(",") if r)
        unknown = marker_rules - RULE_NAMES
        if unknown:
            raise ConfigError(f"{path}:{lineno}: spill-gate marker names unknown rule(s) {sorted(unknown)} - fail-closed.")
    for rule, rx in RULES:
        for hit in rx.finditer(line):
            if marker_rules is not None and (not marker_rules or rule in marker_rules):
                continue
            if al.path_allowed(path, rule):
                continue
            if al.literal_allowed(hit.group(0)) or al.literal_allowed(line.strip()):
                continue
            findings.append((where, path, lineno, rule, hit.group(0)))
    return findings


def _redact(s: str) -> str:
    return s[:4] + "..." + f" ({len(s)} chars)"


def scan_blob(path: str, data: bytes, al: Allowlist, where: str = ""):
    text = data.decode("utf-8", errors="replace")
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        out.extend(scan_line(path, i, line, al, where))
    return out


_HUNK = re.compile(r"^(@{2,}) .*? \+(\d+)(?:,\d+)? @{2,}")


def scan_diff(diff: bytes, al: Allowlist, where: str = ""):
    """Scan the added lines of `git diff -U0` / `git diff-tree -p --cc` output.

    Parsed by line shape, not by counts: hunk-body lines start with a space, '+' or
    '-' (`ncols` of them in a combined diff), while a header can only start with
    'd' (diff ...) or '@'. So context lines are tolerated, line numbers stay right,
    and a new `diff ` line always resets the file/hunk state."""
    findings = []
    path = None
    in_hunk = False
    ncols = 1
    new_line = 0
    for raw in diff.decode("utf-8", errors="replace").split("\n"):
        if raw.startswith("diff "):
            in_hunk = False
            path = None
            for pre in ("diff --cc ", "diff --combined "):
                if raw.startswith(pre):
                    path = raw[len(pre):].rstrip("\r")
            continue
        m = _HUNK.match(raw)
        if m and not (in_hunk and raw[:1] in ("+", "-", " ")):
            in_hunk = True
            ncols = len(m.group(1)) - 1
            new_line = int(m.group(2))
            continue
        if not in_hunk:
            if raw.startswith("+++ "):
                p = raw[4:].rstrip("\r")
                if p.endswith("\t"):
                    p = p[:-1]
                path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            continue
        if raw.startswith("\\"):
            continue
        prefix = raw[:ncols]
        if "-" in prefix:
            continue                     # removed: not a line of the result
        if prefix == "+" * ncols and len(raw) >= ncols and ncols > 0 and raw[:1] == "+":
            if path is not None:
                findings.extend(scan_line(path, new_line, raw[ncols:], al, where))
        new_line += 1
    return findings


def report(repo_root, findings, context: str) -> None:
    if not findings:
        allow(repo_root, f"{context}: nothing flagged.")
    print(f"spill-gate: {len(findings)} possible spill(s) in {context}:", file=sys.stderr)
    for where, path, lineno, rule, matched in findings[:200]:
        prefix = f"[{where}] " if where else ""
        print(f"  {prefix}{path}:{lineno}  {rule}  {_redact(matched)}", file=sys.stderr)
    if len(findings) > 200:
        print(f"  ... and {len(findings) - 200} more", file=sys.stderr)
    print(
        "spill-gate: if a hit is a deliberate, public-safe example, add `spill-gate: allow` "
        "to that line, or a `path`/`literal` entry to the allowlist (README).",
        file=sys.stderr,
    )
    block(repo_root, f"{context}: {len(findings)} finding(s).")


# --- modes -------------------------------------------------------------------
def mode_pre_commit(repo_root: Path) -> None:
    # The allowlist is the INDEX copy; the diff is the index (--cached), never the
    # working tree. --no-renames: a renamed file is re-scanned in full as an addition.
    al = load_allowlist(repo_root, ":0:" + ALLOWLIST_NAME)
    diff = _git_ok(["diff", "--cached", *_DIFF_ARGS], repo_root, keep_index_env=True)
    report(repo_root, scan_diff(diff, al), "staged changes")


def _commits_to_push(repo_root: Path, remote_name: str, local_sha: str, remote_sha: str):
    if remote_sha != ZERO_SHA:
        rc, out = _git_raw(["rev-list", "--reverse", local_sha, "--not", remote_sha], repo_root)
        if rc == 0:
            return out.decode().split()
        # remote tip unknown locally (diverged / force): fall through to the remote's known refs.
    spec = f"--remotes={remote_name}" if remote_name else "--remotes"
    return _git_ok(["rev-list", "--reverse", local_sha, "--not", spec], repo_root).decode().split()


def mode_pre_push(repo_root: Path, args) -> None:
    remote_name = args[0] if args else ""
    stdin = sys.stdin.read()
    seen, findings = set(), []
    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 4:
            block(repo_root, f"unparseable pre-push input line {line!r} - fail-closed.")
        _lref, local_sha, _rref, remote_sha = parts
        if local_sha == ZERO_SHA:
            continue  # branch deletion pushes no content
        for c in _commits_to_push(repo_root, remote_name, local_sha, remote_sha):
            if c in seen:
                continue
            seen.add(c)
            # Each commit is judged by the allowlist as it stands IN THAT COMMIT (absent =
            # none; a merge uses its own copy). Independent of which ref is listed first.
            al = load_allowlist(repo_root, f"{c}:{ALLOWLIST_NAME}")
            # --cc: a merge shows what the merge itself introduced (plain diff-tree -p is empty for merges).
            diff = _git_ok(
                ["diff-tree", "-p", "-r", "--root", "--cc", "--no-commit-id", *_DIFF_ARGS, c],
                repo_root,
            )
            findings.extend(scan_diff(diff, al, where=c[:8]))
    report(repo_root, findings, f"{len(seen)} commit(s) being pushed")


def mode_scan_tree(repo_root: Path, args) -> None:
    rev = "HEAD"
    if args:
        if len(args) == 2 and args[0] == "--rev":
            rev = args[1]
        else:
            print("usage: spill-gate.py scan-tree [--rev REV]", file=sys.stderr)
            sys.exit(EXIT_USAGE)
    al = load_allowlist(repo_root, f"{rev}:{ALLOWLIST_NAME}")
    entries = []   # (path, sha); submodules (mode 160000) have no blob here and are skipped
    for rec in _git_ok(["ls-tree", "-r", "-z", rev], repo_root).split(b"\0"):
        if not rec:
            continue
        meta, _, rawp = rec.partition(b"\t")
        mode, typ, sha = meta.decode().split()
        if mode == "160000" or typ != "blob":
            continue
        entries.append((rawp.decode("utf-8", errors="replace"), sha))
    findings = []
    for i in range(0, len(entries), 500):
        chunk = entries[i:i + 500]
        out = _git_ok(["cat-file", "--batch"], repo_root,
                      stdin="".join(s + "\n" for _, s in chunk).encode())
        pos = 0
        for path, sha in chunk:
            nl = out.index(b"\n", pos)
            hdr = out[pos:nl].decode().split()
            if len(hdr) != 3 or hdr[1] != "blob":
                block(repo_root, f"unexpected cat-file output for {path} - fail-closed.")
            size = int(hdr[2])
            data = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
            findings.extend(scan_blob(path, data, al))
    report(repo_root, findings, f"{len(entries)} file(s) at {rev}")


def main(argv) -> None:
    script = Path(argv[0]).name
    stem = script[:-3] if script.endswith(".py") else script
    if stem in ("pre-commit", "pre-push"):
        mode, args = stem, argv[1:]          # installed as a hook: mode from the file name
    elif len(argv) >= 2 and argv[1] in ("pre-commit", "pre-push", "scan-tree"):
        mode, args = argv[1], argv[2:]
    else:
        print("usage: spill-gate.py {pre-commit | pre-push [remote url] | scan-tree [--rev REV]}",
              file=sys.stderr)
        sys.exit(EXIT_USAGE)

    # scan-tree is an explicit manual audit: it runs even when the hook switch is off.
    if mode != "scan-tree" and not _env_truthy(ENV_ENABLE):
        sys.exit(EXIT_ALLOW)  # inert (allow-all) unless SPILL_GATE=on
    repo_root = resolve_repo_root()
    # Separate bypass per hook: bypassing the commit gate does not bypass the push backstop.
    bypass_var = {"pre-commit": ENV_BYPASS, "pre-push": ENV_PUSH_BYPASS}.get(mode)
    if bypass_var and _env_truthy(bypass_var):
        if mode == "pre-push":
            sys.stdin.read()
        bypass(repo_root, bypass_var)
    try:
        if mode == "pre-commit":
            mode_pre_commit(repo_root)
        elif mode == "pre-push":
            mode_pre_push(repo_root, args)
        else:
            mode_scan_tree(repo_root, args)
    except ConfigError as e:
        block(repo_root, str(e))


if __name__ == "__main__":
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except BaseException:  # fail closed on any internal error
        traceback.print_exc()
        print("spill-gate: BLOCKED - internal error - fail-closed.", file=sys.stderr)
        sys.exit(EXIT_BLOCK)
