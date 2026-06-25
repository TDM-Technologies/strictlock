#!/usr/bin/env python3
r"""sink-resolver.py — deterministic auto-resolution of generated-file merge conflicts.

A StrictLock Concurrency / Gates module. A *generated sink* is any checked-in artifact
DERIVED from canonical source by a generator command: a manifest, an index, a JSON schema,
an OpenAPI/Swagger document, a generated README, a rendered table. When two branches both
change the sources, the sink diverges on both sides and a plain merge either mangles it
(line-merging two renders into a corrupt blend) or stops with a conflict a human must hand-
resolve — over and over, for a file no human should ever edit by hand.

THE ONE IDEA. A pure render has no merge — it has a *regenerate*. If the sink is a
deterministic function of its sources and the sources are conflict-free by construction (or
already cleanly merged), then the correct post-merge sink is not "ours" or "theirs" or some
blend of the two — it is exactly what the generator produces from the merged sources. So the
resolution is mechanical and provably correct: take the merged sources, run the generator,
byte-check the result, stage it. No guessing at intent, because the sink carries none.

  merge=binary (.gitattributes) makes git ALWAYS-conflict-cleanly on the sink rather than
  silently line-merging it: the path is left unmerged, "ours" kept verbatim, NO corrupt auto-
  merge and NO conflict markers (verified on git 2.54). `resolve` then overwrites that with a
  fresh regenerate. A plain `post-merge` hook covers the non-conflicting case (sources merged
  cleanly, sink merely went stale).

╔════════════════════════════════════════════════════════════════════════════════════════╗
║ LOUD PRECONDITION — on the tin, not a footnote.                                          ║
║                                                                                          ║
║ This is sound ONLY if your sink is a PURE, DETERMINISTIC, byte-`--check`-able render of  ║
║ its sources. If it is not — if the sink carries any hand-authored content the generator  ║
║ does not reproduce — then regenerate-from-sources SILENTLY CLOBBERS those peer edits.    ║
║ That is the one failure that makes this dangerous if mis-adopted. The byte-oracle below   ║
║ is the structural guard: a regenerate that does not reproduce a byte-exact, stable sink   ║
║ FAILS CLOSED (exit 1) rather than finalizing a clobber.                                  ║
╚════════════════════════════════════════════════════════════════════════════════════════╝

TWO VERBS.
  resolve   The conflicted-merge auto-resolver. Reads the COMPLETE unmerged set
            (`git diff --name-only --diff-filter=U`). If EVERY unmerged path is a configured
            sink: regenerate the sinks from the (already-merged) sources, run the byte-oracle,
            `git add` them, and — if a merge is in progress — finalize it. If ANY unmerged
            path is NOT a sink: ESCALATE (exit 4), write nothing, name the offenders. The
            escalation is the whole safety story: auto-resolving anything but the
            deterministically-regenerable sink would be guessing at real source intent.
  check     The web-UI / CI backstop. `merge=binary` and the `post-merge`/pre-merge hooks are
            INERT on a server-side / web-UI merge (git ignores user merge drivers there and
            fires no local hook), so a stale sink can reach the trunk that way. `check` re-
            derives the sink and fails (exit 1) if the committed bytes are not a fresh render
            — run it in CI on the merged result as the terminal guard.

TWO PLACEMENT VARIANTS.
  * Default (this script) — the git-native shape: `merge=binary` on the sink + a `post-merge`
    hook that regenerates + `resolve` for the conflicted case + `check` in CI. Local merges
    self-heal; CI catches the web-UI path. ~Right for a fleet that merges locally.
  * Documented alternative — a PRE-merge worktree resolver (run before the merge completes, so
    a web-UI fast-forward lands an already-resolved tree), with circuit breakers and a dry-run
    default. Heavier, but workflow-agnostic. See README "The web-UI-merge variant".

THE BYTE-ORACLE (the determinism guard that makes the clobber safe).
  After regenerating, `resolve`/`check` prove the result is a stable, byte-exact render:
    * if SINK_RESOLVER_CHECK_CMD is set, it IS the oracle — a generator `--check` that
      regenerates internally, mutates nothing, and exits non-zero on any drift (the universal
      `--check` / `git diff --exit-code` convention). This is the strong oracle: it proves the
      committed bytes equal a fresh render of the CURRENT sources.
    * if it is unset, the oracle degrades to a DOUBLE-REGENERATE determinism check (regenerate,
      snapshot, regenerate again, compare) — weaker (it proves the generator is deterministic,
      not that the sink matches the sources independently) and announced as such. Configure
      CHECK_CMD for the strong guarantee.
  A failed oracle is FAIL-CLOSED: the regenerate did not produce a stable byte-exact sink, so
  the sources may be unresolved or the generator non-deterministic — escalate to a human rather
  than finalize a merge on an unproven sink.

CONFIG (env). See CONFIG.md.
  SINK_RESOLVER=on  ·  SINK_RESOLVER_GENERATOR_CMD (regenerate in place)  ·
  SINK_RESOLVER_SINKS (os.pathsep path list, placed under merge=binary)  ·
  SINK_RESOLVER_CHECK_CMD (the strong byte-oracle; recommended)  ·
  SINK_RESOLVER_GENERATOR_CWD  ·  SINK_RESOLVER_TIMEOUT  ·  SINK_RESOLVER_LOG_DIR.

Usage:
  python3 sink-resolver.py resolve            # auto-resolve a sink-only conflict + finalize
  python3 sink-resolver.py resolve --dry-run  # classify + print the plan; write nothing
  python3 sink-resolver.py resolve --no-commit # stage the regenerate, don't finalize the merge
  python3 sink-resolver.py check              # CI backstop: exit 1 if the sink is not fresh

Exit codes:
  0  ok (resolved / nothing to resolve / dry-run / disabled no-op / sink fresh)
  1  error / FAIL-CLOSED (git failure, misconfig, generator error/timeout, byte-oracle failed,
     or — for `check` — the sink is STALE)
  2  usage error (argparse)
  4  ESCALATION — a non-sink path is in conflict; a person is needed. Wrote nothing.

Requirements: Python 3.8+ (standard library only) and `git` on PATH.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

# ── enablement ───────────────────────────────────────────────────────────────────────
# Like scope-lease (and unlike a PreToolUse gate), sink-resolver is invoked EXPLICITLY by an
# adopter — at a conflicted merge, from a post-merge hook, in CI. So SINK_RESOLVER is an
# opt-OUT switch for scripts that want one toggle, not a silent kill of an explicit call: an
# explicit invocation with SINK_RESOLVER unset still runs; only a falsey value disables it.
def _enabled() -> bool:
    val = os.environ.get("SINK_RESOLVER")
    if val is None:
        return True
    return val.strip().lower() in ("on", "1", "true", "yes")


DEFAULT_TIMEOUT_S = 120

# Exit codes (the contract a calling hook / CI branches on).
EXIT_OK = 0
EXIT_ERROR = 1        # error / fail-closed / (check) stale sink
EXIT_ESCALATE = 4     # a non-sink conflict — write nothing, a human is needed


# ── exceptions ───────────────────────────────────────────────────────────────────────
class ResolveError(Exception):
    """sink-resolver could not complete soundly (a git failure, a misconfigured/erroring
    generator, or a failed byte-oracle). Fail-closed: surfaced as exit 1, never a silent pass —
    a resolver that fails open finalizes merges it never proved correct."""


class Escalation(Exception):
    """A merge conflict touches a NON-sink file. resolve writes nothing and hands the merge back
    to a person — the expected 'needs a human' outcome, not a bug. Auto-resolving anything but
    the deterministically-regenerable sink would be guessing at real source intent, which this
    tool refuses to do."""


# ── git plumbing ─────────────────────────────────────────────────────────────────────
def _clean_git_env() -> dict:
    """Strip worktree-private git env before invoking the adopter's generator. A generator that
    itself shells out to git (e.g. to read a commit date) can throw inside a linked worktree if
    GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE are inherited — and a conflicted-merge state makes
    that more likely. Stripping them lets the child re-discover the repo cleanly from its cwd.
    The resolver's OWN git calls pass `-C root` explicitly and do not depend on these."""
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return env


