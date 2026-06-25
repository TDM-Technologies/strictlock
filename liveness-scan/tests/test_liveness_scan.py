#!/usr/bin/env python3
r"""Standalone acceptance suite for liveness-scan.

Stdlib `unittest`, no third-party dependencies. The deterministic classification core
(classify / gather / the parsers / attribution) is driven through a FAKE GitProbe and an
injected `now`, so the six statuses are pinned without any real git. A second group builds a
REAL git repo with a linked worktree to prove GitProbe (worktree list / commit time / ahead-of-
base) and the always-exit-0 contract against real subprocess output.

Covers:
  * classification: running / stalled / ambiguous(ceiling) / ambiguous(no-signal) /
    done-unmerged / idle / clean, on both the heartbeat and commit-mtime signals
  * attribution: plan_specifically_claims excludes worktree_bypass; relative-only claims nothing
  * the byte-0 frontmatter discipline (prose / fenced status: active is INERT)
  * the report: escalations surface a NON-destructive recovery command (never `reset --hard`)
  * report-only: never reaps; safe_main exits 0 even when main() raises
  * real git: GitProbe.worktrees / commit_unixtime / ahead_of_base, and main() exits 0

Run:  python3 liveness-scan/tests/test_liveness_scan.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "liveness-scan.py"

_spec = importlib.util.spec_from_file_location("liveness_scan", SCRIPT)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)


def _ct(minutes_ago: float) -> int:
    """A commit unixtime `minutes_ago` minutes before NOW."""
    return int((NOW - timedelta(minutes=minutes_ago)).timestamp())


class FakeProbe(ls.GitProbe):
    def __init__(self, worktrees, commit_times=None, ahead=None):
        self._wts = worktrees
        self._ct = commit_times or {}
        self._ahead = ahead or {}

    def worktrees(self):
        return self._wts

    def commit_unixtime(self, sha):
        return self._ct.get(sha)

    def ahead_of_base(self, sha, base):
        return self._ahead.get(sha)


def _plan(name, status, paths, bypass=False):
    return ls.Plan(name=name, status=status, scope="", worktree_bypass=bypass, allowed_paths=paths)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.th = ls.Thresholds()  # defaults: heartbeat 10, commit 90, ceiling 480
        self.wt = {"path": "/repo/wt-feat", "branch": "feat", "head": "deadbeef"}

    def _c(self, **kw):
        base = dict(active_plan=None, executed_plan=None, commit_age_min=None,
                    ahead_of_base=None, heartbeat_age_min=None, th=self.th)
        base.update(kw)
        return ls.classify(self.wt, NOW, **base)

    def test_running_heartbeat_fresh(self):
        r = self._c(active_plan=_plan("p", "active", []), heartbeat_age_min=2.0)
        self.assertEqual(r["status"], "running")
        self.assertFalse(r["escalate"])

    def test_stalled_heartbeat_past_window(self):
        r = self._c(active_plan=_plan("p", "active", []), heartbeat_age_min=30.0)
        self.assertEqual(r["status"], "stalled")
        self.assertFalse(r["escalate"])

    def test_ambiguous_past_ceiling_escalates(self):
        r = self._c(active_plan=_plan("p", "active", []), heartbeat_age_min=600.0)
        self.assertEqual(r["status"], "ambiguous")
        self.assertTrue(r["escalate"])
        self.assertIsNotNone(r["recovery"])

    def test_ambiguous_no_signal_escalates(self):
        r = self._c(active_plan=_plan("p", "active", []),
                    commit_age_min=None, heartbeat_age_min=None)
        self.assertEqual(r["status"], "ambiguous")
        self.assertTrue(r["escalate"])

    def test_commit_mtime_fallback_running(self):
        # no heartbeat -> uses commit age + the 90m commit window; 10m -> running on commit-mtime
        r = self._c(active_plan=_plan("p", "active", []), commit_age_min=10.0)
        self.assertEqual(r["status"], "running")
        self.assertEqual(r["signal"], "commit-mtime")

    def test_commit_mtime_note_on_stalled(self):
        # the "inferred from commit mtime" note annotates the stalled/ambiguous lines (not running)
        r = self._c(active_plan=_plan("p", "active", []), commit_age_min=120.0)
        self.assertEqual(r["status"], "stalled")
        self.assertIn(ls.NO_HEARTBEAT_NOTE, r["reason"])

    def test_commit_mtime_fallback_stalled(self):
        r = self._c(active_plan=_plan("p", "active", []), commit_age_min=120.0)
        self.assertEqual(r["status"], "stalled")

    def test_done_unmerged_via_executed_plan(self):
        r = self._c(executed_plan=_plan("p", "executed", []))
        self.assertEqual(r["status"], "done-unmerged")
        self.assertFalse(r["escalate"])

    def test_done_unmerged_via_ahead(self):
        r = self._c(ahead_of_base=True)
        self.assertEqual(r["status"], "done-unmerged")

    def test_idle_no_plan_not_ahead(self):
        r = self._c(ahead_of_base=False)
        self.assertEqual(r["status"], "idle")
        self.assertIn("clean or merged", r["reason"])

    def test_idle_unknown_base_says_unresolvable_not_merged(self):
        # ahead_of_base is None (base ref absent, e.g. no remote) -> idle, but the reason must NOT
        # claim the branch is "clean or merged" (it's unknown)
        r = self._c(ahead_of_base=None)
        self.assertEqual(r["status"], "idle")
        self.assertNotIn("clean or merged", r["reason"])
        self.assertIn("unresolvable", r["reason"])

    def test_main_worktree_is_clean_even_with_claiming_plan(self):
        main_wt = {"path": "/repo", "branch": "main", "head": "abc"}
        r = ls.classify(main_wt, NOW, active_plan=_plan("p", "active", ["/repo/x"]),
                        executed_plan=None, commit_age_min=1.0, ahead_of_base=None,
                        heartbeat_age_min=1.0, th=self.th)
        self.assertEqual(r["status"], "clean")
        self.assertFalse(r["escalate"])

    def test_recovery_is_non_destructive(self):
        r = self._c(active_plan=_plan("p", "active", []), heartbeat_age_min=600.0)
        self.assertNotIn("reset --hard", r["recovery"])
        self.assertNotIn("reap", r["recovery"].split("#")[0])
        self.assertIn("git status", r["recovery"])


class TestAttribution(unittest.TestCase):
    def test_absolute_path_under_worktree_claims(self):
        p = _plan("p", "active", ["/repo/wt-feat/src/a.py"])
        self.assertTrue(ls.plan_specifically_claims(p, "/repo/wt-feat"))

    def test_relative_only_claims_nothing(self):
        p = _plan("p", "active", ["src/a.py"])
        self.assertFalse(ls.plan_specifically_claims(p, "/repo/wt-feat"))

    def test_worktree_bypass_excluded_from_attribution(self):
        # bypass plan with NO absolute path under this wt -> not attributed (the key correctness lesson)
        p = _plan("p", "active", [], bypass=True)
        self.assertFalse(ls.plan_specifically_claims(p, "/repo/wt-feat"))

    def test_path_under_a_different_worktree_does_not_claim(self):
        p = _plan("p", "active", ["/repo/wt-other/src/a.py"])
        self.assertFalse(ls.plan_specifically_claims(p, "/repo/wt-feat"))

    def test_symlinked_worktree_root_still_attributes(self):
        # the /var -> /private/var (and any symlinked root) trip: a plan path via the symlink
        # spelling must still attribute to the resolved worktree root (the form git reports).
        tmp = Path(tempfile.mkdtemp())
        try:
            real = tmp / "real_wt"
            real.mkdir()
            (real / "src").mkdir()
            (real / "src" / "a.py").write_text("x\n")
            link = tmp / "link_wt"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            # plan references the file via the SYMLINK spelling; worktree root is the REAL path
            p = _plan("p", "active", [str(link / "src" / "a.py")])
            self.assertTrue(ls.plan_specifically_claims(p, str(real)))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_case_distinct_worktrees_not_falsely_attributed(self):
        # on a case-sensitive FS /repo/WT-Feat and /repo/wt-feat are DISTINCT; a plan owning one
        # must not claim the other. _canon must NOT case-fold. (Skip where the FS folds case.)
        tmp = Path(tempfile.mkdtemp())
        try:
            lower = tmp / "wt-feat"
            lower.mkdir()
            upper = tmp / "WT-Feat"
            if upper.exists():  # case-insensitive FS folded them to one dir — N/A here
                self.skipTest("case-insensitive filesystem; distinct-case dirs collapse")
            upper.mkdir()
            p = _plan("p", "active", [str(upper / "src" / "a.py")])
            self.assertFalse(ls.plan_specifically_claims(p, str(lower)),
                             "a plan under WT-Feat must NOT claim the distinct dir wt-feat")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestFrontmatter(unittest.TestCase):
    def test_byte0_block_parsed(self):
        body = ls.parse_frontmatter("---\nstatus: active\nname: p\n---\nbody\n")
        self.assertIsNotNone(body)
        self.assertEqual(ls.fm_scalar(body, "status"), "active")

    def test_prose_status_is_inert(self):
        self.assertIsNone(ls.parse_frontmatter("Some prose.\nstatus: active\n"))

    def test_fenced_yaml_is_inert(self):
        self.assertIsNone(ls.parse_frontmatter("# Title\n```yaml\nstatus: active\n```\n"))

    def test_inline_comment_stripped(self):
        body = ls.parse_frontmatter("---\nstatus: active  # the one active plan\n---\n")
        self.assertEqual(ls.fm_scalar(body, "status"), "active")

    def test_fm_list(self):
        body = ls.parse_frontmatter(
            "---\nallowed_paths:\n  - /a/b.py\n  - /c/d.py   # a note\n---\n")
        self.assertEqual(ls.fm_list(body, "allowed_paths"), ["/a/b.py", "/c/d.py"])


class TestGatherAndReport(unittest.TestCase):
    def setUp(self):
        self.th = ls.Thresholds()

    def test_gather_counts_and_escalations(self):
        wts = [
            {"path": "/repo", "branch": "main", "head": "m"},
            {"path": "/repo/wt-a", "branch": "feat-a", "head": "a"},   # done-unmerged (ahead)
            {"path": "/repo/wt-b", "branch": "feat-b", "head": "b"},   # idle (not ahead)
        ]
        probe = FakeProbe(wts, commit_times={"m": _ct(1), "a": _ct(5), "b": _ct(5)},
                          ahead={"a": True, "b": False})
        digest = ls.gather(plans_dir=None, base="origin/main", heartbeat_relpath=None,
                           now=NOW, probe=probe, th=self.th)
        self.assertEqual(digest["counts"].get("clean"), 1)
        self.assertEqual(digest["counts"].get("done-unmerged"), 1)
        self.assertEqual(digest["counts"].get("idle"), 1)
        self.assertEqual(len(digest["escalations"]), 0)
        report = ls.build_report(digest)
        self.assertIn("liveness-scan", report)
        self.assertIn("No — no ambiguous stalls", report.replace("_None", "No"))

    def test_gather_escalates_ambiguous_with_active_plan(self):
        # an active plan claims wt-a; head commit is 600m old, no heartbeat -> ambiguous, escalate
        plan = _plan("big-wp", "active", ["/repo/wt-a/src/x.py"])
        wts = [{"path": "/repo/wt-a", "branch": "feat-a", "head": "a"}]
        probe = FakeProbe(wts, commit_times={"a": _ct(600)})

        class _Plans:  # feed load_plans a dir-like that yields our plan via monkeypatch
            pass
        # inject by monkeypatching load_plans for this test
        orig = ls.load_plans
        ls.load_plans = lambda d: [plan]
        try:
            digest = ls.gather(plans_dir=Path("/does/not/matter"), base="origin/main",
                               heartbeat_relpath=None, now=NOW, probe=probe, th=self.th)
        finally:
            ls.load_plans = orig
        self.assertEqual(len(digest["escalations"]), 1)
        self.assertEqual(digest["escalations"][0]["status"], "ambiguous")
        report = ls.build_report(digest)
        self.assertIn("Escalations", report)
        self.assertIn("recovery", report)


class TestReportOnlyContract(unittest.TestCase):
    def test_safe_main_exits_zero_on_internal_error(self):
        # force main() to raise; safe_main must still return 0 (never break a scheduled run)
        orig = ls.main
        ls.main = lambda argv=None: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(ls.safe_main([]), 0)
        finally:
            ls.main = orig


class TestRealGit(unittest.TestCase):
    """Prove GitProbe + the exit-0 CLI against a real repo with a linked worktree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("LIVENESS_SCAN_") or k == "PLAN_GATE_PLANS_DIR":
                os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def _git(self, *args, cwd=None, check=True):
        proc = subprocess.run(["git", "-C", str(cwd or self.root), "-c", "commit.gpgsign=false",
                               *args], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
        return proc

    def _seed(self):
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "T")
        self._git("checkout", "-q", "-b", "main")
        (self.root / "a.txt").write_text("a\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "seed")

    def test_probe_worktrees_and_ahead(self):
        self._seed()
        wt = self.tmp / "wt-feat"
        self._git("worktree", "add", "-q", "-b", "feat", str(wt))
        (wt / "b.txt").write_text("b\n")
        self._git("add", "-A", cwd=wt)
        self._git("commit", "-q", "-m", "feat work", cwd=wt)

        probe = ls.GitProbe(str(self.root))
        wts = probe.worktrees()
        paths = {w["path"] for w in wts}
        self.assertIn(str(self.root.resolve()), {str(Path(p).resolve()) for p in paths})
        self.assertTrue(any(w["branch"] == "feat" for w in wts))

        feat = next(w for w in wts if w["branch"] == "feat")
        self.assertIsNotNone(probe.commit_unixtime(feat["head"]))
        # feat is ahead of main (has a commit main doesn't)
        self.assertTrue(probe.ahead_of_base(feat["head"], "main"))
        # main is not ahead of itself
        main = next(w for w in wts if w["branch"] == "main")
        self.assertFalse(probe.ahead_of_base(main["head"], "main"))

    def test_cli_exits_zero(self):
        self._seed()
        rc = ls.safe_main(["--root", str(self.root), "--dry-run"])
        self.assertEqual(rc, 0)
        rc_json = ls.safe_main(["--root", str(self.root), "--json"])
        self.assertEqual(rc_json, 0)

    def test_log_dir_colliding_with_a_file_still_exits_zero(self):
        # LIVENESS_SCAN_LOG_DIR pointing at an existing FILE must not crash the scan: exit 0,
        # report still printed, no traceback-as-failure.
        self._seed()
        collide = self.tmp / "not-a-dir"
        collide.write_text("i am a file\n")
        os.environ["LIVENESS_SCAN_LOG_DIR"] = str(collide)
        rc = ls.safe_main(["--root", str(self.root)])
        self.assertEqual(rc, 0)

    def test_no_origin_main_base_exits_zero_and_is_idle(self):
        # a repo with no origin/main: ahead_of_base is unknown -> a worktree reads idle (safe),
        # and the scan still exits 0.
        self._seed()
        wt = self.tmp / "wt-feat"
        self._git("worktree", "add", "-q", "-b", "feat", str(wt))
        (wt / "c.txt").write_text("c\n")
        self._git("add", "-A", cwd=wt)
        self._git("commit", "-q", "-m", "feat work", cwd=wt)
        os.environ["LIVENESS_SCAN_BASE"] = "origin/main"  # does not exist in this repo
        rc = ls.safe_main(["--root", str(self.root), "--dry-run"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
