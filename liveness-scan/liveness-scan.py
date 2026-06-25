#!/usr/bin/env python3
r"""liveness-scan.py — a read-only liveness reporter for a fleet of agent worktrees.

A StrictLock Concurrency / Meta-process module. When you run N autonomous agents in N git
worktrees, one supervision job no single agent can do for itself is *triage the fleet*: which
worktrees are actively working, which have gone quiet, which finished but never merged, and —
the dangerous one — which are AMBIGUOUSLY stalled (crashed, or just slow?). This scanner makes
that triage recurring and cheap, with **zero write-path exposure**.

It is a REPORTER, not a gate and not a reaper. It never edits a tracked file, never blocks a
tool call, registers no hook, and **exits 0 always** — a pure reporter must never break a
scheduled run or a session kickoff. Its whole job is to turn "re-derive the fleet's state by
hand" into a glance, so a human spends judgment on what the signal MEANS.

THE LOAD-BEARING INVARIANT: liveness is REPORT-ONLY, and an ambiguous stall ESCALATES (it is
written to the report) — the scanner NEVER auto-reaps a worktree. Killing a "dead" session that
was merely slow is unrecoverable data loss; the scanner refuses that call and hands it to a
person, with a copy-pasteable, NON-destructive inspect command (never `reset --hard`). It can
*under*-claim liveness (a running session it can't see classifies idle) but it never *over*-claims
death.

WHERE THE SIGNAL COMES FROM (two-tier, degrades gracefully):
  * a per-worktree HEARTBEAT file (LIVENESS_SCAN_HEARTBEAT_RELPATH), read by EXISTENCE + mtime
    only — the sharp signal, if your harness touches one (e.g. alongside a scope-lease acquire).
  * else COMMIT mtime — coarser, flagged in the reason. Always available.
  HEARTBEAT_TIMEOUT_MIN (the lease-fresh window) is distinct from CEILING_MIN (the whole-unit-of-
  work ceiling beyond which even fresh activity is suspect → escalate).

WHO OWNS A WORKTREE (attribution): if you run plan-gate, the scanner maps a worktree to the
session running in it via the active/executed plan whose **absolute** allowed_paths resolve under
that worktree root (the same enumeration plan-gate and scope-lease read). Plans are optional —
without them, attribution falls back to "branch ahead of base ⇒ done-unmerged" / "idle".

CLASSIFICATION (deterministic; report-only):
  running        an owning active plan + a fresh activity signal.
  stalled        an owning active plan, quiet past the heartbeat/commit window — likely slow; watch.
  ambiguous      an owning active plan idle past the whole-WP ceiling, or no activity signal at
                 all — crashed-or-slow unclear ⇒ ESCALATE (never reap).
  done-unmerged  work complete / branch ahead of base, not yet merged ⇒ pending merge.
  idle           no plan claims it; branch clean or merged.
  clean          the main / detached worktree — not a session worktree.

CONFIG (env). See CONFIG.md. All optional; sensible defaults; no machine-specific paths baked in.
  LIVENESS_SCAN_PLANS_DIR (or PLAN_GATE_PLANS_DIR)  ·  LIVENESS_SCAN_BASE (default origin/main)  ·
  LIVENESS_SCAN_HEARTBEAT_RELPATH  ·  LIVENESS_SCAN_HEARTBEAT_TIMEOUT_MIN  ·
  LIVENESS_SCAN_COMMIT_STALE_MIN  ·  LIVENESS_SCAN_CEILING_MIN  ·  LIVENESS_SCAN_LOG_DIR.

Modes:
  (default)   human report to stdout (+ the gitignored logs if LIVENESS_SCAN_LOG_DIR is set)
  --dry-run   compute + print the report; write NOTHING
  --json      structured digest (tooling / tests)
  --explain   maintainer breakdown to stderr (thresholds + per-worktree internals)

Usage:
  python3 liveness-scan.py            # scan + print the report
  python3 liveness-scan.py --dry-run  # scan, print, write nothing
  python3 liveness-scan.py --json     # structured

Always exits 0. Requirements: Python 3.8+ (standard library only) and `git` on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path, PurePath

# ── tuning defaults (overridable by env; no machine-specific paths) ──────────────────
DEFAULT_HEARTBEAT_TIMEOUT_MIN = 10    # heartbeat older than this (with a ~30s touch) => likely dead
DEFAULT_COMMIT_STALE_MIN = 90         # no-heartbeat fallback: commit mtime older than this => "quiet"
DEFAULT_CEILING_MIN = 480             # whole-WP ceiling (8h); idle past this => ambiguous => ESCALATE
DEFAULT_BASE = "origin/main"
NO_HEARTBEAT_NOTE = "no heartbeat file — activity inferred from commit mtime"


# ── env helpers ──────────────────────────────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        print(f"liveness-scan: {name}={raw!r} is not an integer; using default {default}.",
              file=sys.stderr)
        return default
    return v if v > 0 else default


def _plans_dir() -> Path | None:
    """The plan-gate plans directory: LIVENESS_SCAN_PLANS_DIR, then PLAN_GATE_PLANS_DIR (so an
    adopter already running plan-gate / scope-lease needs no extra config). None = no plan
    attribution (graceful degrade to ahead-of-base / idle)."""
    for var in ("LIVENESS_SCAN_PLANS_DIR", "PLAN_GATE_PLANS_DIR"):
        val = os.environ.get(var, "").strip()
        if val:
            return Path(val).expanduser()
    return None


def _log_dir() -> Path | None:
    val = os.environ.get("LIVENESS_SCAN_LOG_DIR", "").strip()
    return Path(val).expanduser() if val else None


def _heartbeat_relpath() -> str | None:
    val = os.environ.get("LIVENESS_SCAN_HEARTBEAT_RELPATH", "").strip()
    return val or None


def _base_ref() -> str:
    return os.environ.get("LIVENESS_SCAN_BASE", "").strip() or DEFAULT_BASE


# ── frontmatter parser (byte-0 anchored; same discipline as plan-gate / scope-lease) ─
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SCALAR_RE_CACHE: dict[str, re.Pattern] = {}


def _strip_scalar(raw_val: str) -> str:
    """Strip surrounding quotes + a trailing inline comment from a YAML scalar — same conservative
    rules as plan-gate / scope-lease, so what this scanner treats as 'active' matches what the gate
    and the lease do. A `#` preceded by whitespace ends an unquoted value; a `#` embedded in a token
    or inside quotes is preserved."""
    v = raw_val.strip()
    if not v:
        return v
    if v[0] in ('"', "'"):
        q = v[0]
        if len(v) >= 2 and v[-1] == q:
            return v[1:-1]
        end = v.find(q, 1)
        if end != -1:
            return v[1:end]
        return v
    m = re.search(r"\s#", v)
    if m:
        v = v[:m.start()]
    return v.rstrip()


def parse_frontmatter(text: str) -> str | None:
    """Return the YAML frontmatter body iff a `---\\n...\\n---\\n` block starts at BYTE 0, else
    None. A `status: active` inside prose or a fenced ```yaml example is INERT (never parsed) — the
    structural fix for a naive `^status:` grep's false positives."""
    m = _FM_RE.match(text)
    return m.group(1) if m else None


def fm_scalar(body: str, key: str) -> str | None:
    pat = _SCALAR_RE_CACHE.get(key)
    if pat is None:
        pat = re.compile(rf"^{re.escape(key)}:\s*(.+?)\s*$", re.MULTILINE)
        _SCALAR_RE_CACHE[key] = pat
    m = pat.search(body)
    return _strip_scalar(m.group(1)) if m else None


def fm_list(body: str, key: str) -> list[str]:
    """Parse a simple block YAML list (`key:` then `  - item` lines, trailing `# comment` stripped).
    Stops at the first line that is neither blank/comment nor a `-` list item. Sufficient for plan
    `allowed_paths` (no nested/flow sequences)."""
    lines = body.splitlines()
    out: list[str] = []
    in_list = False
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*(.*)$")
    for line in lines:
        if not in_list:
            m = key_re.match(line)
            if m and m.group(2).strip() == "":      # `key:` with the list on following lines
                in_list = True
            continue
        s = line.strip()
        if s == "" or s.startswith("#"):
            continue                                  # blank / comment INSIDE the list — skip
        m = re.match(r"^(\s*)-\s+(.*)$", line)
        if not m:
            break                                     # dedented to a new key — list ended
        out.append(_strip_scalar(m.group(2)))
    return out


# ── plan model ───────────────────────────────────────────────────────────────────────
class Plan:
    __slots__ = ("name", "status", "scope", "worktree_bypass", "allowed_paths")

    def __init__(self, name, status, scope, worktree_bypass, allowed_paths):
        self.name = name
        self.status = status
        self.scope = scope
        self.worktree_bypass = worktree_bypass
        self.allowed_paths = allowed_paths


def load_plans(plans_dir: Path | None) -> list[Plan]:
    """All plans with a byte-0 frontmatter block. No status filter here — callers filter."""
    out: list[Plan] = []
    if plans_dir is None or not plans_dir.is_dir():
        return out
    for f in sorted(plans_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = parse_frontmatter(text)
        if body is None:
            continue
        out.append(Plan(
            name=f.name,
            status=(fm_scalar(body, "status") or "").lower(),
            scope=(fm_scalar(body, "scope") or "").lower(),
            worktree_bypass=(fm_scalar(body, "worktree_bypass") or "").lower() == "true",
            allowed_paths=fm_list(body, "allowed_paths"),
        ))
    return out


def _norm(p: str) -> str:
    """Normalize a path for prefix comparison: absolute-ish, lowercased, forward slashes, no
    trailing slash. (Attribution is a coarse 'is this path under that worktree' check, so a
    lowercase fold is acceptable here and harmless on case-sensitive FS for this purpose.)"""
    try:
        s = str(PurePath(p))
    except (TypeError, ValueError):
        s = str(p)
    return s.replace("\\", "/").rstrip("/").lower()


def _is_abs(p: str) -> bool:
    return p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", p) is not None


def plan_specifically_claims(plan: Plan, worktree_root: str) -> bool:
    """Liveness ATTRIBUTION: a plan specifically OWNS a worktree iff >=1 ABSOLUTE allowed_paths
    entry resolves under it. **Excludes worktree_bypass** on purpose — a cross-tree bypass plan
    has authority in many worktrees but is the session of NONE, so attributing it per-worktree
    would mis-mark EVERY worktree as that plan's session.

    KNOWN BLIND SPOT (fail-safe): a running session whose active plan is worktree_bypass:true with
    no absolute path under its own worktree is invisible here → classifies idle/done-unmerged. That
    FALSE-NEGATIVE under-claims liveness (never over-claims death), so it is safe for a report-only
    scanner; it would need revisiting before any destructive consumer."""
    wt = _norm(worktree_root)
    for entry in plan.allowed_paths:
        if _is_abs(entry) and _norm(entry).startswith(wt + "/"):
            return True
    return False


# ── git probe (read-only; injectable for tests) ──────────────────────────────────────
class GitProbe:
    """Thin read-only git accessor. Pure reads only (worktree list, commit time, ancestry). A
    test substitutes a fake to drive classify() without real git/subprocess."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def _run(self, args: list[str]) -> str:
        try:
            cp = subprocess.run(
                ["git", "-C", self.repo_root, *args],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return cp.stdout if cp.returncode == 0 else ""

    def worktrees(self) -> list[dict]:
        """[{path, head, branch}] from `git worktree list --porcelain`."""
        out: list[dict] = []
        cur: dict = {}
        for line in self._run(["worktree", "list", "--porcelain"]).splitlines():
            if line.startswith("worktree "):
                if cur:
                    out.append(cur)
                cur = {"path": line[len("worktree "):].strip(), "head": "", "branch": ""}
            elif line.startswith("HEAD "):
                cur["head"] = line[len("HEAD "):].strip()
            elif line.startswith("branch "):
                cur["branch"] = line[len("branch "):].strip().replace("refs/heads/", "")
            elif line.strip() == "" and cur:
                out.append(cur)
                cur = {}
        if cur:
            out.append(cur)
        return out

    def commit_unixtime(self, sha: str) -> int | None:
        if not sha:
            return None
        s = self._run(["show", "-s", "--format=%ct", sha]).strip()
        return int(s) if s.isdigit() else None

    def ahead_of_base(self, sha: str, base: str) -> bool | None:
        """True iff sha has commit(s) not reachable from base (i.e. unmerged). None if unknown
        (e.g. base ref absent)."""
        if not sha:
            return None
        s = self._run(["rev-list", "--count", f"{base}..{sha}"]).strip()
        return (int(s) > 0) if s.isdigit() else None


# ── classification (deterministic core; report-only) ─────────────────────────────────
def _age_min(now: datetime, unixtime: int | None) -> float | None:
    if unixtime is None:
        return None
    return max(0.0, (now - datetime.fromtimestamp(unixtime, tz=timezone.utc)).total_seconds() / 60.0)


def _recovery_command(path: str) -> str:
    """A copy-pasteable, NON-destructive inspect command for a human (the dead-man's-switch).
    Never `reset --hard`, never `reap`; never executed here."""
    p = path.replace("\\", "/")
    return (f"cd '{p}' && git status && git stash list && git log --oneline -5   "
            "# inspect first; destructive recovery (reset/reap) is a human's call")


class Thresholds:
    def __init__(self):
        self.heartbeat_timeout = _env_int("LIVENESS_SCAN_HEARTBEAT_TIMEOUT_MIN",
                                          DEFAULT_HEARTBEAT_TIMEOUT_MIN)
        self.commit_stale = _env_int("LIVENESS_SCAN_COMMIT_STALE_MIN", DEFAULT_COMMIT_STALE_MIN)
        self.ceiling = _env_int("LIVENESS_SCAN_CEILING_MIN", DEFAULT_CEILING_MIN)


def classify(wt: dict, now: datetime, *, active_plan: Plan | None, executed_plan: Plan | None,
             commit_age_min: float | None, ahead_of_base: bool | None,
             heartbeat_age_min: float | None, th: Thresholds) -> dict:
    """Classify ONE worktree → {status, reason, escalate, recovery, signal, age_min}.
    ambiguous => escalate; the core never reaps."""
    path = wt.get("path", "")
    branch = wt.get("branch", "")
    is_main_wt = branch in ("main", "master", "")   # main/detached: session-liveness is N/A

    # Prefer the precise heartbeat age; fall back to commit mtime (coarser, flagged).
    if heartbeat_age_min is not None:
        age, stale_after, signal = heartbeat_age_min, th.heartbeat_timeout, "heartbeat"
    else:
        age, stale_after, signal = commit_age_min, th.commit_stale, "commit-mtime"
    note = "" if signal == "heartbeat" else f" [{NO_HEARTBEAT_NOTE}]"

    # main / detached is structurally NOT a session worktree — classify clean BEFORE any plan
    # claim, so an active plan carrying main-repo-absolute paths doesn't mis-mark main as running.
    if is_main_wt:
        return {"status": "clean",
                "reason": f"main / non-session worktree (branch {branch or 'detached'})",
                "escalate": False, "recovery": None, "signal": signal, "age_min": age}

    if active_plan is not None:
        if age is None:
            return {"status": "ambiguous",
                    "reason": f"active plan {active_plan.name} but no activity signal (no "
                    "heartbeat, no readable commit time) — crashed-or-fresh unclear; ESCALATE, "
                    "never reap",
                    "escalate": True, "recovery": _recovery_command(path),
                    "signal": signal, "age_min": None}
        if age > th.ceiling:
            return {"status": "ambiguous",
                    "reason": f"active plan {active_plan.name} idle {age:.0f}m (> {th.ceiling}m "
                    f"whole-WP ceiling) via {signal} — crashed-or-slow unclear; ESCALATE, never "
                    f"reap{note}",
                    "escalate": True, "recovery": _recovery_command(path),
                    "signal": signal, "age_min": age}
        if age > stale_after:
            return {"status": "stalled",
                    "reason": f"active plan {active_plan.name} quiet {age:.0f}m (> {stale_after}m "
                    f"{signal} window) — likely slow/paused; watch{note}",
                    "escalate": False, "recovery": None, "signal": signal, "age_min": age}
        return {"status": "running",
                "reason": f"active plan {active_plan.name}, {signal} fresh ({age:.0f}m)",
                "escalate": False, "recovery": None, "signal": signal, "age_min": age}

    if executed_plan is not None or ahead_of_base is True:
        who = executed_plan.name if executed_plan is not None else "branch ahead of base"
        return {"status": "done-unmerged",
                "reason": f"work complete / commits not on base ({who}) — pending merge",
                "escalate": False, "recovery": None, "signal": signal, "age_min": age}

    return {"status": "idle",
            "reason": f"no active plan claims this worktree; branch {branch or 'detached'} clean "
            "or merged",
            "escalate": False, "recovery": None, "signal": signal, "age_min": age}


# ── gather (pure read; now + probe injected for testability) ─────────────────────────
def gather(*, plans_dir: Path | None, base: str, heartbeat_relpath: str | None,
           now: datetime, probe: GitProbe, th: Thresholds,
           heartbeat_age_override: float | None = None) -> dict:
    plans = load_plans(plans_dir)
    active_plans = [p for p in plans if p.status == "active"]
    executed_plans = [p for p in plans if p.status == "executed"]

    sessions: list[dict] = []
    for wt in probe.worktrees():
        root = wt.get("path", "")
        claim_active = next((p for p in active_plans if plan_specifically_claims(p, root)), None)
        claim_executed = next((p for p in executed_plans if plan_specifically_claims(p, root)), None)
        head = wt.get("head", "")
        commit_age = _age_min(now, probe.commit_unixtime(head))
        # ahead-of-base only matters when no active plan owns the worktree (done vs idle).
        ahead = probe.ahead_of_base(head, base) if claim_active is None else None

        if heartbeat_age_override is not None:
            hb_age: float | None = heartbeat_age_override
        else:
            hb_age = None
            if heartbeat_relpath:
                hb_file = Path(root) / heartbeat_relpath
                if hb_file.is_file():
                    try:
                        hb_age = max(0.0, (now.timestamp() - hb_file.stat().st_mtime) / 60.0)
                    except OSError:
                        hb_age = None

        c = classify(wt, now, active_plan=claim_active, executed_plan=claim_executed,
                     commit_age_min=commit_age, ahead_of_base=ahead,
                     heartbeat_age_min=hb_age, th=th)
        sessions.append({"path": root, "branch": wt.get("branch", ""), "head": head, **c})

    counts: dict[str, int] = {}
    for s in sessions:
        counts[s["status"]] = counts.get(s["status"], 0) + 1

    return {
        "now": now,
        "base": base,
        "sessions": sessions,
        "counts": counts,
        "escalations": [s for s in sessions if s["escalate"]],
        "active_plan_count": len(active_plans),
    }


# ── rendering ────────────────────────────────────────────────────────────────────────
_STATUS_ORDER = ["ambiguous", "stalled", "done-unmerged", "running", "idle", "clean"]


def build_report(digest: dict) -> str:
    now = digest["now"]
    counts = digest["counts"]
    summary = ", ".join(f"{counts[k]} {k}" for k in _STATUS_ORDER if counts.get(k)) or "no worktrees"
    lines = [
        "# liveness-scan — fleet liveness / escalations",
        "",
        f"_Generated {now.isoformat()} by liveness-scan.py (read-only reporter). Report-only: "
        "never reaps, exits 0. Escalations are for a human to action._",
        "",
        f"**Summary:** {summary}. Active plans: {digest['active_plan_count']}. "
        f"Base: `{digest['base']}`.",
        "",
        "## Escalations (judgment required — a human actions; the scanner never auto-reaps)",
        "",
    ]
    if not digest["escalations"]:
        lines.append("_None — no ambiguous stalls this scan._")
    else:
        for s in digest["escalations"]:
            lines.append(f"### `{s['branch'] or s['path']}` — {s['status'].upper()}")
            lines.append(f"- {s['reason']}")
            lines.append(f"- path: `{s['path']}`")
            if s.get("recovery"):
                lines.append(f"- recovery (copy-paste; inspect first): `{s['recovery']}`")
            lines.append("")
    lines.append("## All worktrees")
    lines.append("")
    for s in sorted(digest["sessions"], key=lambda x: (_STATUS_ORDER.index(x["status"])
                    if x["status"] in _STATUS_ORDER else 99, x["path"])):
        lines.append(f"- **{s['status']}** · `{s['branch'] or '(detached)'}` · {s['reason']}")
    lines.append("")
    return "\n".join(lines)


def jsonl_row(digest: dict) -> str:
    return json.dumps({
        "ts": digest["now"].isoformat(),
        "tool": "liveness-scan",
        "base": digest["base"],
        "counts": digest["counts"],
        "active_plans": digest["active_plan_count"],
        "escalations": [{"branch": s["branch"], "path": s["path"], "status": s["status"],
                         "reason": s["reason"]} for s in digest["escalations"]],
    }, sort_keys=True)


def _explain(digest: dict, th: Thresholds) -> str:
    return (f"liveness-scan --explain: HEARTBEAT_TIMEOUT_MIN={th.heartbeat_timeout} "
            f"COMMIT_STALE_MIN={th.commit_stale} CEILING_MIN={th.ceiling} base={digest['base']} "
            f"worktrees={len(digest['sessions'])} counts={digest['counts']} "
            f"escalations={len(digest['escalations'])}")


def write_outputs(log_dir: Path, report: str, row: str) -> tuple[Path, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    md = log_dir / "liveness-escalations.md"
    jl = log_dir / "liveness-scan.log"
    md.write_text(report + "\n", encoding="utf-8")
    with jl.open("a", encoding="utf-8") as fh:
        fh.write(row + "\n")
    return md, jl


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="liveness-scan.py",
        description="Read-only fleet-liveness / escalations reporter (advisory; exits 0).")
    ap.add_argument("--root", default=".", metavar="DIR",
                    help="A path inside the repo whose worktrees to scan (default: cwd).")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print the report; write NOTHING")
    ap.add_argument("--json", action="store_true", help="emit the structured digest as JSON")
    ap.add_argument("--explain", action="store_true",
                    help="add the maintainer breakdown to stderr")
    args = ap.parse_args(argv)

    # Resolve the repo top-level so `worktree list` enumerates the whole fleet even from a subdir.
    try:
        top = subprocess.run(["git", "-C", args.root, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        repo_root = top.stdout.strip() if top.returncode == 0 else str(Path(args.root).resolve())
    except (OSError, subprocess.SubprocessError):
        repo_root = str(Path(args.root).resolve())

    th = Thresholds()
    now = datetime.now(timezone.utc)
    probe = GitProbe(repo_root)
    digest = gather(plans_dir=_plans_dir(), base=_base_ref(),
                    heartbeat_relpath=_heartbeat_relpath(), now=now, probe=probe, th=th)

    if args.explain:
        print(_explain(digest, th), file=sys.stderr)

    if args.json:
        print(json.dumps({
            "base": digest["base"],
            "counts": digest["counts"],
            "active_plans": digest["active_plan_count"],
            "sessions": digest["sessions"],
            "escalations": len(digest["escalations"]),
        }, indent=2))
        return 0

    report = build_report(digest)
    print(report)
    log_dir = _log_dir()
    if args.dry_run:
        print("\nliveness-scan: --dry-run, wrote nothing", file=sys.stderr)
    elif log_dir is not None:
        md, jl = write_outputs(log_dir, report, jsonl_row(digest))
        print(f"\nliveness-scan: wrote {md} + appended {jl}", file=sys.stderr)
    else:
        print("\nliveness-scan: LIVENESS_SCAN_LOG_DIR unset — printed only, wrote nothing",
              file=sys.stderr)
    return 0


def safe_main(argv: list[str] | None = None) -> int:
    """Exit-0-always wrapper. A pure reporter must NEVER break a scheduled run or a kickoff, so an
    unhandled exception is surfaced to stderr and swallowed to a 0 return. argparse's SystemExit
    (--help / bad flag) propagates unchanged."""
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception:
        print("liveness-scan: unhandled exception; advisory exit 0. Trace:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(safe_main())
