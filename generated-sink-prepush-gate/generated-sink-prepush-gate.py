#!/usr/bin/env python3
"""generated-sink-prepush-gate — a fail-closed pre-push freshness backstop.

A StrictLock module. The SIBLING of generated-sink-commit-gate, and the line of
LAST defense for the same property: never let a stale checked-in generated
artifact (manifest / index / schema / swagger / README) leave the machine.

The commit-time gate is the line of FIRST defense, but a `--no-verify` commit, a
deliberate single-commit bypass, or a source change committed without staging
its companion sink can let a stale sink slip onto a branch. This pre-push gate
intercepts `git push` and re-runs the configured generator in `--check` mode
against the terminal branch state — refusing the push (loud non-zero exit)
unless the checked-in sink is a BYTE-EXACT match of a fresh regeneration.

It DEPARTS from the commit-time gate on two points:

  1. trigger: it runs on EVERY push. There is no staged-source early-exit — the
     terminal branch state is validated regardless of what is about to ship.
     (Defense-in-depth complement to the staged-change-scoped commit-time gate.)
  2. bypass isolation: it honors ONLY its own uniquely-named bypass,
     SINK_PREPUSH_GATE_BYPASS — NOT the commit-time gate's bypass. Bypassing the
     first line of defense must not silently disable the last line of defense.

It is **fail-closed** by construction: a missing/misconfigured generator, a
generator that errors or times out, or any internal error is a push-BLOCKING
condition — never a silent pass. A backstop that fails open is not a backstop.

Everything is configured by environment variables (see the README CONFIG
table). The repo root is resolved with `git rev-parse --show-toplevel` — no
machine-specific or project-specific defaults are baked in.

Wiring: this script is a git `pre-push` hook. Git invokes a pre-push hook with
two argv (remote name, remote URL) and feeds the refs being pushed on stdin.
This gate validates the *working-tree* sink state, so it ignores both — it
neither reads stdin nor requires the argv — and works as a plain pre-push hook.

Exit codes (the git-hook contract):
  0  -> allow the push (gate disabled, or sink is fresh)
  1  -> block the push (stale sink, or any fail-closed condition)

Requirements: Python 3.8+ (standard library only).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

# --- environment variable names (consistently SINK_PREPUSH_GATE_*-prefixed) ---
ENV_ENABLE = "SINK_PREPUSH_GATE"           # "on" enables the gate; else inert.
ENV_GENERATOR = "SINK_PREPUSH_GATE_GENERATOR"   # the generator --check command.
ENV_GENERATOR_CWD = "SINK_PREPUSH_GATE_GENERATOR_CWD"  # cwd for the generator (repo-root-relative or absolute).
ENV_TIMEOUT = "SINK_PREPUSH_GATE_TIMEOUT"  # seconds before the generator is treated as a fail-closed timeout.
ENV_LOG_DIR = "SINK_PREPUSH_GATE_LOG_DIR"  # optional dir for the append-only decision log.
ENV_BYPASS = "SINK_PREPUSH_GATE_BYPASS"    # "1" = single-push, log-on-use bypass (uniquely-named).

DEFAULT_TIMEOUT_S = 120

EXIT_ALLOW = 0
EXIT_BLOCK = 1


def _log(repo_root: Path | None, decision: str, reason: str) -> None:
    """Append a one-line audit record when a log dir is configured (best-effort)."""
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
            "gate": "generated-sink-prepush-gate",
            "decision": decision,
            "reason": reason,
            "repo_root": str(repo_root) if repo_root else None,
        }
        with (d / "sink-prepush-gate.log").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record) + "\n")
    except OSError:
        pass


def allow(repo_root: Path | None, reason: str) -> None:
    print(f"generated-sink-prepush-gate: ALLOW — {reason}", file=sys.stderr)
    _log(repo_root, "allow", reason)
    sys.exit(EXIT_ALLOW)


def block(repo_root: Path | None, reason: str) -> None:
    # Fail-CLOSED dominant: every blocking path routes here with a loud non-zero
    # exit. NEVER fall through to allow.
    print(f"generated-sink-prepush-gate: BLOCKED — {reason}", file=sys.stderr)
    _log(repo_root, "block", reason)
    sys.exit(EXIT_BLOCK)


def _clean_git_env() -> dict:
    """Strip worktree-private git env before invoking a generator that may shell
    out to git itself (avoids EISDIR/ENOENT inside a linked worktree)."""
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return env


def _git(args: list, cwd: Path | None) -> tuple:
    """Run a git command. A git/OS failure is fail-closed (the caller blocks)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=30,
            env=_clean_git_env(),
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError) as e:
        block(cwd, f"`git {' '.join(args)}` failed ({e}) — fail-closed.")
        raise  # unreachable (block exits); satisfies type-checkers.


