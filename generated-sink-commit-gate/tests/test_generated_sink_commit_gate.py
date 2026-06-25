#!/usr/bin/env python3
"""Standalone test suite for generated-sink-commit-gate.

Stdlib `unittest`, no third-party dependencies. Each test builds a REAL git repo
in a tempdir with a REAL generator and a REAL checked-in sink, then invokes the
shipped gate as a subprocess (its true git-hook contract: run in a repo, exit 0
to allow / non-zero to block). The tests prove BEHAVIOR end to end — a stale sink
actually blocks, a fresh sink actually passes — not smoke.

Run:  python3 generated-sink-commit-gate/tests/test_generated_sink_commit_gate.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "generated-sink-commit-gate.py"

# A trivial-but-real generator: it renders SOURCE.txt -> a canonical SINK form
# and, in --check mode, exits non-zero iff the checked-in SINK.txt is not a
# byte-exact match. This is the universal `--check` convention the gate relies on
# — exercised for real, not mocked.
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


class CommitGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        # Hermetic git identity so commits work without machine config.
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

        # Real source + real generator + a fresh, byte-exact sink.
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
        """A clean env that enables the gate, points it at the real generator,
        and triggers on staged SOURCE.txt changes."""
        env = dict(self.git_env)
        env["GENERATED_SINK_COMMIT_GATE"] = "on"
        env["GENERATED_SINK_COMMIT_GATE_GENERATOR"] = f'{sys.executable} gen.py --check'
        env["GENERATED_SINK_COMMIT_GATE_SOURCE_PATHS"] = "SOURCE.txt"
        env["GENERATED_SINK_COMMIT_GATE_BYPASS"] = ""
        env["GENERATED_SINK_COMMIT_GATE_TIMEOUT"] = "60"
        env["GENERATED_SINK_COMMIT_GATE_LOG_DIR"] = ""
        for k, v in overrides.items():
            env[k] = v
        return env

    def run_gate(self, env: dict) -> subprocess.CompletedProcess:
        """Run the gate from inside the repo, exactly as a pre-commit hook does."""
        return subprocess.run(
            [sys.executable, str(GATE)],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            env=env,
        )

    def stage(self, *paths: str):
        run_git(self.repo, "add", *paths, env=self.git_env)

    # --- behavioral tests ----------------------------------------------------

    def test_fresh_sink_passes(self):
        # Source AND its companion sink both regenerated and staged together:
        # the gate must ALLOW (exit 0).
        self.source.write_text("changed source\n", encoding="utf-8")
        self.sink.write_text(_render("changed source\n"), encoding="utf-8")
        self.stage("SOURCE.txt", "SINK.txt")
        p = self.run_gate(self.gate_env())
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW; stderr={p.stderr!r}")

    def test_stale_sink_blocked(self):
        # Source changed and staged, but the sink was NOT regenerated. The
        # checked-in sink is now stale => the gate MUST block (non-zero).
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")  # NOTE: sink deliberately left stale + unstaged
        p = self.run_gate(self.gate_env())
        self.assertNotEqual(p.returncode, 0, msg=f"expected BLOCK; stderr={p.stderr!r}")
        self.assertIn("STALE", p.stderr)

    def test_missing_generator_config_loud_fail(self):
        # Source staged so the trigger fires, but no generator configured. The
        # gate cannot verify freshness => fail-closed loud non-zero, NOT a pass.
        self.source.write_text("changed source\n", encoding="utf-8")
        self.sink.write_text(_render("changed source\n"), encoding="utf-8")
        self.stage("SOURCE.txt", "SINK.txt")
        env = self.gate_env(GENERATED_SINK_COMMIT_GATE_GENERATOR="")
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("GENERATED_SINK_COMMIT_GATE_GENERATOR", p.stderr)
        self.assertIn("fail-closed", p.stderr.lower())

    def test_missing_source_paths_config_loud_fail(self):
        # Gate enabled, generator set, but no SOURCE_PATHS trigger configured.
        # The gate cannot tell what to watch => fail-closed, not a silent allow.
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(GENERATED_SINK_COMMIT_GATE_SOURCE_PATHS="")
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("GENERATED_SINK_COMMIT_GATE_SOURCE_PATHS", p.stderr)

    def test_uninvokable_generator_loud_fail(self):
        # The configured generator does not exist on disk / PATH. Invocation
        # OSErrors => fail-closed block, never a silent pass.
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(
            GENERATED_SINK_COMMIT_GATE_GENERATOR="/nonexistent/definitely/not/a/real/binary --check"
        )
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("fail-closed", p.stderr.lower())

    def test_generator_timeout_loud_fail(self):
        # A generator that never returns must be killed and treated as a
        # fail-closed block — not allowed through after a hang.
        slow = self.repo / "slow.py"
        slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(
            GENERATED_SINK_COMMIT_GATE_GENERATOR=f"{sys.executable} slow.py --check",
            GENERATED_SINK_COMMIT_GATE_TIMEOUT="1",
        )
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0, msg=f"expected fail-closed BLOCK; stderr={p.stderr!r}")
        self.assertIn("fail-closed", p.stderr.lower())

    def test_unrelated_change_not_triggered(self):
        # Staging a file OUTSIDE the configured source paths must NOT trigger the
        # check (and must pass) — even though the sink on disk is, in fact, stale.
        # This proves the trigger is scoped, not that the gate is asleep.
        self.source.write_text("now stale-making\n", encoding="utf-8")  # sink now stale on disk
        other = self.repo / "README.md"
        other.write_text("docs only\n", encoding="utf-8")
        self.stage("README.md")  # source NOT staged
        p = self.run_gate(self.gate_env())
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW (untriggered); stderr={p.stderr!r}")

    def test_gate_disabled_allows_stale(self):
        # With the master switch off, a genuinely stale staged sink passes.
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(GENERATED_SINK_COMMIT_GATE="off")
        p = self.run_gate(env)
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW (disabled); stderr={p.stderr!r}")

    def test_bypass_allows_stale(self):
        # The uniquely-named single-commit bypass lets a known-stale sink through,
        # and logs on use.
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(GENERATED_SINK_COMMIT_GATE_BYPASS="1")
        p = self.run_gate(env)
        self.assertEqual(p.returncode, 0, msg=f"expected ALLOW (bypass); stderr={p.stderr!r}")
        self.assertIn("bypass", p.stderr.lower())

    def test_decision_log_written(self):
        # When a log dir is configured, a block appends an audit record.
        log_dir = Path(self._tmp.name) / "logs"
        self.source.write_text("changed source\n", encoding="utf-8")
        self.stage("SOURCE.txt")
        env = self.gate_env(GENERATED_SINK_COMMIT_GATE_LOG_DIR=str(log_dir))
        p = self.run_gate(env)
        self.assertNotEqual(p.returncode, 0)
        logfile = log_dir / "sink-commit-gate.log"
        self.assertTrue(logfile.exists(), "decision log should be written")
        self.assertIn("block", logfile.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