def _git_run(
    root: Path, *args: str, check_rc: bool = True
) -> subprocess.CompletedProcess:
    """Run `git -C root <args>` with signing disabled (no interactive gpg prompt) and path
    quoting off (so unmerged-path output is byte-comparable repo-relative POSIX). Identity is
    left to the repo's own config, so an auto-finalized merge stays attributed to the session —
    not to this tool. Raises ResolveError on a non-zero exit when check_rc."""
    proc = subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false",
         "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, env=_clean_git_env(),
    )
    if check_rc and proc.returncode != 0:
        raise ResolveError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def resolve_repo_root(start: Path) -> Path:
    """Resolve the repo top-level via `git rev-parse --show-toplevel`. No hardcoding; a failure
    (not a git repo, git missing) is fail-closed — the resolver cannot know where the sink lives,
    so it refuses rather than guesses."""
    return Path(_git_run(start, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def _unmerged_paths(root: Path) -> list[str]:
    """Repo-relative POSIX paths currently in a conflicted/unmerged state (one per line)."""
    out = _git_run(root, "diff", "--name-only", "--diff-filter=U").stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _merge_in_progress(root: Path) -> bool:
    return _git_run(root, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                    check_rc=False).returncode == 0


# ── sink-set normalization (the same-file comparison) ────────────────────────────────
def normalize_sinks(root: Path, sinks: list[str]) -> list[str]:
    """Fold each configured sink to a canonical repo-relative POSIX key so it can be compared
    against git's repo-relative unmerged-path output regardless of how it was spelled (absolute
    vs relative, `./` prefix, trailing slash, back/forward slashes). A sink that resolves OUTSIDE
    the repo is a fail-closed misconfiguration — the resolver would never be able to match it
    against an unmerged path, so a stale sink could slip through unnoticed."""
    top = root.resolve()
    keys: list[str] = []
    seen: set[str] = set()
    for raw in sinks:
        s = str(raw).strip()
        if not s:
            continue
        p = Path(s.replace("\\", "/"))
        if not p.is_absolute():
            p = top / p
        p = p.resolve()
        try:
            rel = p.relative_to(top).as_posix()
        except ValueError as exc:
            raise ResolveError(
                f"configured sink {raw!r} resolves to {p} which is OUTSIDE the repo at {top} — "
                "fail-closed (a sink the resolver can never match would let a stale artifact "
                "slip through). Fix SINK_RESOLVER_SINKS."
            ) from exc
        if rel not in seen:
            seen.add(rel)
            keys.append(rel)
    return keys


# ── the configured generator ─────────────────────────────────────────────────────────
def _parse_cmd(value: str, what: str) -> list[str]:
    try:
        argv = shlex.split(value, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ResolveError(f"{what} is not a parseable command ({exc}) — fail-closed.") from exc
    if not argv:
        raise ResolveError(f"{what} parsed to an empty command — fail-closed.")
    return argv


def _generator_cwd(root: Path) -> Path:
    cwd_cfg = os.environ.get("SINK_RESOLVER_GENERATOR_CWD", "").strip()
    if not cwd_cfg:
        return root
    p = Path(cwd_cfg)
    gen_cwd = (p if p.is_absolute() else (root / p)).resolve()
    if not gen_cwd.is_dir():
        raise ResolveError(f"generator cwd {gen_cwd} does not exist — fail-closed.")
    return gen_cwd


def _timeout_s() -> int:
    raw = os.environ.get("SINK_RESOLVER_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        v = int(raw)
    except ValueError:
        print(f"sink-resolver: SINK_RESOLVER_TIMEOUT={raw!r} is not an integer; "
              f"using default {DEFAULT_TIMEOUT_S}s.", file=sys.stderr)
        return DEFAULT_TIMEOUT_S
    if v <= 0:
        print(f"sink-resolver: SINK_RESOLVER_TIMEOUT={v} must be > 0; "
              f"using default {DEFAULT_TIMEOUT_S}s.", file=sys.stderr)
        return DEFAULT_TIMEOUT_S
    return v


def _run_configured_cmd(root: Path, value: str, what: str) -> subprocess.CompletedProcess:
    """Run a configured generator / check command in the generator cwd, with worktree-private
    git env stripped. Any way it fails to RUN (missing, OS error, timeout) is fail-closed — a
    command that cannot finish cannot prove the sink is sound. The RETURN CODE is the caller's to
    interpret (e.g. a `--check` non-zero means 'stale', not 'crashed')."""
    argv = _parse_cmd(value, what)
    gen_cwd = _generator_cwd(root)
    try:
        return subprocess.run(
            argv, cwd=str(gen_cwd), capture_output=True, text=True,
            timeout=_timeout_s(), env=_clean_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ResolveError(
            f"{what} exceeded {_timeout_s()}s (SINK_RESOLVER_TIMEOUT) — fail-closed. A generator "
            "that cannot finish cannot prove the sink is fresh."
        ) from exc
    except OSError as exc:
        raise ResolveError(
            f"could not invoke {what} {argv!r} ({exc}) — fail-closed. Check the command and that "
            "the tool is installed."
        ) from exc


def _regenerate(root: Path) -> None:
    """Run SINK_RESOLVER_GENERATOR_CMD — regenerate the sink(s) in place from the current
    (merged) sources. A non-zero exit is fail-closed (the generator itself errored)."""
    cmd = os.environ.get("SINK_RESOLVER_GENERATOR_CMD", "").strip()
    if not cmd:
        raise ResolveError(
            "SINK_RESOLVER_GENERATOR_CMD is not set — the resolver cannot regenerate the sink "
            "without it. Configure it (see CONFIG.md) or disable with SINK_RESOLVER!=on. "
            "Fail-closed."
        )
    proc = _run_configured_cmd(root, cmd, "SINK_RESOLVER_GENERATOR_CMD")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ResolveError(
            f"the generator exited non-zero ({proc.returncode}) while regenerating — fail-closed.\n"
            f"  command: {cmd}\n  output:\n{detail}"
        )


def _sink_bytes(root: Path, sink_rel: list[str]) -> dict[str, bytes | None]:
    """Snapshot the on-disk bytes of each sink (None if absent), for the determinism oracle."""
    out: dict[str, bytes | None] = {}
    for rel in sink_rel:
        fp = root / rel
        out[rel] = fp.read_bytes() if fp.is_file() else None
    return out


def byte_oracle(root: Path, sink_rel: list[str]) -> str:
    """Prove the just-regenerated sink is a STABLE, byte-exact render — the structural guard that
    makes the `--ours`-then-regenerate clobber safe. Returns the oracle mode used ("check-cmd" or
    "double-regenerate"). Raises ResolveError (fail-closed) if the proof fails.

      * SINK_RESOLVER_CHECK_CMD set -> the STRONG oracle: a generator `--check` that regenerates
        internally, mutates nothing, exits non-zero on drift. Proves the committed bytes equal a
        fresh render of the CURRENT sources.
      * unset -> the WEAKER double-regenerate determinism check: snapshot the sinks, regenerate
        again, require byte-identical output. Proves the generator is deterministic on these
        sources (so the clobber is reproducible), but not that the sink matches the sources
        independently. Announced on stderr so the weaker guarantee is never silent.
    """
    check_cmd = os.environ.get("SINK_RESOLVER_CHECK_CMD", "").strip()
    if check_cmd:
        proc = _run_configured_cmd(root, check_cmd, "SINK_RESOLVER_CHECK_CMD")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ResolveError(
                "the byte-oracle (SINK_RESOLVER_CHECK_CMD) reports the regenerated sink is NOT "
                "byte-fresh — fail-closed, finalized nothing. The sources may still be unresolved "
                "or the generator is non-deterministic; resolve by hand.\n"
                f"  command: {check_cmd}\n  output:\n{detail}"
            )
        return "check-cmd"

    # Weaker fallback: prove the generator is deterministic on these sources.
    print("sink-resolver: SINK_RESOLVER_CHECK_CMD is unset — using the weaker double-regenerate "
          "determinism oracle. Set CHECK_CMD (a generator --check) for the strong byte-exact-"
          "vs-sources guarantee.", file=sys.stderr)
    before = _sink_bytes(root, sink_rel)
    _regenerate(root)
    after = _sink_bytes(root, sink_rel)
    drifted = [rel for rel in sink_rel if before.get(rel) != after.get(rel)]
    if drifted:
        raise ResolveError(
            "the generator is NON-DETERMINISTIC on these sources (a second regenerate produced "
            f"different bytes for: {', '.join(drifted)}) — fail-closed. sink-resolver is sound "
            "only for a pure, deterministic render; resolve by hand."
        )
    return "double-regenerate"


# ── logging ──────────────────────────────────────────────────────────────────────────
def _log_decision(decision: str, reason: str, extra: dict | None = None) -> None:
    """Append one JSON line to the decision log when SINK_RESOLVER_LOG_DIR is set. Best-effort:
    a logging failure never changes the resolve outcome."""
    log_dir = os.environ.get("SINK_RESOLVER_LOG_DIR", "").strip()
    if not log_dir:
        return
    try:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tool": "sink-resolver", "decision": decision, "reason": reason,
            **(extra or {}),
        }
        with (d / "sink-resolver.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


# ── resolve — the conflicted-merge auto-resolver ─────────────────────────────────────
def resolve_merge(
    *, root: Path, sinks: list[str], finalize: bool = True, dry_run: bool = False
) -> tuple[str, list[str]]:
    """Sink-only auto-resolver. Inspects the COMPLETE unmerged set:

      • nothing unmerged                 -> ("clean", [])             nothing to resolve.
      • EVERY unmerged path is a sink    -> regenerate the sinks from the (already cleanly-merged)
                                            sources, run the byte-oracle, `git add` them, and — if
                                            a merge is in progress and finalize — commit to
                                            complete the merge. -> ("resolved-finalized" |
                                            "resolved-staged", [sinks])
      • ANY non-sink path unmerged       -> raise Escalation naming them; WRITE NOTHING.

    Sound only under the loud precondition (the sink is a pure, deterministic render): the
    sources are already reconciled by the merge, so a fresh regenerate reproduces exactly the
    sink both sides intended. No `git checkout --ours` is needed — a fresh render overwrites the
    unmerged bytes and `git add` clears the unmerged state. Returns (status, resolved_sinks).
    """
    sink_rel = normalize_sinks(root, sinks)
    if not sink_rel:
        raise ResolveError(
            "SINK_RESOLVER_SINKS is not set — the resolver cannot tell which conflicted paths it "
            "is allowed to auto-resolve, so it would have to escalate everything. Configure the "
            "sink path(s) (see CONFIG.md). Fail-closed."
        )
    sink_set = set(sink_rel)

    unmerged = _unmerged_paths(root)
    if not unmerged:
        return "clean", []
    non_sink = sorted(p for p in unmerged if p not in sink_set)
    if non_sink:
        raise Escalation(
            "merge conflicts on non-sink file(s) — escalating, wrote nothing:\n  "
            + "\n  ".join(non_sink)
            + "\nsink-resolver only auto-handles a conflict isolated to the configured generated "
            f"sink(s) ({', '.join(sink_rel)}); resolve these by hand, then finish the merge."
        )
    conflicted_sinks = sorted(p for p in unmerged if p in sink_set)

    if dry_run:
        return "would-resolve", conflicted_sinks

    _regenerate(root)                              # regenerate from the merged sources
    mode = byte_oracle(root, sink_rel)             # prove it's a stable byte-exact render
    _git_run(root, "add", "--", *conflicted_sinks)  # clears the unmerged state for the sinks

    remaining = _unmerged_paths(root)
    if remaining:                                  # defensive: nothing should remain after staging
        raise ResolveError(
            f"unexpected conflicts remain after staging the sink(s): {', '.join(remaining)} — "
            "fail-closed (did the generator leave a source unmerged?)."
        )

    if finalize and _merge_in_progress(root):
        _git_run(root, "commit", "--no-edit")
        _log_decision("resolved-finalized", f"oracle={mode}",
                      {"sinks": conflicted_sinks})
        return "resolved-finalized", conflicted_sinks
    _log_decision("resolved-staged", f"oracle={mode}", {"sinks": conflicted_sinks})
    return "resolved-staged", conflicted_sinks


# ── check — the web-UI / CI freshness backstop ───────────────────────────────────────
def check_fresh(*, root: Path, sinks: list[str]) -> tuple[bool, str]:
    """The terminal backstop for the web-UI / server-side merge path, where `merge=binary` and
    the local hooks are INERT (git ignores user merge drivers and fires no post-merge hook). Run
    in CI on the merged result. Returns (fresh, message).

      * SINK_RESOLVER_CHECK_CMD set -> its exit code is the verdict (0 fresh, non-0 stale) — it
        regenerates internally and mutates nothing. The clean path.
      * unset -> regenerate in place, `git diff --exit-code` the sink(s), then RESTORE them
        (`git checkout --`) so `check` is net non-mutating. Drift -> stale.

    A stale sink is a FAILURE (the caller exits 1): a stale generated artifact reached the merged
    tree via a path where the local self-heal never ran.
    """
    sink_rel = normalize_sinks(root, sinks)
    if not sink_rel:
        raise ResolveError("SINK_RESOLVER_SINKS is not set — nothing to check. Fail-closed.")

    check_cmd = os.environ.get("SINK_RESOLVER_CHECK_CMD", "").strip()
    if check_cmd:
        proc = _run_configured_cmd(root, check_cmd, "SINK_RESOLVER_CHECK_CMD")
        if proc.returncode == 0:
            return True, f"sink(s) fresh — {check_cmd} reports byte-exact in sync."
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, ("the committed sink is STALE (the merged tree carries a generated "
                       "artifact that is not a fresh render of its sources) — "
                       f"{check_cmd} output:\n{detail}")

    # No strong oracle: regenerate, diff, restore.
    print("sink-resolver: SINK_RESOLVER_CHECK_CMD is unset — `check` will regenerate and diff "
          "(set CHECK_CMD for a non-mutating, stronger check).", file=sys.stderr)
    _regenerate(root)
    diff = _git_run(root, "diff", "--exit-code", "--", *sink_rel, check_rc=False)
    fresh = diff.returncode == 0
    _git_run(root, "checkout", "--", *sink_rel, check_rc=False)  # restore (net non-mutating)
    if fresh:
        return True, "sink(s) fresh — a regenerate produced no change from the committed bytes."
    return False, ("the committed sink is STALE — a regenerate from the merged sources differs "
                   f"from the committed bytes:\n{diff.stdout.strip()}")


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def _sinks_from_env() -> list[str]:
    raw = os.environ.get("SINK_RESOLVER_SINKS", "")
    if not raw.strip():
        return []
    parts = raw.split(os.pathsep) if os.pathsep in raw else raw.split()
    return [p.strip() for p in parts if p.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sink-resolver.py",
        description="Deterministic auto-resolution of generated-file merge conflicts: "
                    "resolve / check.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_res = sub.add_parser("resolve", help="Auto-resolve a sink-only conflict; escalate (exit 4) "
                                           "if a non-sink path is in conflict.")
    p_res.add_argument("--root", default=".", metavar="DIR",
                       help="A path inside the repo/worktree (default: cwd).")
    p_res.add_argument("--no-commit", dest="finalize", action="store_false",
                       help="Stage the regenerated sink but do NOT finalize the merge commit "
                            "(e.g. mid-rebase — finish with your own commit).")
    p_res.add_argument("--dry-run", action="store_true",
                       help="Classify the conflict and print the plan; write NOTHING.")

    p_chk = sub.add_parser("check", help="CI backstop: exit 1 if the merged sink is not a fresh "
                                         "render (covers the web-UI-merge path).")
    p_chk.add_argument("--root", default=".", metavar="DIR",
                       help="A path inside the repo/worktree (default: cwd).")

    args = ap.parse_args(argv)
    start = Path(args.root)

    if not _enabled():
        print("sink-resolver: SINK_RESOLVER is off — no-op (resolved/checked nothing).",
              file=sys.stderr)
        return EXIT_OK

    sinks = _sinks_from_env()

    try:
        root = resolve_repo_root(start)
    except ResolveError as exc:
        print(f"sink-resolver: ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.cmd == "resolve":
        try:
            status, resolved = resolve_merge(
                root=root, sinks=sinks, finalize=args.finalize, dry_run=args.dry_run,
            )
        except Escalation as exc:
            print(f"sink-resolver: ESCALATE: {exc}", file=sys.stderr)
            _log_decision("escalate", str(exc).splitlines()[0])
            return EXIT_ESCALATE
        except ResolveError as exc:
            print(f"sink-resolver: ERROR: {exc}", file=sys.stderr)
            _log_decision("error", str(exc).splitlines()[0])
            return EXIT_ERROR
        if status == "clean":
            print("sink-resolver: no merge conflict to resolve (sink untouched).")
        elif status == "would-resolve":
            print(f"DRY-RUN would auto-resolve the sink-only conflict: {', '.join(resolved)}")
        elif status == "resolved-finalized":
            print(f"RESOLVED + finalized the merge: regenerated {', '.join(resolved)}")
        else:  # resolved-staged
            print(f"RESOLVED (staged, not committed): regenerated {', '.join(resolved)}")
        return EXIT_OK

    if args.cmd == "check":
        try:
            fresh, msg = check_fresh(root=root, sinks=sinks)
        except ResolveError as exc:
            print(f"sink-resolver: ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if fresh:
            print(f"CHECK OK: {msg}")
            return EXIT_OK
        print(f"sink-resolver: CHECK FAILED — {msg}", file=sys.stderr)
        return EXIT_ERROR

    ap.error(f"unknown command {args.cmd!r}")  # unreachable (subparser required)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed on our own crash, never silent
        traceback.print_exc(file=sys.stderr)
        print(f"sink-resolver: ERROR (unhandled, fail-closed): {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