def resolve_repo_root() -> Path:
    """Resolve the repo root via `git rev-parse --show-toplevel`. No hardcoding.

    A failure here is fail-closed: the gate cannot establish where the sink lives,
    so it must refuse rather than guess.
    """
    code, out, err = _git(["rev-parse", "--show-toplevel"], cwd=Path(os.getcwd()))
    if code != 0 or not out.strip():
        block(None, f"could not resolve repo root via `git rev-parse --show-toplevel` ({err.strip()}) — fail-closed.")
    return Path(out.strip()).resolve()


def _timeout_s() -> int:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        v = int(raw)
    except ValueError:
        print(
            f"generated-sink-prepush-gate: {ENV_TIMEOUT}={raw!r} is not an integer; "
            f"using default {DEFAULT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        return DEFAULT_TIMEOUT_S
    if v <= 0:
        print(
            f"generated-sink-prepush-gate: {ENV_TIMEOUT}={v} must be > 0; "
            f"using default {DEFAULT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        return DEFAULT_TIMEOUT_S
    return v


def run_check(repo_root: Path) -> None:
    """Regenerate-and-compare via the configured generator's --check mode.

    The generator is expected to exit non-zero iff the checked-in sink is NOT a
    byte-exact match of a fresh regeneration. The gate does NOT mutate the
    working tree and does NOT auto-stage — fail-and-tell, human stays in the loop.

    Every failure mode is fail-closed (block).
    """
    generator = os.environ.get(ENV_GENERATOR, "").strip()
    if not generator:
        block(
            repo_root,
            f"{ENV_GENERATOR} is not set — the backstop cannot verify sink freshness "
            f"without a generator --check command. Configure it (see the README CONFIG "
            f"section) or disable the gate with {ENV_ENABLE}!=on. Fail-closed.",
        )

    try:
        argv = shlex.split(generator, posix=(os.name != "nt"))
    except ValueError as e:
        block(repo_root, f"{ENV_GENERATOR} is not a parseable command ({e}) — fail-closed.")
    if not argv:
        block(repo_root, f"{ENV_GENERATOR} parsed to an empty command — fail-closed.")

    cwd_cfg = os.environ.get(ENV_GENERATOR_CWD, "").strip()
    if cwd_cfg:
        cwd_path = Path(cwd_cfg)
        gen_cwd = cwd_path if cwd_path.is_absolute() else (repo_root / cwd_path)
    else:
        gen_cwd = repo_root
    gen_cwd = gen_cwd.resolve()
    if not gen_cwd.is_dir():
        block(repo_root, f"generator cwd {gen_cwd} does not exist — fail-closed.")

    timeout_s = _timeout_s()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(gen_cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_clean_git_env(),
        )
    except subprocess.TimeoutExpired:
        block(
            repo_root,
            f"generator exceeded {timeout_s}s ({ENV_TIMEOUT}) — fail-closed. A generator "
            f"that cannot finish cannot prove the sink is fresh.",
        )
    except OSError as e:
        block(
            repo_root,
            f"could not invoke the generator {argv!r} ({e}) — fail-closed. Check "
            f"{ENV_GENERATOR} / {ENV_GENERATOR_CWD} and that the tool is installed.",
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        block(
            repo_root,
            "BACKSTOP — the checked-in generated sink is STALE or invalid; push refused "
            "(fail-closed). Regenerate it (run your generator WITHOUT --check), commit "
            "the result, and push again.\n\n"
            f"If this is a deliberate single-push of a documented stale snapshot, set "
            f"{ENV_BYPASS}=1 for this one push (logged on use).\n\n"
            f"generator command: {generator}\n"
            f"generator output:\n{detail}",
        )

    allow(repo_root, "BACKSTOP — generated sink is byte-exact in sync; push allowed.")


def main() -> None:
    # Master switch — inert unless explicitly enabled.
    if os.environ.get(ENV_ENABLE, "").strip().lower() != "on":
        print(f"generated-sink-prepush-gate: {ENV_ENABLE} != 'on' — gate disabled.", file=sys.stderr)
        sys.exit(EXIT_ALLOW)

    # Uniquely-named, single-push, log-on-use bypass. Distinct from the
    # commit-time gate's bypass on purpose: a bypass of the first line of defense
    # must not collaterally disable the last line of defense.
    if os.environ.get(ENV_BYPASS, "").strip() == "1":
        print(
            f"generated-sink-prepush-gate: {ENV_BYPASS}=1 — backstop DELIBERATELY "
            f"bypassed for this push (logged on use). Use only for a documented, "
            f"deliberately-shipped stale snapshot.",
            file=sys.stderr,
        )
        _log(None, "bypass", f"{ENV_BYPASS}=1 explicit single-push bypass")
        sys.exit(EXIT_ALLOW)

    repo_root = resolve_repo_root()
    # No staged-source early-exit: the backstop always validates the terminal
    # working-tree sink state on a push.
    run_check(repo_root)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail-CLOSED on our own crash. A backstop that fails open on an internal
        # bug is not a backstop. Block loudly.
        traceback.print_exc(file=sys.stderr)
        try:
            block(None, "unhandled internal error — fail-closed.")
        except SystemExit:
            raise
