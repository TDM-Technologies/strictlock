#!/usr/bin/env python3
"""generated-sink-commit-gate — a fail-closed commit-time freshness gate.

A StrictLock module. A *generated sink* is any checked-in artifact that is
DERIVED from canonical source by a generator command: a manifest, an index, a
JSON schema, an OpenAPI/Swagger document, a generated README, a rendered table.
The sink is committed to the repo so humans and tools (and a stateless agent's
cross-session memory) can read it without re-running the generator. The hazard:
the moment a source changes but the sink is not regenerated, every downstream
reader silently desyncs — and nothing notices until a stale fact has already
propagated.

This gate makes "commit a stale sink" structurally impossible. Wired as a git
`pre-commit` hook, on any commit whose staged changes touch the configured
source paths it:

  1. re-runs the configured generator in `--check` mode (regenerate-and-compare,
     NO working-tree mutation),
  2. and refuses the commit (loud non-zero exit) unless the checked-in sink is a
     BYTE-EXACT match of what the generator would now produce.

It is **fail-closed** by construction: a missing/misconfigured generator, a
generator that errors or times out, an unreadable sink, or any internal error is
a commit-BLOCKING condition — never a silent pass. A freshness gate that fails
open is not a freshness gate.

Everything is configured by environment variables (see the README CONFIG
table). The repo root is resolved with `git rev-parse --show-toplevel` — there
are no machine-specific or project-specific defaults baked in.

Exit codes (the git-hook contract):
  0  -> allow the commit (gate disabled, not triggered, or sink is fresh)
  1  -> block the commit (stale sink, or any fail-closed condition)

Requirements: Python 3.8+ (standard library only).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

# --- environment variable names (consistently SINK_COMMIT_GATE_*-prefixed) ----
ENV_ENABLE = "SINK_COMMIT_GATE"           # "on" enables the gate; else inert.
ENV_GENERATOR = "SINK_COMMIT_GATE_GENERATOR"   # the generator --check command.
ENV_SOURCE_PATHS = "SINK_COMMIT_GATE_SOURCE_PATHS"  # os.pathsep list of source path prefixes (trigger).
ENV_GENERATOR_CWD = "SINK_COMMIT_GATE_GENERATOR_CWD"  # cwd for the generator (repo-root-relative or absolute).
ENV_TIMEOUT = "SINK_COMMIT_GATE_TIMEOUT"  # seconds before the generator is treated as a fail-closed timeout.
ENV_LOG_DIR = "SINK_COMMIT_GATE_LOG_DIR"  # optional dir for the append-only decision log.
ENV_BYPASS = "SINK_COMMIT_GATE_BYPASS"    # "1" = single-commit, log-on-use bypass (uniquely-named).

DEFAULT_TIMEOUT_S = 120

# Block exit code (the loud refusal). 0 = allow.
EXIT_ALLOW = 0
EXIT_BLOCK = 1


def _log(repo_root: Path | None, decision: str, reason: str) -> None:
    """Append a one-line audit record when a log dir is configured.

    Best-effort: a logging failure must never weaken the gate, so it is
    swallowed. The decision itself is always printed to stderr by the caller.
    """
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
            "gate": "generated-sink-commit-gate",
            "decision": decision,
            "reason": reason,
            "repo_root": str(repo_root) if repo_root else None,
        }
        with (d / "sink-commit-gate.log").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record) + "\n")
    except OSError:
        pass


def allow(repo_root: Path | None, reason: str) -> None:
    print(f"generated-sink-commit-gate: ALLOW — {reason}", file=sys.stderr)
    _log(repo_root, "allow", reason)
    sys.exit(EXIT_ALLOW)


def block(repo_root: Path | None, reason: str) -> None:
    # Fail-CLOSED dominant: every blocking path (stale sink, infra error,
    # timeout, misconfiguration) routes here with a loud non-zero exit.
    print(f"generated-sink-commit-gate: BLOCKED — {reason}", file=sys.stderr)
    _log(repo_root, "block", reason)
    sys.exit(EXIT_BLOCK)


def _clean_git_env() -> dict:
    """Strip worktree-private git env before invoking a generator.

    A generator that itself shells out to `git` (e.g. to read the last-commit
    date) can throw inside a linked worktree if GIT_DIR / GIT_WORK_TREE /
    GIT_INDEX_FILE are inherited. Stripping them lets the child re-discover the
    repo cleanly from its cwd. The gate's own git calls pass cwd explicitly and
    do not depend on these.
    """
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

    A failure here (not a git repo, git missing) is fail-closed: the gate cannot
    establish where the sink lives, so it must refuse rather than guess.
    """
    code, out, err = _git(["rev-parse", "--show-toplevel"], cwd=Path(os.getcwd()))
    if code != 0 or not out.strip():
        block(None, f"could not resolve repo root via `git rev-parse --show-toplevel` ({err.strip()}) — fail-closed.")
    return Path(out.strip()).resolve()


def _intermediate_git_state(repo_root: Path) -> str | None:
    """A rebase/merge/cherry-pick in progress fires the commit hook but offers no
    clean `--no-verify`; the post-operation commit re-validates. Detect it so the
    gate skips rather than blocking a legitimate conflict-resolution commit."""
    code, out, _ = _git(["rev-parse", "--git-path", "."], repo_root)
    # Resolve the (possibly worktree-shared) git dir for in-progress markers.
    code2, gd_out, _ = _git(["rev-parse", "--git-common-dir"], repo_root)
    if code2 != 0 or not gd_out.strip():
        return None
    gd = Path(gd_out.strip())
    if not gd.is_absolute():
        gd = (repo_root / gd).resolve()
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
        if (gd / marker).exists():
            return marker
    return None


