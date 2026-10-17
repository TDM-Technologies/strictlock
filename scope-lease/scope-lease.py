#!/usr/bin/env python3
r"""scope-lease.py — a git-native, zero-service exclusive lock over a path set.

A StrictLock Concurrency module. plan-gate enumerates the paths a unit of work *may*
touch; scope-lease makes that hold **exclusive** across concurrent agents — so N
autonomous agents never edit the same source file at once. It closes the two failures
human supervision used to cover: **deadlock-on-crash** (a crashed holder's scope frees by
deadline, then is reclaimed) and **stale-holder-write** (a holder that lost its lease and
woke up is caught by the fence at the merge gate). No daemon, no server, no shared service —
the only coordination point is git itself.

THE ONE IDEA. A scope (one repo-relative path) is claimed by an atomic `git update-ref`
compare-and-swap on `refs/locks/<sha1(canonical-path)>` — the single real-time consensus
point a branch-per-agent substrate otherwise lacks. The lock ref points at a blob holding
`{lock_id, owner, token, deadline, host, git_dir}`. Lock refs live OFF every branch (under
`refs/locks/*`, never `refs/heads/*`), so they never re-enter the merge path.

Exclusion AT ACQUIRE (with correct repo-relative normalization) is the PRIMARY guard: git
silently auto-merges two edits to the same file on different lines (no conflict, no marker),
so the merge is NOT a reliable collision point for source. The only way to *prevent* that
corruption is to stop both agents holding the same file at once. The per-path monotonic
`token` + ownership `fence-check` are the reclaim/zombie BACKSTOP (an agent that lost its
lease then woke and wrote), not the primary guard.

THREE VERBS.
  acquire      Normalize each path to a canonical repo-relative key; build one lease blob;
               run a single TRANSACTIONAL `git update-ref --stdin` creating every path-ref
               CAS-from-absent (all-or-nothing). A path held by a LIVE holder -> structured
               DENY (first-mover wins, exit 5, writes nothing). A past-deadline holder ->
               reclaimed with token+1 (logged on stderr, never a silent background reaper).
  fence-check  The reclaim/zombie backstop, run at the merge gate: assert this lock still
               holds every one of its path-refs. Any missing or foreign ref -> this lock was
               reclaimed away -> exit 4, write nothing.
  release      Drop only the locks THIS lock_id owns; idempotent.

PATH SOURCE (the seam — where the path set comes from). The primitive takes a path list +
a lock id and does NOT hard-depend on plan-gate. The source is selected by
`SCOPE_LEASE_SOURCE`:
  plan-gate (default)  Read `allowed_paths` (and the `name`/slug as the lock id) from the
                       single `status: active` plan in `SCOPE_LEASE_PLANS_DIR` (or
                       `PLAN_GATE_PLANS_DIR`). Adopters already running plan-gate get the
                       lease against their existing plans for free.
  paths                Take the path set from `--paths`, stdin, or `SCOPE_LEASE_PATHS`; the
                       lock id from `--lock-id` or `SCOPE_LEASE_LOCK_ID`.
  file                 Read `allowed_paths` (+ `name`) from a small own-frontmatter file at
                       `--file` or `SCOPE_LEASE_PATHS_FILE` — same minimal YAML subset.

SAFETY INVARIANTS (binding).
  * `refs/locks/*` coordinates AGENTS only. It must NEVER wall a human's manual merge or
    edits — there is no human-facing lock, no checkout block; `fence-check` exit-4 governs
    the AGENT merge path only. (A `worktree_bypass` equivalent: humans are simply never
    gated by this tool.)
  * Fail-loud single-machine boundary. Acquire-time exclusion is real ONLY within one shared
    `.git` ref store. Every lock blob is stamped with host + git-common-dir; reading a lock
    from a FOREIGN store is a hard stop (exit 6), never a silent downgrade to detect-at-merge.
    The real multi-machine fix (push-CAS to origin) is a later phase.
  * Surface, don't auto-reap. A reclaim of an expired holder is always logged to stderr
    naming who was evicted and why — never a background reaper.

CONFIG (env). See CONFIG.md.
  SCOPE_LEASE=on  ·  SCOPE_LEASE_SOURCE (plan-gate|paths|file)  ·  SCOPE_LEASE_PLANS_DIR  ·
  SCOPE_LEASE_PATHS / --paths / SCOPE_LEASE_PATHS_FILE  ·  SCOPE_LEASE_LOCK_ID  ·
  SCOPE_LEASE_TTL  ·  SCOPE_LEASE_OWNER  ·  SCOPE_LEASE_LOG_DIR.

Usage:
  python3 scope-lease.py acquire     [--root DIR] [--ttl-seconds N] [--deadline ISO8601] \
                                     [--source plan-gate|paths|file] [--paths P ...] \
                                     [--file FILE] [--lock-id ID] [--owner OWNER]
  python3 scope-lease.py fence-check [--root DIR] [<source selectors as above>]
  python3 scope-lease.py release     [--root DIR] [<source selectors as above>]
  python3 scope-lease.py holder      [--root DIR] [--lock-id ID] [--owner OWNER] [--json] PATH ...
                                     (READ-ONLY: 0 = every path free or mine, 5 = at least one
                                      held by someone else; plan-gate's PLAN_GATE_SCOPE_LEASE
                                      calls this. "Mine" = lock id matches --lock-id /
                                      SCOPE_LEASE_LOCK_ID, or owner matches --owner /
                                      SCOPE_LEASE_OWNER / branch / user.email.)

Exit codes: 0 ok; 1 unexpected error (incl. undeterminable owner / no path source);
            2 usage error (argparse);
            4 fence-check lost (a held scope was reclaimed away — "a guard tripped, write
              nothing"); 5 DENY (a scope is held by a live holder — first-mover wins);
            6 machine-boundary fail-loud (a lock from a non-shared ref store).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import unicodedata
from pathlib import Path

# ── enablement ───────────────────────────────────────────────────────────────────────
# Configured entirely by env, no machine defaults. Unlike a PreToolUse gate (which must be
# inert-by-default so an unconfigured harness isn't wedged), scope-lease is invoked
# explicitly by an adopter at session start — so SCOPE_LEASE is an *opt-out* affordance for
# scripts that want a single switch, not a silent kill of an explicit call. We honor it only
# when set to a falsey value; an explicit invocation with SCOPE_LEASE unset still runs.
def _enabled() -> bool:
    val = os.environ.get("SCOPE_LEASE")
    if val is None:
        return True
    return val.strip().lower() in ("on", "1", "true", "yes")


# ── lock refs & defaults ─────────────────────────────────────────────────────────────
LOCKS_REF_PREFIX = "refs/locks/"
DEFAULT_TTL_SECONDS = 1800  # 30 min: long enough to cover a work burst, short enough that a
#                             crashed holder's scope frees by deadline (reclaim is the backstop)


# ── exceptions ───────────────────────────────────────────────────────────────────────
class LeaseDenied(Exception):
    """A scope is held by a LIVE holder — first-mover wins, the loser writes nothing. Carries
    the conflicting path + the holder's lock_id/owner/deadline so the denial NAMES the blocker
    (the structured-DENY contract; never a bare failure)."""

    def __init__(self, path: str, holder: dict | None, detail: str = ""):
        self.path = path
        self.holder = holder or {}
        hid = self.holder.get("lock_id", "?")
        howner = self.holder.get("owner", "?")
        hdl = self.holder.get("deadline", "?")
        msg = (
            f"scope {path!r} is already leased by lock {hid!r} (owner {howner}) until {hdl}"
            + (f" — {detail}" if detail else "")
            + "\nfirst-mover wins; this work claimed nothing and wrote nothing. "
            "Pick a disjoint scope, or wait for the holder's lease to lapse."
        )
        super().__init__(msg)


class MachineBoundaryError(Exception):
    """Fail-loud: a lock blob carries a host/git-common-dir that differs from this process's.
    Acquire-time exclusion is real ONLY when all worktrees share one `.git` ref store; a lock
    from a foreign store means exclusion has SILENTLY degraded to detect-at-merge. We refuse to
    proceed on the strong guarantee we no longer have. The real multi-machine fix is push-CAS to
    origin; until then this is a hard stop, never a silent downgrade."""


class LeaseError(Exception):
    """A scope-lease operation could not complete (e.g. a git command failed, or no path
    source could be resolved). Distinct from DENY/fence-loss (expected governance outcomes)."""


class OwnerDeriveError(Exception):
    """Raised when no owner is supplied and none can be derived from the git context. We refuse
    to silently stamp a meaningless owner — an unresolvable owner is a call routed back to the
    caller (`--owner` / `SCOPE_LEASE_OWNER`), not a guess."""


class PathSourceError(Exception):
    """Raised when the configured path source yields no usable path set / lock id."""


# ── git plumbing ─────────────────────────────────────────────────────────────────────
def _git_run(
    root: Path, *args: str, check_rc: bool = True, input: str | None = None
) -> subprocess.CompletedProcess:
    """Run `git -C root <args>` with signing disabled (no interactive gpg prompt) and path
    quoting off (so ref output is byte-comparable). `input` is fed on stdin (for
    `hash-object --stdin` / `update-ref --stdin`). Raises LeaseError on a non-zero exit when
    check_rc."""
    # Bytes on the wire, not text mode: on Windows a text-mode stdin pipe rewrites "\n" as
    # "\r\n", and `update-ref --stdin` rejects the stray CR ("extra input"). Decode output
    # ourselves with the same universal-newline result text mode would give.
    raw = subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false",
         "-c", "core.quotepath=false", *args],
        capture_output=True, input=None if input is None else input.encode("utf-8"),
    )

    def _text(b: bytes) -> str:
        return b.decode("utf-8", "replace").replace("\r\n", "\n")

    proc = subprocess.CompletedProcess(raw.args, raw.returncode, _text(raw.stdout), _text(raw.stderr))
    if check_rc and proc.returncode != 0:
        raise LeaseError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _resolve_loose(p: Path) -> Path:
    """Resolve symlinks and (on Windows) 8.3 short names in the longest EXISTING
    prefix of `p`, then re-append the not-yet-existing tail. A plain Path.resolve()
    on Windows with Python < 3.10 leaves a path whose last component does not exist
    completely unresolved, so a short-name spelling (RUNNER~1) of a new file would
    key differently from the same file spelled repo-relative. Same result as
    resolve() when the whole path exists."""
    p = Path(os.path.abspath(str(p)))
    tail: list[str] = []
    cur = p
    while True:
        try:
            if cur.exists():
                base = cur.resolve()
                break
        except OSError:
            pass
        if cur.parent == cur:
            return p
        tail.append(cur.name)
        cur = cur.parent
    for part in reversed(tail):
        base = base / part
    return base


def _git_toplevel(root: Path) -> Path:
    return _resolve_loose(Path(_git_run(root, "rev-parse", "--show-toplevel").stdout.strip()))


def _git_config_bool(root: Path, key: str) -> bool:
    """Read a boolean git config value (default False if unset/unparseable)."""
    out = _git_run(root, "config", "--get", key, check_rc=False).stdout.strip().lower()
    return out in ("true", "1", "yes", "on")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: str) -> datetime.datetime | None:
    """Parse an ISO-8601 deadline back to an aware datetime; None if unparseable/empty. A
    missing/garbled deadline is treated as 'no deadline' -> not-live -> reclaimable (fail toward
    freeing a possibly-stuck scope, not toward a permanent wedge)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:  # treat a naive stamp as UTC so comparisons never raise
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# ── single-machine ref-store identity (fail-loud boundary) ───────────────────────────
def _refstore(root: Path) -> dict:
    """Identity of THIS process's ref store: hostname + resolved git-common-dir. All linked
    worktrees on one machine share the common dir, so they agree; a separate clone/machine does
    not. Stamped into every lock blob and asserted on every foreign blob we read."""
    common = Path(_git_run(root, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = Path(root) / common
    return {"host": socket.gethostname(), "git_dir": str(common.resolve())}


def _assert_same_refstore(blob: dict, refstore: dict, path: str) -> None:
    if blob.get("host") != refstore["host"] or blob.get("git_dir") != refstore["git_dir"]:
        raise MachineBoundaryError(
            f"lock on {path!r} was minted by a foreign ref store "
            f"(blob host={blob.get('host')!r} git_dir={blob.get('git_dir')!r}; "
            f"here host={refstore['host']!r} git_dir={refstore['git_dir']!r}). "
            "Cross-machine exclusion is not real on a per-clone ref store — refusing to "
            "acquire/fence/release on a guarantee we don't have (the fix is push-CAS to origin)."
        )


# ── path normalization — the load-bearing same-file guard ────────────────────────────
def _fold_flags(root: Path) -> tuple[bool, bool]:
    """(case_insensitive, normalizes_unicode) for the repo's filesystem, taken from git's OWN
    per-FS auto-detection: `core.ignorecase` (git sets true on a case-insensitive FS, e.g. APFS)
    and `core.precomposeunicode` (true on macOS, where NFD and NFC name the same file). These are
    exactly the signals git uses for its own path identity, so the lock key agrees with git's
    notion of 'the same file' on THIS filesystem — and a case-SENSITIVE FS (typical Linux, the
    autonomous-team trajectory) keeps genuinely-distinct files distinct (no false collision)."""
    return _git_config_bool(root, "core.ignorecase"), _git_config_bool(root, "core.precomposeunicode")


def _normalize_repo_relative(
    root: Path, path: str, top: Path | None = None, fold: tuple[bool, bool] | None = None
) -> str:
    """Canonical repo-relative POSIX key for a scope path. Collapses `.`/`..`, resolves
    absolute<->relative against the git top-level, normalizes separators, drops trailing slash,
    and — gated on the repo filesystem's actual identity semantics (`_fold_flags`) — case-folds
    and/or Unicode-NFC-folds the KEY so two spellings of the SAME file map to ONE key.

    THE load-bearing correctness piece: if `src/Handler.py` and `src/handler.py` (same inode on
    a case-insensitive FS) produced two keys, two agents would both 'cleanly' acquire and git
    would silently merge their different-line edits with no marker — the exact shared-collision-
    key bug a path-list lock must close. The fold is FS-AWARE on purpose: unconditional case-
    folding would instead invent FALSE collisions between two real files on a case-sensitive FS."""
    top = top or _git_toplevel(root)
    p = Path(path)
    if not p.is_absolute():
        p = top / p
    p = _resolve_loose(p)
    ignorecase, precompose = fold if fold is not None else _fold_flags(root)
    try:
        rel = p.relative_to(top).as_posix()
    except ValueError:
        rel = None
        if ignorecase or precompose:
            # On a case-insensitive / Unicode-normalizing filesystem the SAME directory can
            # be spelled `/private/TMP/r` and `/private/tmp/r`; resolve() keeps the spelling it
            # was given, so relative_to() fails and the key would silently become the absolute
            # path (a different key => the lock is missed). Compare prefix parts folded the way
            # the repo's filesystem folds them.
            def _f(s: str) -> str:
                if precompose:
                    s = unicodedata.normalize("NFC", s)
                return s.casefold() if ignorecase else s
            pp, tp = p.parts, top.parts
            if len(pp) >= len(tp) and all(_f(a) == _f(b) for a, b in zip(pp, tp)):
                rel = "/".join(pp[len(tp):])
        if rel is None:
            rel = p.as_posix()  # outside the repo: still canonical, just not repo-relative
    rel = rel or "."
    if precompose:
        rel = unicodedata.normalize("NFC", rel)
    if ignorecase:
        rel = rel.casefold()
    return rel


def _normalize_paths(root: Path, paths: list[str] | None) -> list[str]:
    """Normalize + dedupe + sort a path set into canonical scope keys. The FS-fold flags are
    read ONCE and threaded through, so the per-path loop costs no extra git calls and every path
    keys the same way (absolute-vs-relative, dir-prefix-vs-file, trailing slash all collapse)."""
    if not paths:
        return []
    top = _git_toplevel(root)
    fold = _fold_flags(root)
    keys = {_normalize_repo_relative(root, p, top, fold) for p in paths if str(p).strip()}
    return sorted(keys)


def _lease_ref(norm_path: str) -> str:
    """`refs/locks/<sha1(canonical-path)>`. The path is hashed (not embedded) so any path string
    yields a valid, fixed-width ref name regardless of its characters."""
    return LOCKS_REF_PREFIX + hashlib.sha1(norm_path.encode("utf-8")).hexdigest()


# ── lock blobs ───────────────────────────────────────────────────────────────────────
def _ref_oid(root: Path, ref: str) -> str | None:
    """The exact object id a ref points at, or None if the ref does not exist. `show-ref
    --verify` matches the full ref name only (no short-name DWIM) and exits non-zero if absent."""
    proc = _git_run(root, "show-ref", "--verify", "--hash", ref, check_rc=False)
    oid = proc.stdout.strip()
    return oid or None


def _hash_lease_blob(
    root: Path, *, lock_id: str, owner: str, token: int, deadline: str, refstore: dict,
    acquired: str = ""
) -> str:
    """Write the lock payload as a git blob and return its oid. Deterministic JSON (sorted keys,
    no spaces) so identical logical leases share a blob. CRITICAL ORDERING (the GC race): the blob
    is created with `-w` and then IMMEDIATELY referenced by the caller's update-ref — a ref-pinned
    blob survives `git gc`, a dangling one can be reaped in the sub-second hash->ref window."""
    body = {"lock_id": lock_id, "owner": owner, "token": token, "deadline": deadline,
            "host": refstore["host"], "git_dir": refstore["git_dir"]}
    if acquired:  # when this holder first took the scope (lets `holder` report a lease age)
        body["acquired"] = acquired
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return _git_run(root, "hash-object", "-w", "--stdin", input=payload).stdout.strip()


def _read_lease_blob(root: Path, oid: str) -> dict:
    """Parse a lock blob back to its dict payload."""
    raw = _git_run(root, "cat-file", "-p", oid).stdout
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LeaseError(f"lock blob {oid} is not valid JSON: {exc}") from exc


def _log_reclaim(path: str, holder: dict, token: int, deadline: str) -> None:
    """Surface a reclaim — NEVER a silent background reaper (eviction is explicit and on stderr).
    One line naming who was evicted and why."""
    print(
        f"scope-lease: RECLAIMED stale lock on {path!r} "
        f"(was lock {holder.get('lock_id', '?')!r}, owner {holder.get('owner', '?')}, "
        f"expired {holder.get('deadline', '?')}) -> token {token}, new deadline {deadline}",
        file=sys.stderr,
    )


def _log_decision(log_dir: Path | None, row: dict) -> None:
    """Append one JSON line to the audit log when SCOPE_LEASE_LOG_DIR is set. The append-only
    trail is the compliance exhaust — produced as a byproduct of the lock doing its job. Best-
    effort: a logging failure never changes the lock outcome."""
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": _utcnow().isoformat(), **row}, sort_keys=True) + "\n"
        with (log_dir / "scope-lease.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


# ── the three verbs ──────────────────────────────────────────────────────────────────
def acquire_lease(
    *,
    root: Path,
    lock_id: str,
    owner: str,
    paths: list[str] | None,
    now: datetime.datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    deadline: str | None = None,
    log_dir: Path | None = None,
) -> dict:
    """Transactionally claim a lock ref for EVERY normalized path in `paths`.

      • path free                       -> `create` (CAS-from-absent).
      • path held by a LIVE foreign holder (deadline > now) -> raise LeaseDenied; claim NOTHING.
      • path held by an EXPIRED holder  -> reclaim via `update <new> <exact-stale-oid>`,
                                           minting token+1; logged.
      • path already held by THIS lock  -> re-acquire/extend (token+1, fresh deadline).

    All refs are applied in ONE `git update-ref --stdin` transaction (all-or-nothing): if any
    path is contended at commit time the WHOLE batch aborts — no half-claim orphan ref. Returns
    {deadline, leased: [paths], reclaimed: [(path, holder, token)]}. Empty paths is a no-op
    success (nothing to lease). Raises LeaseDenied (live holder) / MachineBoundaryError (foreign
    ref store, fail-loud).

    Reclaim is DEADLINE-ONLY: the TTL is the liveness signal — short enough that a crashed holder
    frees, long enough to cover a work burst. A reclaim is always surfaced via `_log_reclaim`,
    never a silent background reaper."""
    root = Path(root)
    norm = _normalize_paths(root, paths)
    if not norm:
        return {"deadline": "", "leased": [], "reclaimed": []}
    now = now or _utcnow()
    if deadline is None:
        deadline = (now + datetime.timedelta(seconds=ttl_seconds)).isoformat()
    refstore = _refstore(root)

    # Phase 1 — DECIDE (reads only, no blob writes): per path, resolve verb + token + old-oid.
    # A LIVE foreign holder raises here, before anything is written (no half-claim).
    plan: list[tuple] = []        # (ref, verb, old_oid|None, token, acquired)
    reclaimed: list[tuple] = []
    now_iso = now.isoformat()
    for p in norm:
        ref = _lease_ref(p)
        old = _ref_oid(root, ref)
        if old is None:
            plan.append((ref, "create", None, 1, now_iso))
            continue
        holder = _read_lease_blob(root, old)
        _assert_same_refstore(holder, refstore, p)  # fail loud BEFORE trusting the lock
        held_live = (_parse_iso(holder.get("deadline", "")) or now) > now \
            if holder.get("deadline") else False
        # Ours = same lock id AND same owner identity. A lock id alone is NOT enough: it is
        # visible (it appears in plan-gate's deny message), so matching it must not let a
        # different agent take over, extend or rewrite a LIVE lock. An expired lock is
        # reclaimable by anyone.
        is_ours = holder.get("lock_id") == lock_id and holder.get("owner") == owner
        if held_live and not is_ours:
            raise LeaseDenied(p, holder, detail=(
                "that lock id is held by a different owner" if holder.get("lock_id") == lock_id
                else ""))                           # first-mover wins; write nothing
        token = int(holder.get("token", 0)) + 1     # ours (extend) or expired (reclaim)
        # An extend keeps the original acquire time (age = time since first taken); a
        # reclaim starts a new age.
        acquired = (holder.get("acquired") or now_iso) if is_ours else now_iso
        plan.append((ref, "update", old, token, acquired))
        if not is_ours:
            reclaimed.append((p, holder, token))

    # Phase 2 — MATERIALIZE: hash every blob and run the transaction BACK-TO-BACK, so each blob
    # is ref-pinned within sub-ms of creation (a dangling blob can be reaped by a sibling
    # `git gc --prune=now` in the hash->ref window; pinning immediately closes the GC race).
    txn: list[str] = []
    for ref, verb, old, token, acquired in plan:
        blob = _hash_lease_blob(root, lock_id=lock_id, owner=owner, token=token,
                                deadline=deadline, refstore=refstore, acquired=acquired)
        txn.append(f"create {ref} {blob}" if verb == "create" else f"update {ref} {blob} {old}")

    proc = _git_run(root, "update-ref", "--stdin", input="\n".join(txn) + "\n", check_rc=False)
    if proc.returncode != 0:
        # The pre-check passed but a racer changed a ref between our read and the commit, so the
        # transaction aborted (atomically — nothing was written). Re-read to name the new holder.
        for p in norm:
            cur = _ref_oid(root, _lease_ref(p))
            if cur is not None:
                h = _read_lease_blob(root, cur)
                if h.get("lock_id") != lock_id and (_parse_iso(h.get("deadline", "")) or now) > now:
                    raise LeaseDenied(p, h, detail="lost the acquire race (transaction aborted)")
        raise LeaseError(
            f"lease acquire transaction failed (exit {proc.returncode}) — likely a transient "
            f"ref race or a blob reaped by a concurrent `git gc`; safe to RETRY (nothing was "
            f"written): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    for p, holder, token in reclaimed:
        _log_reclaim(p, holder, token, deadline)
    _log_decision(log_dir, {"event": "acquire", "lock_id": lock_id, "owner": owner,
                            "leased": norm, "reclaimed": [r[0] for r in reclaimed],
                            "reclaimed_detail": [
                                {"path": rp, "was_lock_id": rh.get("lock_id", ""),
                                 "was_owner": rh.get("owner", ""),
                                 "was_deadline": rh.get("deadline", ""), "token": rt}
                                for rp, rh, rt in reclaimed],
                            "deadline": deadline})
    return {"deadline": deadline, "leased": norm, "reclaimed": reclaimed}


def fence_check(
    *, root: Path, lock_id: str, paths: list[str] | None
) -> tuple[bool, list[tuple]]:
    """The reclaim/zombie backstop (runs at the merge gate). For each normalized path: assert a
    lock ref EXISTS and its blob's lock_id == this lock. A missing or foreign lock means this lock
    was reclaimed away while it slept -> it must NOT write. Returns (ok, failures) where failures
    is [(path, reason)]. Ownership-based (not token-based) by design — the token exists for
    reclaim-CAS replay-safety; the fence asks the simpler question 'do I still hold this?'."""
    root = Path(root)
    norm = _normalize_paths(root, paths)
    refstore = _refstore(root)
    failures: list[tuple] = []
    for p in norm:
        oid = _ref_oid(root, _lease_ref(p))
        if oid is None:
            failures.append((p, "lock missing (reclaimed or never held)"))
            continue
        blob = _read_lease_blob(root, oid)
        _assert_same_refstore(blob, refstore, p)  # fail loud on a foreign-store lock
        if blob.get("lock_id") != lock_id:
            failures.append((p, f"lock now held by {blob.get('lock_id')!r}"))
    return (not failures, failures)


def release_lease(
    *, root: Path, lock_id: str, paths: list[str] | None, log_dir: Path | None = None,
    owner: str | None = None,
) -> list[str]:
    """Delete only the locks THIS lock_id owns (blob.lock_id == lock_id), CAS-pinned to the exact
    oid so a concurrent reclaim is never clobbered. Idempotent: an absent or foreign lock is a
    no-op success. Returns the list of released paths.

    Each lock is released in its OWN transaction (not one batch): if a racer reclaims one of this
    lock's paths mid-release, that one CAS-delete fails harmlessly while every OTHER owned lock
    still drops — a batch would abort wholesale and strand the rest until their deadline."""
    root = Path(root)
    norm = _normalize_paths(root, paths)
    refstore = _refstore(root)
    released: list[str] = []
    # Pass 1 (decide, change nothing): read every path and fail loud on a foreign-store lock
    # BEFORE any delete, like acquire — a raise mid-loop would strand earlier deletions
    # unlogged and leave later owned locks held. Foreign STORE is not a silent no-op.
    owned: list[tuple[str, str, str]] = []
    lock_owner = ""  # the owner stamped in the lock blob (one lock_id => one acquiring owner)
    for p in norm:
        ref = _lease_ref(p)
        oid = _ref_oid(root, ref)
        if oid is None:
            continue
        blob = _read_lease_blob(root, oid)
        _assert_same_refstore(blob, refstore, p)
        if blob.get("lock_id") != lock_id:
            continue  # never release a lock we don't own
        live = bool(blob.get("deadline")) and \
            (_parse_iso(blob.get("deadline", "")) or _utcnow()) > _utcnow()
        if owner is not None and live and blob.get("owner") != owner:
            # Same lock id, different owner, still live: a takeover attempt, not a release.
            raise LeaseDenied(p, blob, detail="that lock id is held by a different owner")
        lock_owner = lock_owner or str(blob.get("owner") or "")
        owned.append((p, ref, oid))
    # Pass 2: delete.
    for p, ref, oid in owned:
        # CAS-pinned delete; check_rc=False so a concurrent reclaim of THIS path (oid moved)
        # fails this one delete without aborting the others.
        proc = _git_run(root, "update-ref", "-d", ref, oid, check_rc=False)
        if proc.returncode == 0:
            released.append(p)
    if released:
        _log_decision(log_dir, {"event": "release", "lock_id": lock_id,
                                "owner": lock_owner, "released_by": owner or "",
                                "released": released})
    return released


def holder_status(
    *, root: Path, paths: list[str], lock_id: str | None = None,
    owner: str | None = None, now: datetime.datetime | None = None,
    owner_root: Path | str | None = None, no_owner: bool = False,
) -> list[dict]:
    """READ-ONLY: who holds each path right now. One dict per input path (input order):

        {"path": <as given>, "key": <canonical>, "status": "free" | "mine" | "held", ...}

    `free`  no lock ref, or the lock's deadline has passed (an expired lock is reclaimable by
            the next `acquire`; the dict then also carries "expired": true plus the old holder).
    `mine`  a live lock whose lock_id == `lock_id` (when given) OR whose owner == `owner`.
            When `owner` is not given it is derived lazily (a path nobody holds never needs
            one) by derive_owner: SCOPE_LEASE_OWNER, else the git branch of `owner_root`
            (default `root`). `owner_root` is the AGENT's own worktree, which may differ
            from the repo the looked-up path lives in. If no identity can be derived
            (detached HEAD, no env), or `no_owner` is set, nothing is mine.
    `held`  a live lock held by someone else. Carries lock_id, owner, token, deadline,
            acquired and age_seconds (None for a lock minted before `acquired` existed).

    Canonicalization is the one in this file (_normalize_paths + _lease_ref); nothing is
    reimplemented by callers. Writes nothing. Raises MachineBoundaryError for a lock minted by
    a foreign ref store, LeaseError for a git failure, OwnerDeriveError when an owner is needed
    but cannot be derived."""
    root = Path(root)
    now = now or _utcnow()
    top = _git_toplevel(root)
    fold = _fold_flags(root)
    keys = [_normalize_repo_relative(root, p, top, fold) for p in paths]
    # One for-each-ref instead of one show-ref per path.
    listing = _git_run(root, "for-each-ref", "--format=%(objectname) %(refname)",
                       LOCKS_REF_PREFIX).stdout
    held_refs: dict[str, str] = {}
    for ln in listing.splitlines():
        oid, _, ref = ln.strip().partition(" ")
        if oid and ref:
            held_refs[ref] = oid
    refstore: dict | None = None
    my_owner = owner
    owner_resolved = owner is not None
    out: list[dict] = []
    for raw, key in zip(paths, keys):
        entry: dict = {"path": raw, "key": key, "status": "free"}
        oid = held_refs.get(_lease_ref(key))
        if oid is None:
            out.append(entry)
            continue
        blob = _read_lease_blob(root, oid)
        if refstore is None:
            refstore = _refstore(root)
        _assert_same_refstore(blob, refstore, key)
        deadline = blob.get("deadline", "")
        live = bool(deadline) and (_parse_iso(deadline) or now) > now
        acquired = _parse_iso(blob.get("acquired", ""))
        entry.update({
            "lock_id": blob.get("lock_id", ""), "owner": blob.get("owner", ""),
            "token": blob.get("token", 0), "deadline": deadline,
            "acquired": blob.get("acquired", ""),
            "age_seconds": (int((now - acquired).total_seconds()) if acquired else None),
        })
        if not live:
            entry["expired"] = True
            out.append(entry)
            continue
        if lock_id and blob.get("lock_id") == lock_id:
            entry["status"] = "mine"
        else:
            if my_owner is None and not owner_resolved:
                owner_resolved = True
                if not no_owner:
                    try:
                        my_owner = derive_owner(Path(owner_root) if owner_root else root)
                    except OwnerDeriveError:
                        my_owner = None  # no identity: no lease is mine
            entry["status"] = ("mine" if my_owner is not None
                               and blob.get("owner") == my_owner else "held")
        out.append(entry)
    return out


# ── owner resolution ─────────────────────────────────────────────────────────────────
def derive_owner(root: Path) -> str:
    """Owner id, resolved in order: SCOPE_LEASE_OWNER env -> the git branch of `root`.
    Raises OwnerDeriveError if both fail (detached HEAD and no env) — we refuse to silently
    stamp a meaningless owner; an unresolvable owner is a call routed back to the caller
    (`--owner`), not a stub. There is no user.email fallback: an email names a person, not an
    agent, and would let every agent of one user count as the same owner. The
    1-worktree:1-branch model makes the branch a stable owner; two agents on the SAME branch
    count as one agent (give each its own SCOPE_LEASE_OWNER to tell them apart)."""
    env_owner = os.environ.get("SCOPE_LEASE_OWNER", "").strip()
    if env_owner:
        return env_owner
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
    except OSError as exc:
        raise OwnerDeriveError(f"could not run git to derive owner: {exc}") from exc
    branch = proc.stdout.strip()
    if proc.returncode == 0 and branch and branch != "HEAD":
        return branch
    raise OwnerDeriveError(
        f"could not derive owner at {root} (no SCOPE_LEASE_OWNER, and no branch: detached "
        "HEAD or not a repo) — pass --owner / set SCOPE_LEASE_OWNER explicitly"
    )


# ── path-source adapters (the seam) ──────────────────────────────────────────────────
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _slugify(name: str) -> str:
    """Lowercased, hyphen-collapsed slug for a plan name -> a stable lock id."""
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s or "lock"


def parse_frontmatter(text: str) -> dict:
    """Tiny YAML subset (top-level scalars + list-of-strings under a key) — byte-compatible with
    plan-gate's parser, so a plan-gate plan file reads identically here. Inline ` # comment` is
    stripped from scalars and list entries (the `#` must be whitespace-preceded, per YAML);
    `key: []` and a bare key both read as a real empty list."""
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
                current_key = key
                result[key] = []
            else:
                result[key] = value
                current_key = None
    return result


def _strip_scalar(raw_val: str) -> str:
    """Strip surrounding quotes and a trailing inline comment from a YAML scalar — same
    conservative rules as plan-gate: a `#` preceded by whitespace ends an unquoted value; a `#`
    embedded in a token (e.g. `C:\\foo#bar.md`) or inside quotes is preserved."""
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


def _frontmatter_paths_and_id(text: str) -> tuple[list[str], str | None]:
    """Pull (allowed_paths, lock_id) out of a plan/own-frontmatter file. lock_id comes from the
    plan `name` (slugified) if present; the caller may override."""
    fm = parse_frontmatter(text)
    paths = [str(p) for p in (fm.get("allowed_paths") or []) if str(p).strip()]
    name = fm.get("name")
    lock_id = _slugify(str(name)) if isinstance(name, str) and name.strip() else None
    return paths, lock_id


def _plans_dir() -> Path | None:
    """The plan-gate plans directory: SCOPE_LEASE_PLANS_DIR, then PLAN_GATE_PLANS_DIR (so an
    adopter already running plan-gate needs no extra config)."""
    for var in ("SCOPE_LEASE_PLANS_DIR", "PLAN_GATE_PLANS_DIR"):
        val = os.environ.get(var, "").strip()
        if val:
            return Path(val).expanduser()
    return None


def _resolve_from_plan_gate() -> tuple[list[str], str | None]:
    """Default adapter: read allowed_paths + name from the single `status: active` plan in the
    plans dir. Mirrors plan-gate's own active-plan selection (one active plan at a time)."""
    plans_dir = _plans_dir()
    if plans_dir is None:
        raise PathSourceError(
            "source 'plan-gate' needs SCOPE_LEASE_PLANS_DIR (or PLAN_GATE_PLANS_DIR) set to the "
            "plans directory — none found."
        )
    if not plans_dir.is_dir():
        raise PathSourceError(f"plans dir {plans_dir} does not exist or is not a directory.")
    actives: list[tuple[Path, list[str], str | None]] = []
    for md in sorted(plans_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if str(fm.get("status", "")).strip().lower() != "active":
            continue
        paths, lid = _frontmatter_paths_and_id(text)
        actives.append((md, paths, lid or _slugify(md.stem)))
    if not actives:
        raise PathSourceError(
            f"no plan with `status: active` in {plans_dir} — author one (see SCHEMA.md), or use "
            "--source paths / --source file."
        )
    if len(actives) > 1:
        names = ", ".join(str(p[0].name) for p in actives)
        raise PathSourceError(
            f"more than one `status: active` plan in {plans_dir} ({names}) — exactly one may be "
            "active at a time. Archive the others."
        )
    _md, paths, lid = actives[0]
    return paths, lid


def _resolve_from_file(file_path: str) -> tuple[list[str], str | None]:
    """Standalone adapter: read allowed_paths + name from an own-frontmatter file."""
    fp = Path(file_path).expanduser()
    if not fp.is_file():
        raise PathSourceError(f"path source file {fp} not found.")
    return _frontmatter_paths_and_id(fp.read_text(encoding="utf-8", errors="replace"))


def _resolve_from_paths(cli_paths: list[str] | None) -> tuple[list[str], str | None]:
    """Standalone adapter: path set from --paths, else SCOPE_LEASE_PATHS (os.pathsep- or
    whitespace-separated), else stdin (one path per line). lock id comes from --lock-id /
    SCOPE_LEASE_LOCK_ID at the caller layer."""
    if cli_paths:
        return [p for p in cli_paths if p.strip()], None
    env = os.environ.get("SCOPE_LEASE_PATHS", "").strip()
    if env:
        parts = env.split(os.pathsep) if os.pathsep in env else env.split()
        return [p for p in (s.strip() for s in parts) if p], None
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        if lines:
            return lines, None
    raise PathSourceError(
        "source 'paths' found no paths — pass --paths, set SCOPE_LEASE_PATHS, or pipe one path "
        "per line on stdin."
    )


def resolve_path_source(
    *, source: str, cli_paths: list[str] | None, file_path: str | None, cli_lock_id: str | None
) -> tuple[list[str], str]:
    """Resolve (paths, lock_id) from the selected source. lock_id precedence: --lock-id /
    SCOPE_LEASE_LOCK_ID override > the source's own id (plan name / file name) > error. Raises
    PathSourceError on an unresolvable source or a missing lock id."""
    if source == "plan-gate":
        paths, src_id = _resolve_from_plan_gate()
    elif source == "file":
        fp = file_path or os.environ.get("SCOPE_LEASE_PATHS_FILE", "").strip()
        if not fp:
            raise PathSourceError("source 'file' needs --file or SCOPE_LEASE_PATHS_FILE.")
        paths, src_id = _resolve_from_file(fp)
    elif source == "paths":
        paths, src_id = _resolve_from_paths(cli_paths)
    else:
        raise PathSourceError(f"unknown source {source!r} (expected plan-gate|paths|file).")

    lock_id = (cli_lock_id or os.environ.get("SCOPE_LEASE_LOCK_ID", "").strip() or src_id or "")
    lock_id = lock_id.strip()
    if not lock_id:
        raise PathSourceError(
            "no lock id — the source provided none (no plan `name`), so pass --lock-id or set "
            "SCOPE_LEASE_LOCK_ID. The lock id is the identity that holds the scope; it must be "
            "stable across acquire / fence-check / release."
        )
    return paths, lock_id


# ── CLI ──────────────────────────────────────────────────────────────────────────────
def _default_source() -> str:
    val = os.environ.get("SCOPE_LEASE_SOURCE", "").strip().lower()
    return val if val in ("plan-gate", "paths", "file") else "plan-gate"


def _log_dir() -> Path | None:
    val = os.environ.get("SCOPE_LEASE_LOG_DIR", "").strip()
    return Path(val).expanduser() if val else None


def _ttl_seconds(cli_ttl: int | None) -> int:
    if cli_ttl is not None:
        return cli_ttl
    env = os.environ.get("SCOPE_LEASE_TTL", "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_TTL_SECONDS


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default=".", metavar="DIR",
                   help="Repo / worktree root the lock store lives in (default: cwd).")
    p.add_argument("--source", default=None, choices=("plan-gate", "paths", "file"),
                   help="Path source (default: $SCOPE_LEASE_SOURCE, else plan-gate).")
    p.add_argument("--paths", nargs="*", default=None, metavar="PATH",
                   help="Path set for --source paths (else SCOPE_LEASE_PATHS / stdin).")
    p.add_argument("--file", default=None, metavar="FILE",
                   help="Own-frontmatter file for --source file (else SCOPE_LEASE_PATHS_FILE).")
    p.add_argument("--lock-id", default=None, metavar="ID",
                   help="Override the lock id (else the plan name slug, else SCOPE_LEASE_LOCK_ID).")
    p.add_argument("--owner", default=None, metavar="OWNER",
                   help="Lock owner (else SCOPE_LEASE_OWNER, git branch, or user.email).")


def _fmt_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 120:
        return f"{max(seconds, 0)}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _cmd_holder(args, root: Path) -> int:
    lock_id = (args.lock_id or os.environ.get("SCOPE_LEASE_LOCK_ID", "")).strip() or None
    owner = (args.owner or "").strip() or None
    if owner is None:
        env_owner = os.environ.get("SCOPE_LEASE_OWNER", "").strip()
        owner = env_owner or None  # else derived lazily, only if some live lock is found
    paths = list(args.paths or [])
    if args.paths_from:
        try:
            if args.paths_from == "-":
                data = sys.stdin.buffer.read()
            else:
                with open(args.paths_from, "rb") as fh:
                    data = fh.read()
        except OSError as exc:
            print(f"scope-lease: ERROR: cannot read paths: {exc}", file=sys.stderr)
            return 1
        paths += [os.fsdecode(b) for b in data.split(b"\0") if b]
    if not paths:
        print("scope-lease: ERROR: holder needs at least one path (arguments or "
              "--paths-from).", file=sys.stderr)
        return 2
    try:
        res = holder_status(root=root, paths=paths, lock_id=lock_id, owner=owner,
                            owner_root=args.owner_root or None, no_owner=args.no_owner)
    except MachineBoundaryError as exc:
        print(f"scope-lease: FAIL-LOUD: {exc}", file=sys.stderr)
        return 6
    except (LeaseError, OwnerDeriveError) as exc:
        print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
        return 1
    held = [r for r in res if r["status"] == "held"]
    for r in res:
        if args.json:
            print(json.dumps(r, sort_keys=True))
        elif r["status"] == "held":
            print(f"HELD {r['path']}: lock {r['lock_id']!r} owner {r['owner']} "
                  f"age {_fmt_age(r['age_seconds'])} until {r['deadline']}")
        else:
            print(f"{r['status'].upper()} {r['path']}"
                  + (" (lease expired)" if r.get("expired") else ""))
    if held:
        print(f"scope-lease: {len(held)} of {len(res)} path(s) held by someone else.",
              file=sys.stderr)
        return 5
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scope-lease.py",
        description="git-native exclusive lock over a path set: acquire / fence-check / release.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_acq = sub.add_parser("acquire", help="Claim the path set; DENY (exit 5) if any is held.")
    _add_source_args(p_acq)
    p_acq.add_argument("--ttl-seconds", type=int, default=None, metavar="N",
                       help=f"Lease lifetime (default: $SCOPE_LEASE_TTL, else {DEFAULT_TTL_SECONDS}).")
    p_acq.add_argument("--deadline", default=None, metavar="ISO8601",
                       help="Pin the exact expiry (mainly for deterministic tests).")

    p_fc = sub.add_parser("fence-check", help="Assert this lock still holds every path (0 holds, 4 lost).")
    _add_source_args(p_fc)

    p_rel = sub.add_parser("release", help="Drop the locks this lock_id owns (idempotent).")
    _add_source_args(p_rel)

    p_hold = sub.add_parser(
        "holder",
        help="Read-only: who holds each path (0 all free/mine, 5 any held by someone else).")
    p_hold.add_argument("paths", nargs="*", metavar="PATH",
                        help="Paths to look up (absolute, or relative to the repo top-level).")
    p_hold.add_argument("--paths-from", default=None, metavar="FILE|-",
                        help="Also read NUL-separated paths from FILE, or stdin with '-' "
                             "(no argv length limit; handles newlines in names).")
    p_hold.add_argument("--owner-root", default=None, metavar="DIR",
                        help="Derive MY identity (SCOPE_LEASE_OWNER, else the git branch) from "
                             "this worktree instead of --root: the AGENT's own checkout, which "
                             "need not be the repo the looked-up paths live in.")
    p_hold.add_argument("--no-owner", action="store_true",
                        help="I have no identity: no lease is mine (except by lock id).")
    p_hold.add_argument("--root", default=".", metavar="DIR",
                        help="Repo / worktree the lock store lives in (default: cwd).")
    p_hold.add_argument("--lock-id", default=None, metavar="ID",
                        help="My lock id (else SCOPE_LEASE_LOCK_ID). A lock with this id is mine.")
    p_hold.add_argument("--owner", default=None, metavar="OWNER",
                        help="My owner (else SCOPE_LEASE_OWNER, else the git branch). "
                             "A lock stamped with this owner is mine.")
    p_hold.add_argument("--json", action="store_true",
                        help="One JSON object per path on stdout (machine-readable).")

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "holder":
        # A read-only query: it ignores the SCOPE_LEASE on/off switch (answering "free" because
        # the switch is off would be a wrong answer, not a no-op) and needs no path source.
        return _cmd_holder(args, root)

    if not _enabled():
        print("scope-lease: SCOPE_LEASE is off — no-op (nothing claimed/checked/released).",
              file=sys.stderr)
        return 0

    source = args.source or _default_source()
    try:
        paths, lock_id = resolve_path_source(
            source=source, cli_paths=args.paths, file_path=args.file, cli_lock_id=args.lock_id,
        )
        owner = args.owner or derive_owner(root)
    except PathSourceError as exc:
        print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
        return 1
    except OwnerDeriveError as exc:
        print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
        return 1

    log_dir = _log_dir()

    if args.cmd == "acquire":
        try:
            res = acquire_lease(
                root=root, lock_id=lock_id, owner=owner, paths=paths,
                ttl_seconds=_ttl_seconds(args.ttl_seconds), deadline=args.deadline,
                log_dir=log_dir,
            )
        except MachineBoundaryError as exc:
            print(f"scope-lease: FAIL-LOUD: {exc}", file=sys.stderr)
            return 6
        except LeaseDenied as exc:
            print(f"scope-lease: DENIED: {exc}", file=sys.stderr)
            return 5
        except LeaseError as exc:
            print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
            return 1
        if not res["leased"]:
            print(f"scope-lease: lock {lock_id!r} resolved no paths — nothing to claim.")
        else:
            print(f"LEASED: lock {lock_id!r} (owner {owner}) holds {len(res['leased'])} scope(s) "
                  f"until {res['deadline']}"
                  + (f" (reclaimed {len(res['reclaimed'])})" if res["reclaimed"] else ""))
        return 0

    if args.cmd == "fence-check":
        try:
            ok, failures = fence_check(root=root, lock_id=lock_id, paths=paths)
        except MachineBoundaryError as exc:
            print(f"scope-lease: FAIL-LOUD: {exc}", file=sys.stderr)
            return 6
        except LeaseError as exc:
            print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
            return 1
        if ok:
            print(f"FENCE OK: lock {lock_id!r} still holds all {len(paths)} scope(s).")
            return 0
        print("scope-lease: FENCE LOST — this lock was reclaimed away, write nothing:",
              file=sys.stderr)
        for p, reason in failures:
            print(f"  {p}: {reason}", file=sys.stderr)
        return 4

    if args.cmd == "release":
        try:
            released = release_lease(root=root, lock_id=lock_id, paths=paths, log_dir=log_dir,
                                     owner=owner)
        except MachineBoundaryError as exc:
            print(f"scope-lease: FAIL-LOUD: {exc}", file=sys.stderr)
            return 6
        except LeaseDenied as exc:
            print(f"scope-lease: DENIED: {exc}", file=sys.stderr)
            return 5
        except LeaseError as exc:
            print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"RELEASED: {len(released)} scope(s) for lock {lock_id!r}.")
        return 0

    ap.error(f"unknown command {args.cmd!r}")  # unreachable (subparser required)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any failure non-silently
        print(f"scope-lease: ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
