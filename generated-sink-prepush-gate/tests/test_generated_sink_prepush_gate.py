#!/usr/bin/env python3
"""Standalone test suite for generated-sink-prepush-gate.

Stdlib `unittest`, no third-party dependencies. Each test builds a REAL git repo
in a tempdir with a REAL generator and a REAL checked-in sink, then invokes the
shipped gate as a subprocess (its true git-hook contract: run in a repo, exit 0
to allow / non-zero to block). The tests prove BEHAVIOR end to end — a stale sink
actually blocks the push, a fresh sink actually passes — not smoke.

The pre-push gate departs from the commit gate in that it validates the terminal
working-tree sink on EVERY push, with no staged-source trigger — so these tests
mutate the working tree directly (no staging required) and confirm the gate fires
regardless.

Run:  python3 generated-sink-prepush-gate/tests/test_generated_sink_prepush_gate.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "generated-sink-prepush-gate.py"

# Same real generator convention as the commit-gate suite: render SOURCE.txt ->
# canonical SINK form; in --check mode exit non-zero iff SINK.txt is not a
# byte-exact match.
GENERATOR_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent
    src = (root / "SOURCE.txt").read_text(encoding="utf-8")
    rendered = "GENERATED FROM SOURCE:\\n" + src.upper()
    sink = root / "SINK.txt"

    if "--check" in sys.argv:
        current = sink.read_text(encoding="utf-8") if sink.exists() else ""
        if current != rendered:
            sys.stderr.write("SINK.txt is stale\\n")
            sys.exit(1)
        sys.exit(0)
    else:
        sink.write_text(rendered, encoding="utf-8")
        sys.exit(0)
    """
)


def _render(source_text: str) -> str:
    return "GENERATED FROM SOURCE:\n" + source_text.upper()


def run_git(repo: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )


class PrepushGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "T",
                "GIT_AUTHOR_EMAIL": "t@example.test",
                "GIT_COMMITTER_NAME": "T",
                "GIT_COMMITTER_EMAIL": "t@example.test",
            }
        )
        self.git_env = env
        run_git(self.repo, "init", "-q", env=env)
        run_git(self.repo, "config", "commit.gpgsign", "false", env=env)

        self.source = self.repo / "SOURCE.txt"
        self.source.write_text("hello world\n", encoding="utf-8")
        self.generator = self.repo / "gen.py"
        self.generator.write_text(GENERATOR_SRC, encoding="utf-8")
        self.sink = self.repo / "SINK.txt"
        self.sink.write_text(_render("hello world\n"), encoding="utf-8")

        run_git(self.repo, "add", "-A", env=env)
        run_git(self.repo, "commit", "-q", "-m", "seed", env=env)

    def tearDown(self):
        self._tmp.cleanup()

    # --- helpers -------------------------------------------------------------

    def gate_env(self, **overrides) -> dict:
        env = dict(self.git_env)
        env["GENERATED_SINK_PREPUSH_GATE"] = "on"
        env["GENERATED_SINK_PREPUSH_GATE_GENERATOR"] = f"{sys.executable} gen.py --check"
        env["GENERATED_SINK_PREPUSH_GATE_BYPASS"] = ""
        env["GENERATED_SINK_PREPUSH_GATE_TIMEOUT"] = "60"
        env["GENERATED_SINK_PREPUSH_GATE_LOG_DIR"] = ""
        for k, v in overrides.items():
            env[k] = v
        return env

    def run_gate(self, env: dict) -> subprocess.CompletedProcess:
        """Run the gate from inside the repo, exactly as a pre-push hook does.
        A pre-push hook receives (remote, url) argv and refs on stdin; this gate
        ignores both, so we pass representative argv and empty stdin to prove it."""
        return subprocess.run(
            [sys.executable, str(GATE), "origin", "https://example.test/repo.git"],
            cwd=str(self.repo),
            input="",
            capture_output=True,
            text=True,
            env=env,
        )

    # --- behavioral tests ----------------------------------------------------

    def test_fresh_sink_passes(self):
        # Working tree sink matches a fresh render: push ALLOWED (exit 0).
        p = self.run_gate(self.gate_env())
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW; stderr={p.stderr!r}")

    def test_stale_sink_blocked_with_no_staging(self):
        # The whole point of the backstop: a stale sink in the working tree blocks
        # the push EVEN WITH NOTHING STAGED (the commit gate's trigger never fired,
        # but the terminal state is still stale). This is the defense-in-depth case.
        self.source.write_text("changed source\n", encoding="utf-8")  # sink now stale; nothing staged
        p = self.run_gate(self.gate_env())
        self.assertNotEqual(p.returncode, 0, msg=f"expected BLOCK; stderr={p.stderr!r}")
        self.assertIn("STALE", p.stderr)

    def test_missing_generator_config_loud_fail(self):
        p = self.run_gate(self.gate_env(GENERATED_SINK_PREPUSH_GATE_GENERATOR=""))
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("GENERATED_SINK_PREPUSH_GATE_GENERATOR", p.stderr)
        self.assertIn("fail-closed", p.stderr.lower())

    def test_uninvokable_generator_loud_fail(self):
        env = self.gate_env(
            GENERATED_SINK_PREPUSH_GATE_GENERATOR="/nonexistent/definitely/not/a/real/binary --check"
        )
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("fail-closed", p.stderr.lower())

    def test_generator_timeout_loud_fail(self):
        slow = self.repo / "slow.py"
        slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        env = self.gate_env(
            GENERATED_SINK_PREPUSH_GATE_GENERATOR=f"{sys.executable} slow.py --check",
            GENERATED_SINK_PREPUSH_GATE_TIMEOUT="1",
        )
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("fail-closed", p.stderr.lower())

    def test_gate_disabled_allows_stale(self):
        self.source.write_text("changed source\n", encoding="utf-8")
        p = self.run_gate(self.gate_env(GENERATED_SINK_PREPUSH_GATE="off"))
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW (disabled); stderr={p.stderr!r}")

    def test_bypass_allows_stale(self):
        self.source.write_text("changed source\n", encoding="utf-8")
        env = self.gate_env(GENERATED_SINK_PREPUSH_GATE_BYPASS="1")
        p = self.run_gate(env)
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW (bypass); stderr={p.stderr!r}")
        self.assertIn("bypass", p.stderr.lower())

    def test_bypass_message_prints_the_actual_value(self):
        self.source.write_text("changed source\n", encoding="utf-8")
        p = self.run_gate(self.gate_env(GENERATED_SINK_PREPUSH_GATE_BYPASS="TRUE"))
        self.assertIn("GENERATED_SINK_PREPUSH_GATE_BYPASS=TRUE", p.stderr)

    def test_bypass_accepts_on_true_yes_but_not_garbage(self):
        self.source.write_text("changed source\n", encoding="utf-8")
        for v in ("on", "TRUE", " yes "):
            p = self.run_gate(self.gate_env(GENERATED_SINK_PREPUSH_GATE_BYPASS=v))
            self.assertEqual(p.returncode, 0, msg=f"bypass={v!r}; stderr={p.stderr!r}")
        for v in ("0", "off", "garbage"):
            p = self.run_gate(self.gate_env(GENERATED_SINK_PREPUSH_GATE_BYPASS=v))
            self.assertNotEqual(p.returncode, 0, msg=f"bypass={v!r} must not bypass")

    def test_commit_gate_bypass_does_not_disable_prepush(self):
        # Isolation guarantee: the commit-time gate's bypass env must NOT leak into
        # the pre-push backstop. A stale sink with the SIBLING bypass set still
        # blocks here.
        self.source.write_text("changed source\n", encoding="utf-8")
        env = self.gate_env()
        env["GENERATED_SINK_COMMIT_GATE_BYPASS"] = "1"  # the OTHER gate's bypass
        p = self.run_gate(env)
        self.assertNotEqual(
            p.returncode, 0,
            msg=f"sibling commit-gate bypass must NOT disable the prepush backstop; stderr={p.stderr!r}",
        )

    def test_decision_log_written(self):
        log_dir = Path(self._tmp.name) / "logs"
        self.source.write_text("changed source\n", encoding="utf-8")
        env = self.gate_env(GENERATED_SINK_PREPUSH_GATE_LOG_DIR=str(log_dir))
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0)
        logfile = log_dir / "sink-prepush-gate.log"
        self.assertTrue(logfile.exists(), "decision log should be written")
        self.assertIn("block", logfile.read_text(encoding="utf-8"))


