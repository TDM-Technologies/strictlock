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
        self.assertIn("bypass", (logdir / "spill-gate.log").read_text())

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

    def test_deleted_file_ok_and_binary_skipped(self):
        (self.repo / "README.txt").unlink()
        self.git("add", "-A")
        self.assertEqual(self.gate("pre-commit").returncode, 0)
        self.write("b.bin", "\0\0" + UUID + "\0")
        self.git("add", "b.bin")
        self.assertEqual(self.gate("pre-commit").returncode, 0)


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
        self.write(".spill-gate-allow", "path fixtures/**\n")
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
        self.write(".spill-gate-allow", "# fixtures\npath tests/fixtures/**\npath *.sample\n")
        self.stage("tests/fixtures/a/b.txt", UUID + "\n" + MAC_PATH + "\n")
        self.stage("deep/dir/k.sample", GH_TOKEN + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)

    def test_path_glob_does_not_overmatch(self):
        self.write(".spill-gate-allow", "path tests/fixtures/*.txt\n")
        self.stage("tests/fixtures/sub/b.txt", UUID + "\n")  # '*' does not cross '/'
        self.assertEqual(self.gate("pre-commit").returncode, 1)

    def test_path_with_rules_only_exempts_those_rules(self):
        self.write(".spill-gate-allow", "path docs/** rules=uuid\n")
        self.stage("docs/a.md", UUID + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 0)
        self.stage("docs/b.md", MAC_PATH + "\n")
        self.assertEqual(self.gate("pre-commit").returncode, 1)

    def test_literal_exception(self):
        self.write(".spill-gate-allow", "literal " + UUID + "\n")
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
                self.write(".spill-gate-allow", bad)
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
        self.write(".spill-gate-allow", "path fx/**\n")
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