def _staged_paths(repo_root: Path) -> list:
    code, out, err = _git(["diff", "--cached", "--name-only"], repo_root)
    if code != 0:
        block(repo_root, f"`git diff --cached --name-only` failed ({err.strip()}) — fail-closed.")
    return [p.strip().replace("\\", "/") for p in out.splitlines() if p.strip()]


def _source_prefixes() -> list:
    raw = os.environ.get(ENV_SOURCE_PATHS, "")
    return [p.strip().replace("\\", "/").rstrip("/") for p in raw.split(os.pathsep) if p.strip()]


def _timeout_s() -> int:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        v = int(raw)
    except ValueError:
        # A misconfigured timeout must not silently disable the gate; fall back
        # to the default loudly rather than crashing or skipping.
        print(
            f"generated-sink-commit-gate: {ENV_TIMEOUT}={raw!r} is not an integer; "
            f"using default {DEFAULT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        return DEFAULT_TIMEOUT_S
    if v <= 0:
        print(
            f"generated-sink-commit-gate: {ENV_TIMEOUT}={v} must be > 0; "
            f"using default {DEFAULT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        return DEFAULT_TIMEOUT_S
    return v


def run_check(repo_root: Path) -> None:
    """Regenerate-and-compare via the configured generator's --check mode.

    The generator is expected to exit non-zero iff the checked-in sink is NOT a
    byte-exact match of a fresh regeneration (the universal `--check` /
    `git diff --exit-code` convention). The gate does NOT mutate the working
    tree and does NOT auto-stage — fail-and-tell, human stays in the loop.

    Every failure mode — generator missing, generator errors, generator times
    out, bad config — is fail-closed (block).
    """
    generator = os.environ.get(ENV_GENERATOR, "").strip()
    if not generator:
        block(
            repo_root,
            f"{ENV_GENERATOR} is not set — the gate cannot verify sink freshness "
            f"without a generator --check command. Configure it (see the README "
            f"CONFIG section) or disable the gate with {ENV_ENABLE}!=on. Fail-closed.",
        )

    try:
        argv = shlex.split(generator, posix=(os.name != "nt"))
    except ValueError as e:
        block(repo_root, f"{ENV_GENERATOR} is not a parseable command ({e}) — fail-closed.")
    if not argv:
        block(repo_root, f"{ENV_GENERATOR} parsed to an empty command — fail-closed.")

    # Resolve the generator cwd (repo-root-relative or absolute). Default = root.
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
            "the checked-in generated sink is STALE or invalid — commit refused "
            "(fail-closed). Regenerate it (run your generator WITHOUT --check), stage "
            "the result, and commit again.\n\n"
            f"generator command: {generator}\n"
            f"generator output:\n{detail}",
        )

    allow(repo_root, "staged source changed and the generated sink is byte-exact in sync.")


def main() -> None:
    # Master switch — inert unless explicitly enabled. Same posture as the rest
    # of StrictLock: opt-in, no surprise behavior on import.
    if os.environ.get(ENV_ENABLE, "").strip().lower() != "on":
        # Not enabled => allow (exit 0). No repo resolution needed.
        print(f"generated-sink-commit-gate: {ENV_ENABLE} != 'on' — gate disabled.", file=sys.stderr)
        sys.exit(EXIT_ALLOW)

    # Uniquely-named, single-commit, log-on-use bypass. Deliberately NOT a shared
    # bypass: disabling THIS gate must never collaterally disable a sibling gate.
    if os.environ.get(ENV_BYPASS, "").strip() == "1":
        print(
            f"generated-sink-commit-gate: {ENV_BYPASS}=1 — freshness gate DELIBERATELY "
            f"bypassed for this commit (logged on use). Use only for a documented, "
            f"deliberately-shipped stale snapshot.",
            file=sys.stderr,
        )
        _log(None, "bypass", f"{ENV_BYPASS}=1 explicit single-commit bypass")
        sys.exit(EXIT_ALLOW)

    repo_root = resolve_repo_root()

    # A rebase/merge/cherry-pick commit re-validates after the operation; skip.
    inter = _intermediate_git_state(repo_root)
    if inter is not None:
        allow(repo_root, f"intermediate git state ({inter}) — skipping; the post-operation commit re-validates.")

    # Trigger only when staged changes touch the configured source paths. A gate
    # with no configured source paths cannot know what to watch — that is a
    # fail-closed misconfiguration, not a silent allow.
    prefixes = _source_prefixes()
    if not prefixes:
        block(
            repo_root,
            f"{ENV_SOURCE_PATHS} is not set — the gate cannot tell which staged paths "
            f"should trigger a freshness check. Configure the source path prefix(es) "
            f"(see the README CONFIG section). Fail-closed.",
        )

    staged = _staged_paths(repo_root)
    touched = any(
        sp == pref or sp.startswith(pref + "/") or sp.startswith(pref)
        for sp in staged
        for pref in prefixes
    )
    if not touched:
        allow(repo_root, "commit stages no configured source path; nothing to verify.")

    run_check(repo_root)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail-CLOSED on our own crash: an integrity gate that fails open on an
        # internal bug is not an integrity gate. Block loudly.
        traceback.print_exc(file=sys.stderr)
        try:
            block(None, "unhandled internal error — fail-closed.")
        except SystemExit:
            raise
