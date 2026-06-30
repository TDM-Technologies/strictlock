#!/usr/bin/env python3
"""Standalone test suite for test-protection-guard (dual-mode advisory guard).

Stdlib `unittest`, no third-party dependencies. Each test builds a REAL git repo
in a tempdir with a committed test+source pair, stages an edit, and invokes the
shipped guard as a subprocess — in BOTH of its I/O modes:

  * PreToolUse: a tool-call JSON payload on stdin -> an `allow` decision on
    stdout (the guard NEVER denies; cases are told apart by the reason substring
    and by the stderr warning on a flag).
  * git pre-commit: empty stdin, run in-tree -> stderr warning + exit 0 (always).

It proves behaviour end to end — a real co-commit of a changed assertion + source
actually flags, an additions-only edit does not, the token suppresses the flag —
and that the same core works across JS, Python, and Rust via the default globs.

Run:  python3 test-protection-guard/tests/test_test_protection_guard.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD = HERE.parent / "test-protection-guard.py"


def load_guard():
    spec = importlib.util.spec_from_file_location("tpg_module", GUARD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

# Per-language (test_filename, source_filename, baseline_test, changed_test,
# added_test, baseline_source, changed_source). The changed_test alters an
# EXISTING assertion's expected value; added_test only APPENDS a new assertion.
LANGS = {
    "js": (
        "foo.test.ts", "foo.ts",
        "it('computes', () => { expect(compute()).toBe(5); });\n",
        "it('computes', () => { expect(compute()).toBe(4); });\n",
        "it('computes', () => { expect(compute()).toBe(5); });\n"
        "it('again', () => { expect(compute()).toBe(5); });\n",
        "export const compute = () => 5;\n",
        "export const compute = () => 4;\n",
    ),
    "py": (
        "test_foo.py", "foo.py",
        "def test_computes():\n    assert compute() == 5\n",
        "def test_computes():\n    assert compute() == 4\n",
        "def test_computes():\n    assert compute() == 5\n\ndef test_again():\n    assert compute() == 5\n",
        "def compute():\n    return 5\n",
        "def compute():\n    return 4\n",
    ),
    "rust": (
        "calc_test.rs", "calc.rs",
        "#[test]\nfn computes() { assert_eq!(compute(), 5); }\n",
        "#[test]\nfn computes() { assert_eq!(compute(), 4); }\n",
        "#[test]\nfn computes() { assert_eq!(compute(), 5); }\n"
        "#[test]\nfn again() { assert_eq!(compute(), 5); }\n",
        "pub fn compute() -> i32 { 5 }\n",
        "pub fn compute() -> i32 { 4 }\n",
    ),
}


def git(repo: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=env)


class GuardTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.log = self.root / "guard.log"
        env = dict(os.environ)
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            env.pop(k, None)
        env.update({
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.test",
        })
        self.git_env = env
        git(self.repo, "init", "-q", env=env)
        git(self.repo, "config", "commit.gpgsign", "false", env=env)

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixture construction ------------------------------------------------
    def seed(self, lang="js"):
        tf, sf, base_t, _ch_t, _add_t, base_s, _ch_s = LANGS[lang]
        (self.repo / tf).write_text(base_t, encoding="utf-8")
        (self.repo / sf).write_text(base_s, encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "baseline", env=self.git_env)
        return tf, sf

    def stage(self, lang="js", mode="change", with_token=False, stage_source=True):
        """mode: 'change' (alter the existing assertion) | 'add' (append only)."""
        tf, sf, _b_t, ch_t, add_t, _b_s, ch_s = LANGS[lang]
        if mode == "change":
            token = "// TEST-CORRECTNESS: spec changed; 4 is correct now\n" if with_token else ""
            (self.repo / tf).write_text(token + ch_t, encoding="utf-8")
        elif mode == "add":
            (self.repo / tf).write_text(add_t, encoding="utf-8")
        else:
            raise ValueError(mode)
        if stage_source:
            (self.repo / sf).write_text(ch_s, encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        return tf, sf

    # --- invocation ----------------------------------------------------------
    def guard_env(self, **overrides):
        env = dict(self.git_env)
        env["TEST_PROTECTION_GUARD"] = "on"
        env["TEST_PROTECTION_GUARD_LOG"] = str(self.log)
        for k, v in overrides.items():
            env[k] = v
        return env

    def run_pretooluse(self, command, env=None, cwd=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                           capture_output=True, text=True,
                           env=env or self.guard_env(), cwd=str(cwd or self.repo))
        try:
            hso = json.loads(p.stdout)["hookSpecificOutput"]
            return hso["permissionDecision"], hso["permissionDecisionReason"], p.stderr, p.returncode
        except Exception:
            return "PARSE_ERROR", (p.stdout + p.stderr), p.stderr, p.returncode

    def run_precommit(self, env=None, cwd=None, stdin=""):
        p = subprocess.run([sys.executable, str(GUARD)], input=stdin,
                           capture_output=True, text=True,
                           env=env or self.guard_env(), cwd=str(cwd or self.repo))
        return p.stderr, p.returncode

    def log_rows(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]


# ============================================================================ #
# PreToolUse mode
# ============================================================================ #
class PreToolUseTests(GuardTestBase):
    def test_gate_off_is_inert(self):
        dec, reason, _err, code = self.run_pretooluse(
            "git commit -m x", env=dict(self.git_env, TEST_PROTECTION_GUARD="off"))
        self.assertEqual(dec, "allow")
        self.assertIn("inert", reason.lower())
        self.assertEqual(code, 0)

    def test_not_bash_allows(self):
        p = subprocess.run([sys.executable, str(GUARD)],
                           input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x"}}),
                           capture_output=True, text=True, env=self.guard_env(), cwd=str(self.repo))
        self.assertIn("not a bash", json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"].lower())

    def test_not_git_commit_allows(self):
        dec, reason, _e, _c = self.run_pretooluse("git status")
        self.assertEqual(dec, "allow")
        self.assertIn("not a `git commit`", reason)

    def test_changed_assertion_no_token_flags(self):
        self.seed("js"); self.stage("js", "change", with_token=False)
        dec, reason, err, code = self.run_pretooluse("git commit -m 'fix compute'")
        self.assertEqual(dec, "allow")          # advisory: still allows
        self.assertIn("flagged", reason.lower())
        self.assertIn("test-protection-guard", err)   # loud stderr warning
        self.assertEqual(code, 0)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "flagged")
        self.assertEqual(rows[0]["mode"], "pretooluse")

    def test_inline_token_justifies(self):
        self.seed("js"); self.stage("js", "change", with_token=True)
        dec, reason, err, _c = self.run_pretooluse("git commit -m 'spec change'")
        self.assertEqual(dec, "allow")
        self.assertIn("justification", reason.lower())
        self.assertNotIn("⚠", err)
        self.assertEqual(self.log_rows()[0]["decision"], "justified")

    def test_commit_message_token_justifies(self):
        # The token in the -m message (not the file) counts in PreToolUse mode.
        self.seed("js"); self.stage("js", "change", with_token=False)
        _d, reason, _e, _c = self.run_pretooluse("git commit -m 'TEST-CORRECTNESS: 4 is right now'")
        self.assertIn("justification", reason.lower())
        self.assertEqual(self.log_rows()[0]["decision"], "justified")

    def test_additions_only_not_flagged(self):
        self.seed("js"); self.stage("js", "add")
        _d, reason, _e, _c = self.run_pretooluse("git commit -m 'add test'")
        self.assertIn("assertions only", reason.lower())
        self.assertEqual(self.log_rows(), [])

    def test_no_source_no_coupling(self):
        self.seed("js"); self.stage("js", "change", stage_source=False)
        _d, reason, _e, _c = self.run_pretooluse("git commit -m 'test only'")
        self.assertIn("no test+source", reason.lower())
        self.assertEqual(self.log_rows(), [])

    def test_cross_worktree_cd_prefix_resolves_and_flags(self):
        self.seed("js"); self.stage("js", "change")
        foreign = self.root / "foreign"; foreign.mkdir()
        posix = str(self.repo).replace("\\", "/")
        _d, reason, _e, _c = self.run_pretooluse(f'cd "{posix}" && git commit -m fix', cwd=foreign)
        self.assertIn("flagged", reason.lower())
        self.assertEqual(self.log_rows()[0]["decision"], "flagged")

    def test_outside_git_repo_allows(self):
        outside = self.root / "outside"; outside.mkdir()
        dec, reason, _e, _c = self.run_pretooluse("git commit -m x", cwd=outside)
        self.assertEqual(dec, "allow")
        self.assertIn("not inside a git repo", reason.lower())


# ============================================================================ #
# git pre-commit mode
# ============================================================================ #
class PreCommitTests(GuardTestBase):
    def test_gate_off_silent_allow(self):
        self.seed("js"); self.stage("js", "change")
        err, code = self.run_precommit(env=dict(self.git_env, TEST_PROTECTION_GUARD="off"))
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), "")

    def test_changed_assertion_no_token_warns_and_logs(self):
        self.seed("js"); self.stage("js", "change", with_token=False)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)                # advisory: never blocks
        self.assertIn("test-protection-guard", err)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "flagged")
        self.assertEqual(rows[0]["mode"], "precommit")

    def test_log_is_compact_jsonl(self):
        # The documented grep patterns (e.g. '"decision":"flagged"') assume compact
        # JSONL with no separator spaces. Lock it so the docs can't silently rot.
        self.seed("js"); self.stage("js", "change")
        self.run_precommit()
        raw = self.log.read_text(encoding="utf-8").strip()
        self.assertIn('"decision":"flagged"', raw)
        self.assertNotIn('"decision": "flagged"', raw)

    def test_inline_token_no_warning(self):
        self.seed("js"); self.stage("js", "change", with_token=True)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertNotIn("⚠", err)
        self.assertEqual(self.log_rows()[0]["decision"], "justified")

    def test_additions_only_quiet(self):
        self.seed("js"); self.stage("js", "add")
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertNotIn("⚠", err)
        self.assertEqual(self.log_rows(), [])

    def test_no_source_quiet(self):
        self.seed("js"); self.stage("js", "change", stage_source=False)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertNotIn("⚠", err)

    def test_outside_git_repo_exit_zero(self):
        outside = self.root / "outside"; outside.mkdir()
        err, code = self.run_precommit(cwd=outside)
        self.assertEqual(code, 0)


# ============================================================================ #
# Mode detection
# ============================================================================ #
class ModeDetectionTests(GuardTestBase):
    def test_empty_stdin_is_precommit(self):
        # No payload, but staged coupling: pre-commit path must flag (stderr warn).
        self.seed("js"); self.stage("js", "change")
        err, code = self.run_precommit(stdin="")
        self.assertEqual(code, 0)
        self.assertIn("test-protection-guard", err)
        self.assertEqual(self.log_rows()[0]["mode"], "precommit")

    def test_non_json_stdin_is_precommit(self):
        self.seed("js"); self.stage("js", "change")
        err, code = self.run_precommit(stdin="this is not json\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["mode"], "precommit")

    def test_forced_precommit_ignores_json_payload(self):
        # A JSON payload arrives, but the mode is pinned to precommit: it must NOT
        # emit PreToolUse JSON on stdout; it analyses the staged diff directly.
        self.seed("js"); self.stage("js", "change")
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}})
        p = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True,
                           env=self.guard_env(TEST_PROTECTION_GUARD_MODE="precommit"), cwd=str(self.repo))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")          # no PreToolUse JSON
        self.assertIn("test-protection-guard", p.stderr)  # warned via stderr
        self.assertEqual(self.log_rows()[0]["mode"], "precommit")

    def test_forced_pretooluse_on_empty_stdin(self):
        # Forcing pretooluse with empty stdin -> unparseable payload -> advisory allow JSON.
        p = subprocess.run([sys.executable, str(GUARD)], input="", capture_output=True, text=True,
                           env=self.guard_env(TEST_PROTECTION_GUARD_MODE="pretooluse"), cwd=str(self.repo))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "allow")


# ============================================================================ #
# Cross-language genericization + config overrides
# ============================================================================ #
class GenericizationTests(GuardTestBase):
    def test_python_assertEqual_change_flags(self):
        self.seed("py"); self.stage("py", "change")
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["decision"], "flagged")
        self.assertTrue(any(f.endswith(".py") for f in self.log_rows()[0]["test_files"]))

    def test_rust_assert_eq_change_flags(self):
        self.seed("rust"); self.stage("rust", "change")
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["decision"], "flagged")

    def test_custom_globs_and_regex(self):
        # An exotic stack: *.check files are "tests", *.impl are "source", and the
        # assertion vocabulary is VERIFY(...). Prove the env knobs fully retarget it.
        (self.repo / "a.check").write_text("VERIFY(compute() == 5)\n", encoding="utf-8")
        (self.repo / "a.impl").write_text("compute = 5\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "baseline", env=self.git_env)
        (self.repo / "a.check").write_text("VERIFY(compute() == 4)\n", encoding="utf-8")
        (self.repo / "a.impl").write_text("compute = 4\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        env = self.guard_env(
            TEST_PROTECTION_GUARD_TEST_GLOBS="*.check",
            TEST_PROTECTION_GUARD_SOURCE_GLOBS="*.impl",
            TEST_PROTECTION_GUARD_ASSERTION_RE=r"\bVERIFY\s*\(",
        )
        err, code = self.run_precommit(env=env)
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["decision"], "flagged")

    def test_custom_token(self):
        self.seed("js")
        tf, _sf = LANGS["js"][0], LANGS["js"][1]
        (self.repo / tf).write_text("// SPEC-CHANGED: ok\nit('c', () => { expect(compute()).toBe(4); });\n",
                                    encoding="utf-8")
        (self.repo / "foo.ts").write_text("export const compute = () => 4;\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        err, code = self.run_precommit(env=self.guard_env(TEST_PROTECTION_GUARD_TOKEN="SPEC-CHANGED:"))
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["decision"], "justified")

    def test_log_dir_env_writes_named_file(self):
        # The house _LOG_DIR convention: a dir, with the file named for the module.
        self.seed("js"); self.stage("js", "change")
        log_dir = self.root / "logs"
        env = dict(self.git_env, TEST_PROTECTION_GUARD="on", TEST_PROTECTION_GUARD_LOG_DIR=str(log_dir))
        env.pop("TEST_PROTECTION_GUARD_LOG", None)
        self.run_precommit(env=env)
        self.assertTrue((log_dir / "test-protection-guard.log").exists())


# ============================================================================ #
# git-commit detection helper (in-process unit) — verify findings L3-2 / L3-3
# ============================================================================ #
class GitCommitDetectionTests(unittest.TestCase):
    def setUp(self):
        self.m = load_guard()

    def test_plain_commit(self):
        self.assertTrue(self.m._is_git_commit("git commit -m x"))

    def test_dash_C_commit(self):
        self.assertTrue(self.m._is_git_commit("git -C /some/repo commit -m x"))

    def test_dash_c_config_commit(self):
        self.assertTrue(self.m._is_git_commit("git -c user.name=x commit"))

    def test_env_prefix_commit(self):
        self.assertTrue(self.m._is_git_commit("FOO=1 git commit"))

    def test_valueless_global_flag(self):
        self.assertTrue(self.m._is_git_commit("git --no-pager commit -m x"))

    def test_commit_tree_rejected(self):
        self.assertFalse(self.m._is_git_commit("git commit-tree abc123"))

    def test_commit_prefix_subcommand_rejected(self):
        self.assertFalse(self.m._is_git_commit("git commitfoo -m x"))

    def test_status_rejected(self):
        self.assertFalse(self.m._is_git_commit("git status"))

    def test_not_git_rejected(self):
        self.assertFalse(self.m._is_git_commit("echo git commit"))

    def test_message_with_separators_preserved(self):
        self.assertTrue(self.m._is_git_commit('git commit -m "fix && polish"'))


# ============================================================================ #
# Hardening + signal-quality fixes from the adversarial verify
# ============================================================================ #
class VerifyHardeningTests(GuardTestBase):
    def _raw_pretooluse(self, payload, cwd=None):
        return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True,
                              env=self.guard_env(), cwd=str(cwd or self.repo))

    def test_non_dict_tool_input_allows_without_crash(self):
        # L3-1: a structurally-valid payload with a non-dict tool_input must be
        # handled as input — allow JSON, no traceback via the crash net.
        p = self._raw_pretooluse({"tool_name": "Bash", "tool_input": "git commit"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertNotIn("Traceback", p.stderr)

    def test_non_string_command_allows_without_crash(self):
        p = self._raw_pretooluse({"tool_name": "Bash", "tool_input": {"command": 123}})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertNotIn("Traceback", p.stderr)

    def test_git_C_commit_flags_in_pretooluse(self):
        # L3-2: `git -C <dir> commit` is now recognised as a commit.
        self.seed("js"); self.stage("js", "change")
        dec, reason, _e, _c = self.run_pretooluse("git -c core.pager=cat commit -m fix")
        self.assertEqual(dec, "allow")
        self.assertIn("flagged", reason.lower())

    def test_commit_tree_not_treated_as_commit(self):
        # L3-3: `git commit-tree` no longer over-triggers.
        self.seed("js"); self.stage("js", "change")
        dec, reason, _e, _c = self.run_pretooluse("git commit-tree deadbeef")
        self.assertEqual(dec, "allow")
        self.assertIn("not a `git commit`", reason)
        self.assertEqual(self.log_rows(), [])

    def test_comment_with_assert_word_not_flagged(self):
        # L2-3: the bare word 'assert' in a comment must NOT match (only assertion
        # syntax does). Change only a comment + source; the assertion is untouched.
        (self.repo / "foo.test.ts").write_text(
            "// we assert correctness in the integration suite\n"
            "it('computes', () => { expect(compute()).toBe(5); });\n", encoding="utf-8")
        (self.repo / "foo.ts").write_text("export const compute = () => 5;\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "baseline", env=self.git_env)
        (self.repo / "foo.test.ts").write_text(
            "// we verify correctness in the integration suite\n"   # 'assert' -> 'verify'
            "it('computes', () => { expect(compute()).toBe(5); });\n", encoding="utf-8")
        (self.repo / "foo.ts").write_text("export const compute = () => 6;\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertNotIn("⚠", err)
        self.assertEqual(self.log_rows(), [])

    def test_go_testify_assert_dot_still_caught(self):
        # Regression for the regex change: Go testify `assert.Equal(...)` must
        # still match via the `assert.`/`require.` branch.
        (self.repo / "calc_test.go").write_text(
            'func TestC(t *testing.T) { assert.Equal(t, 5, compute()) }\n', encoding="utf-8")
        (self.repo / "calc.go").write_text("func compute() int { return 5 }\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "baseline", env=self.git_env)
        (self.repo / "calc_test.go").write_text(
            'func TestC(t *testing.T) { assert.Equal(t, 4, compute()) }\n', encoding="utf-8")
        (self.repo / "calc.go").write_text("func compute() int { return 4 }\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        self.assertEqual(self.log_rows()[0]["decision"], "flagged")

    def test_dunder_tests_dir_recognized(self):
        # L2-4: the `__tests__/foo.ts` convention is now a default test glob.
        (self.repo / "__tests__").mkdir()
        (self.repo / "__tests__" / "calc.ts").write_text(
            "it('c', () => { expect(compute()).toBe(5); });\n", encoding="utf-8")
        (self.repo / "calc.ts").write_text("export const compute = () => 5;\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        git(self.repo, "commit", "-q", "-m", "baseline", env=self.git_env)
        (self.repo / "__tests__" / "calc.ts").write_text(
            "it('c', () => { expect(compute()).toBe(4); });\n", encoding="utf-8")
        (self.repo / "calc.ts").write_text("export const compute = () => 4;\n", encoding="utf-8")
        git(self.repo, "add", "-A", env=self.git_env)
        err, code = self.run_precommit()
        self.assertEqual(code, 0)
        rows = self.log_rows()
        self.assertEqual(rows[0]["decision"], "flagged")
        self.assertTrue(any("__tests__" in f for f in rows[0]["test_files"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