class SplitCommandTests(unittest.TestCase):
    """Windows-style (posix=False) splitting must strip one layer of outer quotes."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gate_under_test", Path(__file__).resolve().parent.parent / "generated-sink-prepush-gate.py")
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_windows_quoted_path_with_spaces(self):
        argv = self.m._split_command(r'"C:\Program Files\Python\python.exe" gen.py --check', posix=False)
        self.assertEqual(argv, [r"C:\Program Files\Python\python.exe", "gen.py", "--check"])

    def test_windows_unquoted_untouched(self):
        self.assertEqual(self.m._split_command(r"python C:\x\gen.py", posix=False),
                         ["python", r"C:\x\gen.py"])

    def test_posix_unchanged(self):
        self.assertEqual(self.m._split_command("'a b' c", posix=True), ["a b", "c"])


TRUTHY_VALUES = ["on", "1", "true", "yes", "ON", "On", "TRUE", "Yes", " yes ", "\ton\n"]
FALSY_VALUES = ["0", "off", "false", "no", "", "garbage", "onn", "yes please", "2", "  "]


class BypassValueTests(unittest.TestCase):
    """Switch values: on/1/true/yes (case-insensitive, whitespace-trimmed) are truthy;
    everything else, including empty and garbage, is off."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bypass_values_under_test", Path(__file__).resolve().parent.parent / "generated-sink-prepush-gate.py")
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def _truthy(self, var, value):
        with mock.patch.dict(os.environ, {var: value}):
            return self.m._env_truthy(var)

    def test_truthy_values(self):
        for var in ['GENERATED_SINK_PREPUSH_GATE', 'GENERATED_SINK_PREPUSH_GATE_BYPASS']:
            for v in TRUTHY_VALUES:
                self.assertTrue(self._truthy(var, v), msg=f"{var}={v!r} should be truthy")

    def test_falsy_values(self):
        for var in ['GENERATED_SINK_PREPUSH_GATE', 'GENERATED_SINK_PREPUSH_GATE_BYPASS']:
            for v in FALSY_VALUES:
                self.assertFalse(self._truthy(var, v), msg=f"{var}={v!r} should be off")

    def test_unset_is_off(self):
        env = {k: v for k, v in os.environ.items() if k not in ['GENERATED_SINK_PREPUSH_GATE', 'GENERATED_SINK_PREPUSH_GATE_BYPASS']}
        with mock.patch.dict(os.environ, env, clear=True):
            for var in ['GENERATED_SINK_PREPUSH_GATE', 'GENERATED_SINK_PREPUSH_GATE_BYPASS']:
                self.assertFalse(self.m._env_truthy(var))


if __name__ == "__main__":
    unittest.main(verbosity=2)
