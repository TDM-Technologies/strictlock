#!/usr/bin/env python3
"""Standalone test suite for spill-gate.

Stdlib `unittest`, no third-party dependencies. Each test builds a REAL git repo in
a tempdir and runs the shipped gate as a subprocess (its true hook contract: exit
0 allows, non-zero blocks).

Every spill-shaped fixture is ASSEMBLED AT RUNTIME from fragments, so this file
contains no literal secret / UUID / machine path and passes the gate (and CI's
secret-scan) without any allowlist entry.

Run:  python3 spill-gate/tests/test_spill_gate.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "spill-gate.py"

# --- fixtures, assembled from fragments ---------------------------------------
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
AWS_KEY = "AK" + "IA" + "ABCDEFGHIJKLMNOP"                       # 16 chars after AKIA
GH_TOKEN = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # 36 chars
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
SK_ANT = "sk-" + "ant-" + "abcdefghijklmnopqrstuvwx"
STRIPE = "sk_" + "live_" + "abcdefghijklmnopqrstuv"
GOOGLE = "AI" + "za" + "SyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"
UUID = "123e4567" + "-e89b-" + "12d3-" + "a456-" + "426614174000"
MAC_PATH = "/" + "Users" + "/alice/project"
LINUX_PATH = "/" + "home" + "/bob/src"
WIN_PATH = "C" + ":\\" + "Users" + "\\carol\\x"
WIN_PATH_FWD = "D" + ":/" + "Users" + "/dave/x"


def run(cmd, cwd, env=None, stdin=None):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env, input=stdin)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        env = dict(os.environ)
        for k in list(env):
            if k.startswith("SPILL_GATE") or k.startswith("GIT_"):
                del env[k]
        env.update({
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.test",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        })
        self.env = env
        self.git("init", "-q")
        self.git("config", "commit.gpgsign", "false")
        self.write("README.txt", "hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args, cwd=None):
        r = run(["git", *args], cwd or self.repo, self.env)
        self.assertEqual(r.returncode, 0, msg=f"git {args} failed: {r.stderr}")
        return r.stdout

    def write(self, rel, text, root=None):
        p = (root or self.repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def gate(self, *args, stdin=None, extra=None, enabled=True, cwd=None):
        env = dict(self.env)
        if enabled:
            env["SPILL_GATE"] = "on"
        env.update(extra or {})
        return run([sys.executable, str(GATE), *args], cwd or self.repo, env, stdin)

    def allowlist(self, text):
        """The gate reads the allowlist from GIT (index / pushed tip), so stage it."""
        self.stage(".spill-gate-allow", text)

    def stage(self, rel, text):
        self.write(rel, text)
        self.git("add", rel)

    def commit(self, msg="c"):
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()


class PreCommitPositive(Base):
    def assert_blocks(self, text, rule):
        self.stage("f.txt", "x\n" + text + "\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn(rule, r.stderr)
        self.assertIn("f.txt:2", r.stderr)
        return r

    def test_each_rule_blocks(self):
        cases = [
            (PRIVATE_KEY, "private-key"), (AWS_KEY, "aws-access-key"), (GH_TOKEN, "github-token"),
            (SLACK, "slack-token"), (SK_ANT, "api-key-sk"), (STRIPE, "stripe-live-key"),
            (GOOGLE, "google-api-key"), (UUID, "uuid"), (MAC_PATH, "machine-path"),
            (LINUX_PATH, "machine-path"), (WIN_PATH, "machine-path"), (WIN_PATH_FWD, "machine-path"),
            ("BEGIN " + "OPENSSH PRIVATE" + " KEY", "private-key"),
        ]
        for text, rule in cases:
            with self.subTest(rule=rule, text=text[:8]):
                self.git("reset", "-q")
                self.assert_blocks("see " + text + " here", rule)

    def test_report_never_echoes_the_secret(self):
        r = self.assert_blocks(GH_TOKEN, "github-token")
        self.assertNotIn(GH_TOKEN, r.stderr)
        self.assertNotIn(GH_TOKEN[:12], r.stderr)

    def test_line_numbers_across_hunks(self):
        self.write("a.txt", "1\n2\n3\n4\n5\n")
        self.git("add", "a.txt")
        self.commit()
        self.write("a.txt", "1\nTWO\n3\n4\n" + UUID + "\n")
        self.git("add", "a.txt")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.txt:5", r.stderr)


class PreCommitNegative(Base):
    def test_clean_commit_allows(self):
        self.stage("f.txt", "just words, 1234-5678, /usr/local/bin, AKIA short, ghp_short\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_near_misses_allow(self):
        near = [
            "AKIA" + "SHORT", "ghp_" + "tooshort", "123e4567-e89b-12d3-a456-42661417400",
            "/Users/", "/Users/[A-Za-z]", "/home/", "/home/user", "xoxb-1", "sk-ant-short",
            "BEGIN PUBLIC KEY", "C:\\Windows\\System32",
        ]
        self.stage("f.txt", "\n".join(near) + "\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_inert_when_not_enabled(self):
        self.stage("f.txt", UUID + "\n")
        r = self.gate("pre-commit", enabled=False)
        self.assertEqual(r.returncode, 0)

    def test_bypass_allows_and_is_logged(self):
        self.stage("f.txt", UUID + "\n")
        logdir = Path(self._tmp.name) / "logs"
        r = self.gate("pre-commit", extra={"SPILL_GATE_BYPASS": "yes", "SPILL_GATE_LOG_DIR": str(logdir)})
        self.assertEqual(r.returncode, 0)
        self.assertIn('"decision": "bypass"', (logdir / "spill-gate.log").read_text())

    def test_bypasses_are_split_per_hook(self):
        self.stage("f.txt", UUID + "\n")
        # the commit bypass does not bypass the push backstop, and vice versa
        base = self.git("rev-parse", "HEAD").strip()
        tip = self.commit()
        line = "refs/heads/main %s refs/heads/main %s\n" % (tip, base)
        r = self.gate("pre-push", "origin", "u", stdin=line, extra={"SPILL_GATE_BYPASS": "on"})
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        r = self.gate("pre-push", "origin", "u", stdin=line, extra={"SPILL_GATE_PUSH_BYPASS": "on"})
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("BYPASSED", r.stderr)
        self.stage("g.txt", UUID + "\n")
        r = self.gate("pre-commit", extra={"SPILL_GATE_PUSH_BYPASS": "on"})
        self.assertEqual(r.returncode, 1, msg=r.stderr)

    def test_block_is_logged_without_secret(self):
        self.stage("f.txt", GH_TOKEN + "\n")
        logdir = Path(self._tmp.name) / "logs"
        r = self.gate("pre-commit", extra={"SPILL_GATE_LOG_DIR": str(logdir)})
        self.assertEqual(r.returncode, 1)
        log = (logdir / "spill-gate.log").read_text()
        self.assertIn('"decision": "block"', log)
        self.assertNotIn(GH_TOKEN, log)

    def test_removing_a_spill_is_not_blocked(self):
        # Already-committed spill (e.g. from before the gate) is removed: only '+' lines are scanned.
        self.write("f.txt", UUID + "\n")
        self.git("add", "f.txt")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "old")
        self.write("f.txt", "clean\n")
        self.git("add", "f.txt")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_deleted_file_ok(self):
        (self.repo / "README.txt").unlink()
        self.git("add", "-A")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_binary_and_utf16_content_is_scanned(self):
        # No NUL-skip: ASCII inside a NUL-bearing blob, and UTF-16LE text, still match.
        (self.repo / "b.bin").write_bytes(b"\0\0" + UUID.encode() + b"\0")
        self.git("add", "b.bin")
        self.assertEqual(self.gate("pre-commit").returncode, 1)
        self.git("reset", "-q")
        (self.repo / "u16.txt").write_bytes(("﻿" + GH_TOKEN + "\n").encode("utf-16-le"))
        self.git("add", "u16.txt")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("github-token", r.stderr)


class StagedVsWorktree(Base):
    def test_unstaged_spill_in_worktree_is_ignored(self):
        self.stage("f.txt", "clean\n")
        self.write("f.txt", "clean\n" + UUID + "\n")  # modified after staging, not re-added
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_staged_spill_blocks_even_if_worktree_is_clean(self):
        self.stage("f.txt", UUID + "\n")
        self.write("f.txt", "clean now\n")  # working tree fixed, index still dirty
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("f.txt:1", r.stderr)

    def test_untracked_file_is_ignored(self):
        self.write("untracked.txt", UUID + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_rename_into_unallowlisted_path_is_rescanned(self):
        self.write("fixtures/x.txt", UUID + "\n")
        self.allowlist("path fixtures/**\n")
        self.git("add", "-A")
        self.assertEqual(self.gate("pre-commit").returncode, 0)
        self.commit()
        self.git("mv", "fixtures/x.txt", "src_x.txt")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1, msg=r.stderr)

    def test_installed_as_hook_file_name_selects_mode(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_bytes(GATE.read_bytes())
        hook.chmod(0o755)
        self.stage("f.txt", UUID + "\n")
        r = run([sys.executable, str(hook)], self.repo, dict(self.env, SPILL_GATE="on"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("uuid", r.stderr)


class Allowlist(Base):
    def test_line_marker_all_rules(self):
        self.stage("f.txt", "id " + UUID + "  # spill-gate: allow\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_line_marker_scoped_to_rule(self):
        self.stage("f.txt", UUID + " " + MAC_PATH + " spill-gate: allow=uuid\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("machine-path", r.stderr)
        self.assertNotIn(" uuid ", r.stderr)

    def test_marker_applies_only_to_its_own_line(self):
        self.stage("f.txt", UUID + " spill-gate: allow\n" + UUID + "\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("f.txt:2", r.stderr)
        self.assertNotIn("f.txt:1", r.stderr)

    def test_path_glob_all_rules(self):
        self.allowlist("# fixtures\npath tests/fixtures/**\npath *.sample\n")
        self.stage("tests/fixtures/a/b.txt", UUID + "\n" + MAC_PATH + "\n")
        self.stage("deep/dir/k.sample", GH_TOKEN + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_path_glob_does_not_overmatch(self):
        self.allowlist("path tests/fixtures/*.txt\n")
        self.stage("tests/fixtures/sub/b.txt", UUID + "\n")  # '*' does not cross '/'
        self.assertEqual(self.gate("pre-commit").returncode, 1)

    def test_path_with_rules_only_exempts_those_rules(self):
        self.allowlist("path docs/** rules=uuid\n")
        self.stage("docs/a.md", UUID + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)
        self.stage("docs/b.md", MAC_PATH + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 1)

    def test_literal_exception(self):
        self.allowlist("literal " + UUID + "\n")
        self.stage("f.txt", UUID + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)
        other = UUID[:-1] + "1"
        self.stage("g.txt", other + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 1)

    def test_custom_allowlist_path_env(self):
        al = Path(self._tmp.name) / "elsewhere.allow"
        al.write_text("path f.txt\n", encoding="utf-8")
        self.stage("f.txt", UUID + "\n")
        r = self.gate("pre-commit", extra={"SPILL_GATE_ALLOWLIST": str(al)})
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_malformed_allowlist_fails_closed(self):
        for bad in ("bogus line here\n", "path\n", "path x rules=nonsense\n", "path x notrules\n", "literal\n"):
            with self.subTest(bad=bad):
                self.allowlist(bad)
                self.stage("f.txt", "clean\n")
                r = self.gate("pre-commit")
                self.assertEqual(r.returncode, 1, msg=r.stderr)
                self.assertIn("fail-closed", r.stderr)

    def test_missing_explicit_allowlist_fails_closed(self):
        self.stage("f.txt", "clean\n")
        r = self.gate("pre-commit", extra={"SPILL_GATE_ALLOWLIST": str(Path(self._tmp.name) / "nope")})
        self.assertEqual(r.returncode, 1)

    def test_not_a_git_repo_fails_closed(self):
        outside = Path(self._tmp.name) / "plain"
        outside.mkdir()
        r = self.gate("pre-commit", cwd=outside)
        self.assertEqual(r.returncode, 1)


class PrePush(Base):
    def push_line(self, local_sha, remote_sha, ref="refs/heads/main"):
        return f"{ref} {local_sha} {ref} {remote_sha}\n"

    def bare_remote(self):
        remote = Path(self._tmp.name) / "remote.git"
        run(["git", "init", "-q", "--bare", str(remote)], self._tmp.name, self.env)
        self.git("remote", "add", "origin", str(remote))
        return remote

    def test_clean_range_allows(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.write("a.txt", "fine\n")
        self.git("add", "-A")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, base))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_spill_in_pushed_commit_blocks(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.write("a.txt", GH_TOKEN + "\n")
        self.git("add", "-A")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, base))
        self.assertEqual(r.returncode, 1)
        self.assertIn("github-token", r.stderr)
        self.assertIn(tip[:8], r.stderr)

    def test_spill_added_then_removed_still_blocks(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.write("a.txt", UUID + "\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "oops")
        self.write("a.txt", "clean\n")
        self.git("add", "-A")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, base))
        self.assertEqual(r.returncode, 1, msg="history still carries the spill")

    def test_only_range_is_scanned_not_older_history(self):
        self.write("old.txt", UUID + "\n")
        self.git("add", "-A")
        old = self.commit("old")
        self.write("new.txt", "fine\n")
        self.git("add", "-A")
        tip = self.commit("new")
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, old))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_new_branch_no_remote_base_scans_everything(self):
        self.bare_remote()  # remote exists but has no refs yet
        self.write("a.txt", MAC_PATH + "\n")
        self.git("add", "-A")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, "0" * 40))
        self.assertEqual(r.returncode, 1)
        self.assertIn("machine-path", r.stderr)

    def test_new_branch_excludes_commits_already_on_remote(self):
        remote = self.bare_remote()
        self.write("old.txt", UUID + "\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "old")
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")  # remote tracking ref origin/main now exists
        self.git("checkout", "-q", "-b", "feature")
        self.write("new.txt", "fine\n")
        self.git("add", "-A")
        tip = self.commit("new")
        r = self.gate("pre-push", "origin", str(remote), stdin=self.push_line(tip, "0" * 40, "refs/heads/feature"))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # ...and a spill on the new branch is caught
        self.write("bad.txt", UUID + "\n")
        self.git("add", "-A")
        tip2 = self.commit("bad")
        r = self.gate("pre-push", "origin", str(remote), stdin=self.push_line(tip2, "0" * 40, "refs/heads/feature"))
        self.assertEqual(r.returncode, 1)

    def test_root_commit_is_scanned(self):
        # A push whose range includes the very first commit ('--root').
        solo = Path(self._tmp.name) / "solo"
        solo.mkdir()
        self.git("init", "-q", cwd=solo)
        self.write("a.txt", UUID + "\n", root=solo)
        self.git("add", "-A", cwd=solo)
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "r", cwd=solo)
        tip = self.git("rev-parse", "HEAD", cwd=solo).strip()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, "0" * 40), cwd=solo)
        self.assertEqual(r.returncode, 1)

    def test_deletion_push_allows(self):
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line("0" * 40, "a" * 40))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_garbage_stdin_fails_closed(self):
        r = self.gate("pre-push", "origin", "url", stdin="nonsense\n")
        self.assertEqual(r.returncode, 1)

    def test_empty_stdin_allows(self):
        self.assertEqual(self.gate("pre-push", "origin", "url", stdin="").returncode, 0)

    def test_allowlist_applies_on_push(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.allowlist("path fx/**\n")
        self.write("fx/a.txt", UUID + "\n")
        self.git("add", "-A")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "url", stdin=self.push_line(tip, base))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    @unittest.skipIf(os.name == "nt", "sh hook wrapper; stdin contract covered above")
    def test_real_git_push_is_blocked_by_hook(self):
        remote = self.bare_remote()
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text('#!/bin/sh\nexec "%s" "%s" pre-push "$@"\n' % (sys.executable, GATE), encoding="utf-8")
        hook.chmod(0o755)
        self.env["SPILL_GATE"] = "on"
        self.write("a.txt", UUID + "\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "bad")
        r = run(["git", "push", "origin", "HEAD:refs/heads/main"], self.repo, self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BLOCKED", r.stderr)
        listing = run(["git", "--git-dir", str(remote), "branch"], self.repo, self.env)
        self.assertEqual(listing.stdout.strip(), "", msg="nothing reached the remote")
        # clean the history, push again -> goes through
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.write("ok.txt", "fine\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "ok")
        r = run(["git", "push", "origin", "HEAD:refs/heads/main"], self.repo, self.env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)


class ScanTree(Base):
    def test_scan_tree_reports_committed_files(self):
        self.write("a.txt", "x\n" + AWS_KEY + "\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "a")
        r = self.gate("scan-tree", enabled=False)  # manual audit works without the hook switch
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.txt:2", r.stderr)
        self.assertIn("aws-access-key", r.stderr)

    def test_scan_tree_clean_and_allowlisted(self):
        self.assertEqual(self.gate("scan-tree").returncode, 0)
        self.write("a.txt", UUID + " spill-gate: allow\n")
        self.git("add", "-A")
        self.commit()
        self.assertEqual(self.gate("scan-tree").returncode, 0)

    def test_scan_tree_rev_and_usage(self):
        first = self.git("rev-parse", "HEAD").strip()
        self.write("a.txt", UUID + "\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", "a")
        self.assertEqual(self.gate("scan-tree", "--rev", first).returncode, 0)
        self.assertEqual(self.gate("scan-tree", "--rev", "HEAD").returncode, 1)
        self.assertEqual(self.gate("scan-tree", "--bogus").returncode, 2)
        self.assertEqual(self.gate("frobnicate").returncode, 2)


def load_gate_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sg_under_test", GATE)
    sg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sg)
    return sg


class ReviewFixes(Base):
    """Regression tests for the independent review of the first commit."""

    def blob(self, text):
        r = run(["git", "hash-object", "-w", "--stdin"], self.repo, self.env, stdin=text)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return r.stdout.strip()

    # 1. -diff / binary attributes must not hide a file
    def test_diff_and_binary_attributes_do_not_hide_files_on_commit(self):
        self.stage(".gitattributes", "*.dat -diff\n*.bin binary\n")
        self.stage("x.dat", "id " + UUID + "\n")
        self.stage("y.bin", "id " + UUID + "\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("x.dat:1", r.stderr)
        self.assertIn("y.bin:1", r.stderr)

    def test_diff_attribute_does_not_hide_files_on_push(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.stage(".gitattributes", "*.dat -diff\n")
        self.stage("x.dat", "id " + UUID + "\n")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "u", stdin="r %s r %s\n" % (tip, base))
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("x.dat:1", r.stderr)

    # 2. typechange (symlink -> regular file)
    def test_typechange_symlink_to_file_is_scanned(self):
        run(["git", "update-index", "--add", "--cacheinfo", "120000,%s,link" % self.blob("target"), ],
            self.repo, self.env)
        self.commit("link")
        run(["git", "update-index", "--cacheinfo", "100644,%s,link" % self.blob("v " + UUID + "\n")],
            self.repo, self.env)
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("link:1", r.stderr)

    # 3. merge commits on push
    def _merge_with(self, resolution_text):
        main = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.stage("shared.txt", "base\n")
        base = self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        self.stage("feature.txt", "f\n")
        self.commit("feature")
        self.git("checkout", "-q", main)
        self.stage("m.txt", "m\n")
        self.commit("main")
        self.git("merge", "-q", "--no-ff", "--no-commit", "feature")
        self.write("shared.txt", "base\n" + resolution_text)
        self.git("add", "shared.txt")
        self.git("commit", "-q", "-m", "merge")
        return base, self.git("rev-parse", "HEAD").strip()

    def test_secret_introduced_in_merge_resolution_blocks_push(self):
        base, tip = self._merge_with(UUID + "\n")
        r = self.gate("pre-push", "origin", "u", stdin="r %s r %s\n" % (tip, base))
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn(tip[:8], r.stderr)
        self.assertIn("shared.txt:2", r.stderr)

    def test_clean_merge_push_allows(self):
        base, tip = self._merge_with("resolved\n")
        r = self.gate("pre-push", "origin", "u", stdin="r %s r %s\n" % (tip, base))
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    # 4 + 14. user diff config, context lines, state reset between files
    def test_user_diff_config_is_neutralized(self):
        self.git("config", "diff.interHunkContext", "10")
        self.git("config", "diff.noprefix", "true")
        self.git("config", "diff.mnemonicPrefix", "true")
        self.git("config", "diff.suppressBlankEmpty", "true")
        self.allowlist("path fx/**\n")
        self.write("fx/a.txt", "l1\nl2\nl3\nl4\nl5\n")
        self.git("add", "fx/a.txt")
        self.commit()
        self.write("fx/a.txt", "L1\nl2\nL3\nl4\nl5\n")
        self.git("add", "fx/a.txt")
        self.stage("z.txt", UUID + "\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("z.txt:1", r.stderr)
        self.assertNotIn("fx/", r.stderr)

    def test_parser_counts_context_lines_and_resets_per_file(self):
        sg = load_gate_module()
        diff = (
            "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,5 +1,5 @@\n"
            " c1\n-c2\n+n2\n c3\n c4\n+" + UUID + "\n"
            "diff --git a/fx/b.txt b/fx/b.txt\n--- a/fx/b.txt\n+++ b/fx/b.txt\n@@ -0,0 +1 @@\n+" + UUID + "\n"
        ).encode()
        al = sg.parse_allowlist(None, "path fx/**\n", "t")
        found = sg.scan_diff(diff, al)
        self.assertEqual([(f[1], f[2], f[3]) for f in found], [("a.txt", 5, "uuid")])

    def test_combined_diff_only_flags_lines_new_to_every_parent(self):
        sg = load_gate_module()
        diff = (
            "diff --cc shared.txt\n--- a/shared.txt\n--- a/shared.txt\n+++ b/shared.txt\n@@@ -1,2 -1,2 +1,3 @@@\n"
            "  base\n+ " + GH_TOKEN + "\n++" + UUID + "\n"
        ).encode()
        found = sg.scan_diff(diff, sg.Allowlist())
        self.assertEqual([(f[2], f[3]) for f in found], [(3, "uuid")])

    # 5. allowlist comes from git, not the working tree
    def test_untracked_or_unstaged_allowlist_does_not_loosen_the_gate(self):
        self.write(".spill-gate-allow", "path **\n")          # on disk only
        self.stage("f.txt", UUID + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 1)
        self.allowlist("path nothing-matches\n")               # staged narrow version
        self.write(".spill-gate-allow", "path **\n")          # then widened, unstaged
        self.assertEqual(self.gate("pre-commit").returncode, 1)
        self.git("add", ".spill-gate-allow")                   # staging it makes it count
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_push_uses_the_allowlist_of_the_pushed_tip(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.write(".spill-gate-allow", "path **\n")          # untracked: not in the pushed tip
        self.stage("f.txt", UUID + "\n")
        tip = self.commit()
        r = self.gate("pre-push", "origin", "u", stdin="r %s r %s\n" % (tip, base))
        self.assertEqual(r.returncode, 1, msg=r.stderr)

    def test_explicit_override_reads_disk(self):
        al = Path(self._tmp.name) / "override.allow"
        al.write_text("path **\n", encoding="utf-8")
        self.stage("f.txt", UUID + "\n")
        self.assertEqual(self.gate("pre-commit", extra={"SPILL_GATE_ALLOWLIST": str(al)}).returncode, 0)

    # 6. CI parity: every CI pattern sample is caught, including glued to identifiers
    def test_every_ci_pattern_sample_is_caught(self):
        sg = load_gate_module()
        samples = [
            "BEGIN " + "PRIVATE KEY", "BEGIN " + "RSA PRIVATE KEY", "BEGIN " + "EC PRIVATE KEY",
            "BEGIN " + "OPENSSH PRIVATE KEY", "BEGIN " + "PGP PRIVATE KEY",
            AWS_KEY, "AWS_KEY_" + AWS_KEY, AWS_KEY + "lowercase", "x" + AWS_KEY,
            GH_TOKEN, "TOKEN_" + GH_TOKEN, "x" + GH_TOKEN, GH_TOKEN + "extra",
            UUID, "id" + UUID,
            "/" + "Users" + "/a", "x/" + "Users" + "/Zed", "C" + ":\\" + "Users", "Z" + ":\\\\" + "Users",
        ]
        for s in samples:
            with self.subTest(sample=s[:10]):
                self.assertTrue(sg.scan_line("f.txt", 1, "v=" + s, sg.Allowlist()), msg=s)

    # 7. machine-path captures the whole user segment
    def test_literal_for_one_user_does_not_allow_longer_names(self):
        self.allowlist("literal /" + "Users" + "/j\n")
        self.stage("f.txt", "/" + "Users" + "/john/x\n")
        self.assertEqual(self.gate("pre-commit").returncode, 1)
        self.allowlist("literal /" + "Users" + "/john\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_windows_and_home_literals_need_the_full_segment(self):
        sg = load_gate_module()
        al = sg.Allowlist()
        al.literals.add("C" + ":\\" + "Users" + "\\j")
        al.literals.add("/" + "home" + "/j/")
        self.assertTrue(sg.scan_line("f", 1, WIN_PATH, al))
        self.assertTrue(sg.scan_line("f", 1, LINUX_PATH, al))
        al.literals.add("C" + ":\\" + "Users" + "\\carol")
        al.literals.add("/" + "home" + "/bob/")
        self.assertFalse(sg.scan_line("f", 1, WIN_PATH, al))
        self.assertFalse(sg.scan_line("f", 1, LINUX_PATH, al))

    # 10. scan-tree with a submodule (gitlink)
    def test_scan_tree_skips_submodule_entries(self):
        sha = self.git("rev-parse", "HEAD").strip()
        run(["git", "update-index", "--add", "--cacheinfo", "160000,%s,sub" % sha], self.repo, self.env)
        self.commit("gitlink")
        r = self.gate("scan-tree")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("nothing flagged", r.stderr)

    # 13. marker must be exact; unknown rule fails closed
    def test_marker_must_be_exact(self):
        for tail in ("spill-gate: allowed", "spill-gate:allow", "spill-gate: allow=", "spill-gate: allow=uuid,",
                     "spill-gate: allowx", "spill-gate: " + "allow=uuid_x"):
            with self.subTest(tail=tail):
                self.git("reset", "-q")
                self.stage("f.txt", UUID + " " + tail + "\n")
                r = self.gate("pre-commit")
                self.assertEqual(r.returncode, 1, msg=r.stderr)

    def test_marker_with_unknown_rule_fails_closed(self):
        self.stage("f.txt", UUID + " spill-gate: " + "allow=uuuid\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown rule", r.stderr)

    def test_valid_marker_forms_still_work(self):
        self.stage("f.txt", UUID + " # spill-gate: allow\n" + UUID + " /* spill-gate: allow=uuid */\n"
                   + UUID + " <!-- spill-gate: allow=uuid,machine-path -->\n")
        r = self.gate("pre-commit")
        self.assertEqual(r.returncode, 0, msg=r.stderr)


class SelfCheck(unittest.TestCase):
    def test_modules_own_files_carry_no_literal_spill(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sg", GATE)
        sg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sg)
        al = sg.Allowlist()
        for p in (GATE, Path(__file__).resolve()):
            found = sg.scan_blob(p.name, p.read_bytes(), al)
            self.assertEqual(found, [], msg=f"{p.name}: {[(f[2], f[3]) for f in found]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
