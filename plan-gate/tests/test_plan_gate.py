#!/usr/bin/env python3
"""Standalone test suite for plan-gate.

Stdlib `unittest`, no third-party dependencies. Each test invokes the real
plan-gate.py as a subprocess — the true PreToolUse contract (tool-use JSON on
stdin, decision JSON on stdout) — so the shipped gate is exercised end to end.

Run:  python plan-gate/tests/test_plan_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "plan-gate.py"

PLAN_TEMPLATE = """---
name: test-plan
status: active
allowed_paths:
  - {allowed}
allowed_commands:
  - npm test
---

test body (ignored by the gate)
"""


def write_active_plan(plans_dir: Path, allowed_path: str) -> None:
    (plans_dir / "active.md").write_text(
        PLAN_TEMPLATE.format(allowed=allowed_path), encoding="utf-8"
    )


def run_gate(payload: dict, plans_dir: Path, gate_on: bool = True, cwd: Path = None):
    """Invoke plan-gate.py with a clean, hermetic environment."""
    env = dict(os.environ)
    env["PLAN_GATE"] = "on" if gate_on else "off"
    env["PLAN_GATE_BYPASS"] = ""
    env["PLAN_GATE_ALWAYS_WRITABLE"] = ""
    env["PLAN_GATE_LOG_DIR"] = ""
    env["PLAN_GATE_MCP_WRITE_TOOLS"] = ""
    # Test-only override: scan our staged plans dir, never the real one.
    env["_PLAN_GATE_TEST_PLANS_DIR"] = str(plans_dir)
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def decision(proc) -> str:
    try:
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    except Exception:  # pragma: no cover - surfaced as a test failure below
        return "<no-decision: stdout=%r stderr=%r>" % (proc.stdout, proc.stderr)


class PlanGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        # A non-git working dir so the gate sees worktree_root = None.
        self.work = root / "work"
        self.work.mkdir()
        self.allowed = self.work / "allowed_file.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def test_gate_off_allows_everything(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "x.txt")}},
            self.plans, gate_on=False, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")

    def test_edit_allowed_path(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")

    def test_inline_comment_on_status_and_path(self):
        # `status` and an allowed_paths entry both carry a trailing inline
        # comment. The parser must strip both: the plan stays active and the
        # clean path matches. Before inline-comment support this denied twice
        # over (status read as "active  # ..." -> no active plan).
        (self.plans / "active.md").write_text(
            "---\n"
            "name: test-plan\n"
            "status: active  # work in progress\n"
            "allowed_paths:\n"
            f"  - {self.allowed}  # the one file\n"
            "allowed_commands:\n"
            "  - npm test\n"
            "---\n\ntest body (ignored by the gate)\n",
            encoding="utf-8",
        )
        p = run_gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")

    def test_edit_denied_path(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "other.txt")}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "deny")

    def test_bash_read_only_allowed(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")

    def test_bash_allowed_prefix(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Bash", "tool_input": {"command": "npm test -- --watch=false"}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")

    def test_bash_denied(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "deny")

    def test_no_active_plan_denies(self):
        # plans dir is empty -> deny by default
        p = run_gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "deny")

    def test_non_gated_tool_allowed(self):
        write_active_plan(self.plans, str(self.allowed))
        p = run_gate(
            {"tool_name": "Read", "tool_input": {"file_path": str(self.work / "x.txt")}},
            self.plans, cwd=self.work,
        )
        self.assertEqual(decision(p), "allow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
