#!/usr/bin/env python3
r"""Standalone acceptance suite for scope-lease.

Stdlib `unittest`, no third-party dependencies. Every test builds a REAL git repo in a
tempdir and drives the lock through `git update-ref` CAS for real — never a mock, never a
smoke check. The acceptance tests (a)-(h) cover the binding behaviors against the
path-source shape:

  (a) overlapping scopes -> exactly one HELD, the other a structured DENY (exit 5)
  (b) a back-dated deadline is reclaimable, minting token+1 (logged, not silent)
  (c) a reclaimed-away lock -> fence-check exit 4 (write nothing)
  (d) release is idempotent (release twice, foreign lock left alone)
  (e) fence-check holds while the lock is live; the held path is real
  (f) the NORMALIZATION crux: absolute-vs-relative, dir-prefix-vs-file, trailing slash all
      collide on ONE ref key (the same-file double-grant guard)
  (g) txn atomicity: a partial contention leaves NO half-claim orphan ref
  (h) GC race: the lease blob is ref-pinned and survives `git gc --prune=now`
  (i) the post-build FS-fold fix: a case-insensitive / unicode-normalizing FS folds two
      spellings of the same file onto one key (gated on git's core.ignorecase /
      core.precomposeunicode), while a case-sensitive FS keeps distinct files distinct.

Plus the CLI / path-source seam: the plan-gate adapter (default), the --paths / file
fallbacks, the env owner, machine-boundary fail-loud, and the exit-code contract.

Run:  python3 scope-lease/tests/test_scope_lease.py
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scope-lease.py"

# Import the hyphen-named script as a module (can't `import scope-lease`).
_spec = importlib.util.spec_from_file_location("scope_lease", SCRIPT)
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


# ── helpers ──────────────────────────────────────────────────────────────────────────
def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc


def make_repo(tmp: Path) -> Path:
    """A real git repo with one commit (so HEAD and a branch exist)."""
    root = tmp / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tester@example.com")
    _git(root, "config", "user.name", "Tester")
    _git(root, "checkout", "-q", "-b", "work-branch")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def lock_refs(root: Path) -> list[str]:
    """Every refs/locks/* ref currently present (the orphan-detector for txn atomicity)."""
    out = _git(root, "for-each-ref", "--format=%(refname)", "refs/locks/", check=False).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def past() -> str:
    return (sl._utcnow() - datetime.timedelta(hours=1)).isoformat()


def future() -> str:
    return (sl._utcnow() + datetime.timedelta(hours=1)).isoformat()


# ── (a) overlapping scopes -> exactly one HELD, the other a structured DENY ───────────
class OverlapDenyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_acquire_on_overlap_is_denied_and_writes_nothing(self):
        res = sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                               paths=["src/app.py", "src/util.py"], deadline=future())
        self.assertEqual(set(res["leased"]), {"src/app.py", "src/util.py"})
        refs_after_first = set(lock_refs(self.root))
        self.assertEqual(len(refs_after_first), 2)

        with self.assertRaises(sl.LeaseDenied) as ctx:
            sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                             paths=["src/util.py", "src/new.py"], deadline=future())
        # The DENY names the contended path + the live holder (structured, not bare).
        self.assertEqual(ctx.exception.path, "src/util.py")
        self.assertEqual(ctx.exception.holder.get("lock_id"), "lock-a")
        self.assertIn("lock-a", str(ctx.exception))
        # Loser wrote NOTHING — no new ref for its disjoint path src/new.py.
        self.assertEqual(set(lock_refs(self.root)), refs_after_first,
                         "denied acquire must leave the ref store byte-identical")

    def test_disjoint_scopes_both_win(self):
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py"], deadline=future())
        res = sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                               paths=["src/b.py"], deadline=future())
        self.assertEqual(res["leased"], ["src/b.py"])
        self.assertEqual(len(lock_refs(self.root)), 2)


# ── (b) back-dated deadline is reclaimable with token+1 ───────────────────────────────
class ReclaimTokenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _token_for(self, path: str) -> int:
        ref = sl._lease_ref(sl._normalize_paths(self.root, [path])[0])
        oid = sl._ref_oid(self.root, ref)
        return int(sl._read_lease_blob(self.root, oid)["token"])

    def test_expired_holder_is_reclaimed_with_incremented_token(self):
        sl.acquire_lease(root=self.root, lock_id="stale", owner="ghost",
                         paths=["src/x.py"], deadline=past())
        self.assertEqual(self._token_for("src/x.py"), 1)

        res = sl.acquire_lease(root=self.root, lock_id="fresh", owner="alice",
                               paths=["src/x.py"], deadline=future())
        self.assertEqual([r[0] for r in res["reclaimed"]], ["src/x.py"])
        # token+1, and the lock now belongs to the reclaimer.
        self.assertEqual(self._token_for("src/x.py"), 2)
        ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/x.py"])[0])
        blob = sl._read_lease_blob(self.root, sl._ref_oid(self.root, ref))
        self.assertEqual(blob["lock_id"], "fresh")

    def test_live_holder_is_not_reclaimed(self):
        sl.acquire_lease(root=self.root, lock_id="live", owner="alice",
                         paths=["src/x.py"], deadline=future())
        with self.assertRaises(sl.LeaseDenied):
            sl.acquire_lease(root=self.root, lock_id="other", owner="bob",
                             paths=["src/x.py"], deadline=future())


# ── (c) reclaimed-away -> fence-check exit 4 ; (e) fence holds while live ──────────────
class FenceCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_fence_holds_while_live(self):
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py", "src/b.py"], deadline=future())
        ok, failures = sl.fence_check(root=self.root, lock_id="lock-a",
                                      paths=["src/a.py", "src/b.py"])
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_fence_fails_after_reclaim(self):
        # holder acquires, expires, gets reclaimed by another lock -> original must fence-fail.
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py"], deadline=past())
        sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                         paths=["src/a.py"], deadline=future())
        ok, failures = sl.fence_check(root=self.root, lock_id="lock-a", paths=["src/a.py"])
        self.assertFalse(ok)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], "src/a.py")

    def test_fence_fails_when_lock_missing(self):
        ok, failures = sl.fence_check(root=self.root, lock_id="never-held",
                                      paths=["src/ghost.py"])
        self.assertFalse(ok)
        self.assertIn("missing", failures[0][1])

    def test_cli_fence_check_exit_codes(self):
        # exit 0 while this lock holds; exit 4 once it's been reclaimed away.
        plans = _write_plan(self.root, "fence-plan", ["src/c.py"])
        env = _cli_env(self.root, plans)
        self.assertEqual(_run_cli(["acquire", "--root", str(self.root), "--deadline", future()],
                                  env), 0)
        self.assertEqual(_run_cli(["fence-check", "--root", str(self.root)], env), 0)
        # Steal the scope: re-stamp our own lock as EXPIRED, then let a foreign live holder
        # reclaim it. The CLI fence-check (lock id 'fence-plan' from the plan name) must now fail.
        sl.acquire_lease(root=self.root, lock_id="fence-plan", owner="me",
                         paths=["src/c.py"], deadline=past())   # our own holder, now expired
        sl.acquire_lease(root=self.root, lock_id="thief", owner="bob",
                         paths=["src/c.py"], deadline=future())  # reclaimed away from us
        self.assertEqual(_run_cli(["fence-check", "--root", str(self.root)], env), 4)


# ── (d) release idempotent ────────────────────────────────────────────────────────────
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_release_then_release_again(self):
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py", "src/b.py"], deadline=future())
        first = sl.release_lease(root=self.root, lock_id="lock-a",
                                 paths=["src/a.py", "src/b.py"])
        self.assertEqual(set(first), {"src/a.py", "src/b.py"})
        self.assertEqual(lock_refs(self.root), [])
        # second release is a clean no-op
        second = sl.release_lease(root=self.root, lock_id="lock-a",
                                  paths=["src/a.py", "src/b.py"])
        self.assertEqual(second, [])

    def test_release_never_drops_a_foreign_lock(self):
        sl.acquire_lease(root=self.root, lock_id="owner-a", owner="alice",
                         paths=["src/shared.py"], deadline=future())
        released = sl.release_lease(root=self.root, lock_id="someone-else",
                                    paths=["src/shared.py"])
        self.assertEqual(released, [])
        self.assertEqual(len(lock_refs(self.root)), 1, "foreign lock must survive")


# ── (f) normalization crux — many spellings, one key ──────────────────────────────────
class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_absolute_relative_dotslash_trailing_slash_all_one_key(self):
        top = sl._git_toplevel(self.root)
        spellings = [
            "src/app.py",
            "./src/app.py",
            "src/../src/app.py",
            str(top / "src" / "app.py"),        # absolute
            "src/app.py/",                       # trailing slash (rstripped by .resolve())
        ]
        keys = {sl._normalize_repo_relative(self.root, s, top, (False, False)) for s in spellings}
        self.assertEqual(keys, {"src/app.py"}, f"all spellings must fold to one key, got {keys}")

    def test_two_spellings_collide_at_acquire(self):
        # lock-a claims the relative spelling; lock-b claims the ABSOLUTE spelling of the same
        # file -> must be denied (same ref key), not a silent second grant.
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/app.py"], deadline=future())
        top = sl._git_toplevel(self.root)
        with self.assertRaises(sl.LeaseDenied):
            sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                             paths=[str(top / "src" / "app.py")], deadline=future())
        self.assertEqual(len(lock_refs(self.root)), 1,
                         "the same file under two spellings must be exactly one ref")

    def test_dir_prefix_does_not_collide_with_file(self):
        # A directory entry `src/` and a file `src/app.py` are DIFFERENT scopes -> two keys.
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/"], deadline=future())
        res = sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                               paths=["src/app.py"], deadline=future())
        self.assertEqual(res["leased"], ["src/app.py"])
        self.assertEqual(len(lock_refs(self.root)), 2)


# ── (g) transaction atomicity — no half-claim orphan ──────────────────────────────────
class AtomicityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_partial_contention_leaves_no_orphan_ref(self):
        # lock-a holds src/b.py (live). lock-b asks for [src/a.py (free), src/b.py (held)].
        # The whole batch must abort: src/a.py must NOT end up half-claimed.
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/b.py"], deadline=future())
        before = set(lock_refs(self.root))
        with self.assertRaises(sl.LeaseDenied):
            sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                             paths=["src/a.py", "src/b.py"], deadline=future())
        after = set(lock_refs(self.root))
        self.assertEqual(before, after, "no half-claim: src/a.py must not have been created")
        a_ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/a.py"])[0])
        self.assertIsNone(sl._ref_oid(self.root, a_ref))

    def test_commit_race_abort_is_all_or_nothing_and_names_the_racer(self):
        # The HARDER atomicity path: lock-b's Phase-1 DECIDE sees BOTH paths free, but a racer
        # (lock-a) claims src/b.py in the hash->update-ref window. The `create` CAS for src/b.py
        # then fails, so the whole transaction aborts (lines ~416-430): src/a.py must NOT be
        # half-claimed, and the re-read must raise a structured DENY naming the racer.
        # The race is injected deterministically by hooking the per-blob hash (the only code that
        # runs between DECIDE and the commit) — same probe the adversarial verify used by hand.
        orig_hash = sl._hash_lease_blob
        state = {"raced": False}

        def racing_hash(*args, **kwargs):
            if not state["raced"]:
                state["raced"] = True
                sl._hash_lease_blob = orig_hash          # restore before the racer's own acquire
                sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                                 paths=["src/b.py"], deadline=future())
            return orig_hash(*args, **kwargs)

        before = set(lock_refs(self.root))               # empty
        sl._hash_lease_blob = racing_hash
        try:
            with self.assertRaises(sl.LeaseDenied) as ctx:
                sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                                 paths=["src/a.py", "src/b.py"], deadline=future())
        finally:
            sl._hash_lease_blob = orig_hash
        self.assertTrue(state["raced"], "the racer must fire in the DECIDE->commit window")
        self.assertEqual(ctx.exception.path, "src/b.py")
        self.assertEqual(ctx.exception.holder.get("lock_id"), "lock-a")
        # All-or-nothing: ONLY the racer's single ref exists; lock-b half-claimed nothing.
        b_ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/b.py"])[0])
        self.assertEqual(set(lock_refs(self.root)) - before, {b_ref})
        a_ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/a.py"])[0])
        self.assertIsNone(sl._ref_oid(self.root, a_ref),
                          "the aborted txn must leave src/a.py un-claimed")


# ── (h) GC race — the blob is ref-pinned, survives prune ──────────────────────────────
class GCRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_lease_blob_survives_aggressive_gc(self):
        res = sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                               paths=["src/a.py"], deadline=future())
        # Force the most aggressive prune git offers — a dangling (un-pinned) blob would die here.
        _git(self.root, "gc", "--prune=now")
        # The lock ref and its blob must both still resolve.
        ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/a.py"])[0])
        oid = sl._ref_oid(self.root, ref)
        self.assertIsNotNone(oid, "lock ref must survive gc")
        blob = sl._read_lease_blob(self.root, oid)
        self.assertEqual(blob["lock_id"], "lock-a")
        self.assertEqual(blob["deadline"], res["deadline"])
        # And fence-check still passes after the prune.
        ok, _ = sl.fence_check(root=self.root, lock_id="lock-a", paths=["src/a.py"])
        self.assertTrue(ok)


# ── (i) the FS-fold fix — case-insensitive / unicode double-grant guard ───────────────
class FSFoldTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_case_fold_only_when_git_says_ignorecase(self):
        # Pin the repo to a case-INSENSITIVE FS view -> two casings fold to one key.
        _git(self.root, "config", "core.ignorecase", "true")
        _git(self.root, "config", "core.precomposeunicode", "false")
        fold = sl._fold_flags(self.root)
        self.assertEqual(fold, (True, False))
        ki = sl._normalize_repo_relative(self.root, "src/Handler.py", fold=fold)
        kl = sl._normalize_repo_relative(self.root, "src/handler.py", fold=fold)
        self.assertEqual(ki, kl, "case-insensitive FS must fold Handler.py == handler.py")

    def test_case_sensitive_fs_keeps_distinct_files_distinct(self):
        _git(self.root, "config", "core.ignorecase", "false")
        _git(self.root, "config", "core.precomposeunicode", "false")
        fold = sl._fold_flags(self.root)
        self.assertEqual(fold, (False, False))
        ki = sl._normalize_repo_relative(self.root, "src/Handler.py", fold=fold)
        kl = sl._normalize_repo_relative(self.root, "src/handler.py", fold=fold)
        self.assertNotEqual(ki, kl, "case-sensitive FS must NOT invent a false collision")

    def test_case_insensitive_double_grant_is_denied_end_to_end(self):
        _git(self.root, "config", "core.ignorecase", "true")
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/Handler.py"], deadline=future())
        with self.assertRaises(sl.LeaseDenied):
            sl.acquire_lease(root=self.root, lock_id="lock-b", owner="bob",
                             paths=["src/handler.py"], deadline=future())
        self.assertEqual(len(lock_refs(self.root)), 1,
                         "on a case-insensitive FS the same file must be one ref, not two")

    def test_unicode_nfc_fold_when_git_says_precompose(self):
        _git(self.root, "config", "core.precomposeunicode", "true")
        _git(self.root, "config", "core.ignorecase", "false")
        fold = sl._fold_flags(self.root)
        # 'é' as NFC (single codepoint) vs NFD (e + combining accent) name the same file.
        nfc = sl._normalize_repo_relative(self.root, "src/café.py", fold=fold)
        nfd = sl._normalize_repo_relative(self.root, "src/café.py", fold=fold)
        self.assertEqual(nfc, nfd, "precomposeunicode FS must fold NFD == NFC")


# ── machine-boundary fail-loud ────────────────────────────────────────────────────────
class MachineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_foreign_refstore_blob_fails_loud(self):
        # Hand-mint a lock blob stamped with a FOREIGN host, point a lock ref at it, then try to
        # acquire the same path -> must hard-stop (not silently overwrite).
        foreign = {"lock_id": "elsewhere", "owner": "x", "token": 1, "deadline": future(),
                   "host": "some-other-host", "git_dir": "/somewhere/else/.git"}
        oid = subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "-w", "--stdin"],
            input=json.dumps(foreign), capture_output=True, text=True,
        ).stdout.strip()
        ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/x.py"])[0])
        _git(self.root, "update-ref", ref, oid)
        with self.assertRaises(sl.MachineBoundaryError):
            sl.acquire_lease(root=self.root, lock_id="local", owner="alice",
                             paths=["src/x.py"], deadline=future())

    def _mint_foreign(self, path="src/x.py"):
        foreign = {"lock_id": "elsewhere", "owner": "x", "token": 1, "deadline": future(),
                   "host": "some-other-host", "git_dir": "/somewhere/else/.git"}
        oid = subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "-w", "--stdin"],
            input=json.dumps(foreign), capture_output=True, text=True,
        ).stdout.strip()
        ref = sl._lease_ref(sl._normalize_paths(self.root, [path])[0])
        _git(self.root, "update-ref", ref, oid)
        return ref, oid

    def test_release_foreign_refstore_blob_fails_loud(self):
        ref, oid = self._mint_foreign()
        with self.assertRaises(sl.MachineBoundaryError):
            sl.release_lease(root=self.root, lock_id="local", paths=["src/x.py"])
        self.assertEqual(sl._ref_oid(self.root, ref), oid, "foreign lock must be left in place")

    def test_release_foreign_refstore_changes_nothing_either_path_order(self):
        # Regression: release raised mid-loop AFTER deleting earlier owned locks (unlogged), and
        # path order decided what survived. Decide for every path first; fail loud = no change.
        for order in (["src/a.py", "src/b.py"], ["src/b.py", "src/a.py"]):
            sl.acquire_lease(root=self.root, lock_id="mine", owner="me",
                             paths=["src/a.py"], deadline=future())
            self._mint_foreign("src/b.py")
            logs = Path(self._tmp.name) / "logs"
            a_ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/a.py"])[0])
            with self.assertRaises(sl.MachineBoundaryError):
                sl.release_lease(root=self.root, lock_id="mine", paths=order,
                                 log_dir=logs, owner="me")
            self.assertIsNotNone(sl._ref_oid(self.root, a_ref),
                                 "owned lock must survive a fail-loud release (%s)" % order)
            self.assertFalse((logs / "scope-lease.log").exists())
            # reset for next order
            b_ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/b.py"])[0])
            _git(self.root, "update-ref", "-d", b_ref)
            _git(self.root, "update-ref", "-d", a_ref)

    def test_release_cli_foreign_refstore_exits_6(self):
        self._mint_foreign()
        env = dict(os.environ)
        env["SCOPE_LEASE"] = "on"
        env["SCOPE_LEASE_OWNER"] = "cli-tester"
        env.pop("SCOPE_LEASE_PLANS_DIR", None)
        env.pop("PLAN_GATE_PLANS_DIR", None)
        rc = _run_cli(["release", "--root", str(self.root), "--source", "paths",
                       "--paths", "src/x.py", "--lock-id", "local"], env)
        self.assertEqual(rc, 6)

    def test_local_blob_stamps_this_host_and_gitdir(self):
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py"], deadline=future())
        ref = sl._lease_ref(sl._normalize_paths(self.root, ["src/a.py"])[0])
        blob = sl._read_lease_blob(self.root, sl._ref_oid(self.root, ref))
        self.assertEqual(blob["host"], socket.gethostname())
        self.assertTrue(blob["git_dir"].endswith(".git"))


# ── path-source seam + owner resolution ───────────────────────────────────────────────
def _write_plan(root: Path, name: str, paths: list[str], status: str = "active") -> Path:
    plans = root / "plans"
    plans.mkdir(exist_ok=True)
    body = ["---", f"name: {name}", f"status: {status}", "allowed_paths:"]
    body += [f"  - {p}" for p in paths]
    body += ["---", "", f"# {name}", ""]
    (plans / f"{name}.md").write_text("\n".join(body))
    return plans


class PathSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self._saved_env = {k: os.environ.get(k) for k in
                           ("SCOPE_LEASE_PLANS_DIR", "PLAN_GATE_PLANS_DIR", "SCOPE_LEASE_PATHS",
                            "SCOPE_LEASE_PATHS_FILE", "SCOPE_LEASE_LOCK_ID", "SCOPE_LEASE_OWNER")}
        for k in self._saved_env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_plan_gate_adapter_reads_allowed_paths_and_name(self):
        plans = _write_plan(self.root, "add-logout", ["src/a.py", "src/b.py"])
        os.environ["SCOPE_LEASE_PLANS_DIR"] = str(plans)
        paths, lock_id = sl.resolve_path_source(
            source="plan-gate", cli_paths=None, file_path=None, cli_lock_id=None)
        self.assertEqual(sorted(paths), ["src/a.py", "src/b.py"])
        self.assertEqual(lock_id, "add-logout")

    def test_plan_gate_falls_back_to_plan_gate_plans_dir_var(self):
        plans = _write_plan(self.root, "p1", ["src/a.py"])
        os.environ["PLAN_GATE_PLANS_DIR"] = str(plans)  # the plan-gate adopter's existing var
        paths, lock_id = sl.resolve_path_source(
            source="plan-gate", cli_paths=None, file_path=None, cli_lock_id=None)
        self.assertEqual(paths, ["src/a.py"])
        self.assertEqual(lock_id, "p1")

    def test_more_than_one_active_plan_is_an_error(self):
        plans = _write_plan(self.root, "p1", ["src/a.py"])
        _write_plan(self.root, "p2", ["src/b.py"])  # second active in the same dir
        os.environ["SCOPE_LEASE_PLANS_DIR"] = str(plans)
        with self.assertRaises(sl.PathSourceError):
            sl.resolve_path_source(source="plan-gate", cli_paths=None, file_path=None,
                                   cli_lock_id=None)

    def test_archived_plan_is_ignored(self):
        plans = _write_plan(self.root, "live", ["src/a.py"])
        _write_plan(self.root, "old", ["src/z.py"], status="archived")
        os.environ["SCOPE_LEASE_PLANS_DIR"] = str(plans)
        paths, lock_id = sl.resolve_path_source(
            source="plan-gate", cli_paths=None, file_path=None, cli_lock_id=None)
        self.assertEqual(paths, ["src/a.py"])
        self.assertEqual(lock_id, "live")

    def test_paths_source_from_cli(self):
        paths, lock_id = sl.resolve_path_source(
            source="paths", cli_paths=["src/a.py", "src/b.py"], file_path=None,
            cli_lock_id="my-lock")
        self.assertEqual(paths, ["src/a.py", "src/b.py"])
        self.assertEqual(lock_id, "my-lock")

    def test_paths_source_requires_a_lock_id(self):
        with self.assertRaises(sl.PathSourceError):
            sl.resolve_path_source(source="paths", cli_paths=["src/a.py"], file_path=None,
                                   cli_lock_id=None)

    def test_paths_source_from_env(self):
        os.environ["SCOPE_LEASE_PATHS"] = "src/a.py" + os.pathsep + "src/b.py"
        os.environ["SCOPE_LEASE_LOCK_ID"] = "env-lock"
        paths, lock_id = sl.resolve_path_source(
            source="paths", cli_paths=None, file_path=None, cli_lock_id=None)
        self.assertEqual(paths, ["src/a.py", "src/b.py"])
        self.assertEqual(lock_id, "env-lock")

    def test_file_source(self):
        f = self.root / "scope.md"
        f.write_text("---\nname: file-plan\nallowed_paths:\n  - src/x.py\n  - src/y.py\n---\n")
        paths, lock_id = sl.resolve_path_source(
            source="file", cli_paths=None, file_path=str(f), cli_lock_id=None)
        self.assertEqual(sorted(paths), ["src/x.py", "src/y.py"])
        self.assertEqual(lock_id, "file-plan")

    def test_lock_id_override_beats_plan_name(self):
        plans = _write_plan(self.root, "plan-name", ["src/a.py"])
        os.environ["SCOPE_LEASE_PLANS_DIR"] = str(plans)
        paths, lock_id = sl.resolve_path_source(
            source="plan-gate", cli_paths=None, file_path=None, cli_lock_id="overridden")
        self.assertEqual(lock_id, "overridden")

    def test_owner_env_overrides_branch(self):
        os.environ["SCOPE_LEASE_OWNER"] = "ci-runner-7"
        self.assertEqual(sl.derive_owner(self.root), "ci-runner-7")

    def test_owner_derives_from_branch(self):
        # No SCOPE_LEASE_OWNER -> the repo's branch (work-branch).
        self.assertEqual(sl.derive_owner(self.root), "work-branch")


# ── CLI integration (exit-code contract) ──────────────────────────────────────────────
def _cli_env(root: Path, plans: Path) -> dict:
    env = dict(os.environ)
    env["SCOPE_LEASE"] = "on"
    env["SCOPE_LEASE_SOURCE"] = "plan-gate"
    env["SCOPE_LEASE_PLANS_DIR"] = str(plans)
    env.pop("PLAN_GATE_PLANS_DIR", None)
    env.pop("SCOPE_LEASE_OWNER", None)
    env.pop("SCOPE_LEASE_PATHS", None)
    env.pop("SCOPE_LEASE_LOCK_ID", None)
    return env


def _run_cli(args: list[str], env: dict) -> int:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )
    return proc.returncode


class CLITests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_then_deny_then_release_exit_codes(self):
        plans = _write_plan(self.root, "plan-a", ["src/a.py"])
        env_a = _cli_env(self.root, plans)
        # acquire from plan-a -> 0
        self.assertEqual(_run_cli(["acquire", "--root", str(self.root), "--deadline", future()],
                                  env_a), 0)
        # a SECOND plan claiming the same path, as a different lock id -> DENY exit 5.
        # Point it at a separate dir holding only plan-b (one active plan per dir).
        solo = self.root / "plans-b"
        solo.mkdir()
        (solo / "plan-b.md").write_text(
            "---\nname: plan-b\nstatus: active\nallowed_paths:\n  - src/a.py\n---\n")
        env_b = _cli_env(self.root, solo)
        self.assertEqual(_run_cli(["acquire", "--root", str(self.root), "--deadline", future()],
                                  env_b), 5)
        # release plan-a -> 0, and a second release is still 0 (idempotent)
        self.assertEqual(_run_cli(["release", "--root", str(self.root)], env_a), 0)
        self.assertEqual(_run_cli(["release", "--root", str(self.root)], env_a), 0)

    def test_disabled_is_noop(self):
        plans = _write_plan(self.root, "plan-a", ["src/a.py"])
        env = _cli_env(self.root, plans)
        env["SCOPE_LEASE"] = "off"
        self.assertEqual(_run_cli(["acquire", "--root", str(self.root)], env), 0)
        self.assertEqual(lock_refs(self.root), [], "disabled acquire must claim nothing")

    def test_no_active_plan_is_error_exit_1(self):
        plans = _write_plan(self.root, "old", ["src/a.py"], status="archived")
        env = _cli_env(self.root, plans)
        self.assertEqual(_run_cli(["acquire", "--root", str(self.root)], env), 1)

    def test_release_log_row_carries_owner(self):
        logdir = self.root / "logs"
        env = dict(os.environ)
        env["SCOPE_LEASE"] = "on"
        env["SCOPE_LEASE_OWNER"] = "cli-owner"
        env["SCOPE_LEASE_LOG_DIR"] = str(logdir)
        env.pop("SCOPE_LEASE_PLANS_DIR", None)
        env.pop("PLAN_GATE_PLANS_DIR", None)
        args = ["--root", str(self.root), "--source", "paths",
                "--paths", "src/a.py", "--lock-id", "own-lock"]
        self.assertEqual(_run_cli(["acquire", *args, "--deadline", future()], env), 0)
        self.assertEqual(_run_cli(["release", *args], env), 0)
        rows = [json.loads(l) for l in
                (logdir / "scope-lease.log").read_text(encoding="utf-8").splitlines()]
        rel = [r for r in rows if r["event"] == "release"]
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel[0]["owner"], "cli-owner")
        self.assertEqual(rel[0]["lock_id"], "own-lock")

    def test_release_row_owner_is_the_locks_owner_not_the_releaser(self):
        logdir = self.root / "logs3"
        sl.acquire_lease(root=self.root, lock_id="L", owner="alice",
                         paths=["src/a.py"], deadline=future())
        sl.release_lease(root=self.root, lock_id="L", paths=["src/a.py"],
                         log_dir=logdir, owner="bob")
        row = json.loads((logdir / "scope-lease.log").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["event"], "release")
        self.assertEqual(row["owner"], "alice")
        self.assertEqual(row["released_by"], "bob")

    def test_reclaim_row_carries_detail(self):
        logdir = self.root / "logs2"
        sl.acquire_lease(root=self.root, lock_id="old", owner="alice",
                         paths=["src/a.py"], deadline=past())
        sl.acquire_lease(root=self.root, lock_id="new", owner="bob",
                         paths=["src/a.py"], deadline=future(), log_dir=logdir)
        row = json.loads((logdir / "scope-lease.log").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["reclaimed"], ["src/a.py"])
        d = row["reclaimed_detail"][0]
        self.assertEqual((d["was_lock_id"], d["was_owner"], d["token"]), ("old", "alice", 2))

    def test_paths_source_via_cli_full_cycle(self):
        env = dict(os.environ)
        env["SCOPE_LEASE"] = "on"
        env["SCOPE_LEASE_OWNER"] = "cli-tester"
        env.pop("SCOPE_LEASE_PLANS_DIR", None)
        env.pop("PLAN_GATE_PLANS_DIR", None)
        args = ["--root", str(self.root), "--source", "paths",
                "--paths", "src/a.py", "src/b.py", "--lock-id", "cli-lock"]
        self.assertEqual(_run_cli(["acquire", *args, "--deadline", future()], env), 0)
        self.assertEqual(len(lock_refs(self.root)), 2)
        self.assertEqual(_run_cli(["fence-check", *args], env), 0)
        self.assertEqual(_run_cli(["release", *args], env), 0)
        self.assertEqual(lock_refs(self.root), [])


def _run_holder(root: Path, paths: list, env_extra: dict, *flags: str):
    env = {k: v for k, v in os.environ.items()
           if k not in ("SCOPE_LEASE", "SCOPE_LEASE_OWNER", "SCOPE_LEASE_LOCK_ID")}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "holder", "--root", str(root), *flags, *map(str, paths)],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )


class HolderTests(unittest.TestCase):
    """`holder`: read-only lookup. 0 = every path free or mine, 5 = any held by someone else."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py"], deadline=future())

    def tearDown(self):
        self._tmp.cleanup()

    def test_free_path_exit_0(self):
        p = _run_holder(self.root, [self.root / "src" / "free.py"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("FREE", p.stdout)

    def test_held_by_other_exit_5_names_holder_and_age(self):
        p = _run_holder(self.root, [self.root / "src" / "a.py"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(p.returncode, 5, p.stderr)
        self.assertIn("alice", p.stdout)
        self.assertIn("lock-a", p.stdout)
        self.assertIn("age", p.stdout)

    def test_mine_by_owner_and_by_lock_id(self):
        a = _run_holder(self.root, ["src/a.py"], {"SCOPE_LEASE_OWNER": "alice"})
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("MINE", a.stdout)
        b = _run_holder(self.root, ["src/a.py"],
                        {"SCOPE_LEASE_OWNER": "bob", "SCOPE_LEASE_LOCK_ID": "lock-a"})
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertIn("MINE", b.stdout)

    def test_json_age_is_reported(self):
        p = _run_holder(self.root, ["src/a.py"], {"SCOPE_LEASE_OWNER": "bob"}, "--json")
        row = json.loads(p.stdout.splitlines()[0])
        self.assertEqual((row["status"], row["owner"], row["lock_id"]), ("held", "alice", "lock-a"))
        self.assertIsInstance(row["age_seconds"], int)
        self.assertGreaterEqual(row["age_seconds"], 0)

    def test_one_held_among_many_is_exit_5(self):
        p = _run_holder(self.root, ["src/free.py", "src/a.py"], {"SCOPE_LEASE_OWNER": "bob"},
                        "--json")
        self.assertEqual(p.returncode, 5)
        self.assertEqual([json.loads(x)["status"] for x in p.stdout.splitlines()],
                         ["free", "held"])

    def test_expired_lease_counts_as_free(self):
        sl.acquire_lease(root=self.root, lock_id="old", owner="carol",
                         paths=["src/old.py"], deadline=past())
        p = _run_holder(self.root, ["src/old.py"], {"SCOPE_LEASE_OWNER": "bob"}, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        row = json.loads(p.stdout.splitlines()[0])
        self.assertEqual(row["status"], "free")
        self.assertTrue(row["expired"])

    def test_writes_nothing(self):
        before = set(lock_refs(self.root))
        _run_holder(self.root, ["src/a.py", "src/new.py"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(set(lock_refs(self.root)), before)

    def test_ignores_scope_lease_off_switch(self):
        p = _run_holder(self.root, ["src/a.py"],
                        {"SCOPE_LEASE": "off", "SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(p.returncode, 5)

    def test_foreign_ref_store_is_exit_6(self):
        ref = sl._lease_ref("src/zz.py")
        blob = sl._hash_lease_blob(self.root, lock_id="x", owner="y", token=1, deadline=future(),
                                   refstore={"host": "other-host", "git_dir": "/elsewhere"})
        _git(self.root, "update-ref", ref, blob)
        p = _run_holder(self.root, ["src/zz.py"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(p.returncode, 6)

    def test_not_a_repo_is_an_error(self):
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        p = _run_holder(plain, ["x.py"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertGreaterEqual(p.returncode, 1)
        self.assertNotIn(p.returncode, (5,))

    def test_case_fold_follows_git_ignorecase(self):
        # Same canonicalization as acquire: with core.ignorecase a different spelling of
        # the leased file is still held; on a case-sensitive repo it is a different file.
        _git(self.root, "config", "core.ignorecase", "true")
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["docs/Foo.md"], deadline=future())
        p = _run_holder(self.root, ["docs/foo.md"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(p.returncode, 5, p.stdout)
        _git(self.root, "config", "core.ignorecase", "false")
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["docs/Bar.md"], deadline=future())
        q = _run_holder(self.root, ["docs/bar.md"], {"SCOPE_LEASE_OWNER": "bob"})
        self.assertEqual(q.returncode, 0, q.stdout)

    def test_extend_keeps_original_acquire_time(self):
        first = sl.holder_status(root=self.root, paths=["src/a.py"], owner="bob")[0]
        sl.acquire_lease(root=self.root, lock_id="lock-a", owner="alice",
                         paths=["src/a.py"], deadline=future())
        second = sl.holder_status(root=self.root, paths=["src/a.py"], owner="bob")[0]
        self.assertEqual(first["acquired"], second["acquired"])
        self.assertEqual(second["token"], first["token"] + 1)


class EnabledSwitchValueTests(unittest.TestCase):
    """SCOPE_LEASE opt-out switch: unset runs; on/1/true/yes (any case, trimmed) run;
    any other value disables."""

    def _enabled(self, value):
        env = {k: v for k, v in os.environ.items() if k != "SCOPE_LEASE"}
        if value is not None:
            env["SCOPE_LEASE"] = value
        with mock.patch.dict(os.environ, env, clear=True):
            return sl._enabled()

    def test_unset_runs(self):
        self.assertTrue(self._enabled(None))

    def test_truthy_values_run(self):
        for v in ("on", "1", "true", "yes", "ON", "True", " yes "):
            self.assertTrue(self._enabled(v), msg=repr(v))

    def test_other_values_disable(self):
        for v in ("0", "off", "false", "", "garbage"):
            self.assertFalse(self._enabled(v), msg=repr(v))


if __name__ == "__main__":
    unittest.main(verbosity=2)
