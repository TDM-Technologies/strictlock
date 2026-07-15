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


def run_gate(
    payload: dict,
    plans_dir: Path,
    gate_on: bool = True,
    cwd: Path = None,
    log_dir: Path = None,
    extra_env: dict = None,
):
    """Invoke plan-gate.py with a clean, hermetic environment."""
    env = dict(os.environ)
    env["PLAN_GATE"] = "on" if gate_on else "off"
    env["PLAN_GATE_BYPASS"] = ""
    env["PLAN_GATE_ALWAYS_WRITABLE"] = ""
    env["PLAN_GATE_LOG_DIR"] = str(log_dir) if log_dir else ""
    env["PLAN_GATE_LOG_DECISIONS"] = ""
    env["PLAN_GATE_MCP_WRITE_TOOLS"] = ""
    # Test-only override: scan our staged plans dir, never the real one.
    env["_PLAN_GATE_TEST_PLANS_DIR"] = str(plans_dir)
    if extra_env:
        env.update(extra_env)
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


class DecisionLogTests(unittest.TestCase):
    """The unified, hash-chained authorizations record (plan-gate-decisions.log)."""

    GENESIS = "0" * 64

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        self.logs = root / "logs"  # gate creates it on first row
        self.allowed = self.work / "allowed_file.txt"
        write_active_plan(self.plans, str(self.allowed))

    def tearDown(self):
        self._tmp.cleanup()

    @property
    def log_file(self) -> Path:
        return self.logs / "plan-gate-decisions.log"

    def rows(self) -> list:
        """Parseable rows only — deliberately-damaged lines are skipped."""
        if not self.log_file.exists():
            return []
        out = []
        for ln in self.log_file.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    def gate(self, payload: dict, **kw):
        kw.setdefault("log_dir", self.logs)
        kw.setdefault("cwd", self.work)
        return run_gate(payload, self.plans, **kw)

    def verify(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(GATE), "verify-log", str(self.log_file)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    # --- allow rows, per class ---

    def test_allowed_path_writes_allow_row(self):
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(
            (row["decision"], row["surface"], row["class"], row["plan"]),
            ("allow", "file", "allowed_paths", "active.md"),
        )
        self.assertEqual(row["target"], str(self.allowed))

    def test_read_only_bypass_writes_allow_row(self):
        p = self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(
            (row["decision"], row["surface"], row["class"]),
            ("allow", "bash", "read_only_bypass"),
        )
        self.assertEqual(row["command"], "git status")

    def test_allowed_command_writes_allow_row(self):
        p = self.gate({"tool_name": "Bash", "tool_input": {"command": "npm test -- --ci"}})
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(row["class"], "allowed_command")

    def test_always_writable_writes_allow_row(self):
        scratch = self.work / "scratch"
        scratch.mkdir()
        p = self.gate(
            {"tool_name": "Write", "tool_input": {"file_path": str(scratch / "n.md")}},
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(scratch)},
        )
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(row["class"], "always_writable")

    # --- deny rows carry a class ---

    def test_deny_row_class_denied(self):
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "no.txt")}})
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["class"]), ("deny", "denied"))

    def test_no_active_plan_class(self):
        for f in self.plans.iterdir():
            f.unlink()
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["class"], "no_active_plan")

    def test_mcp_deny_row_has_extra_summary_not_payload(self):
        p = self.gate(
            {"tool_name": "mcp__drive__create",
             "tool_input": {"title": "Q3 report", "content": "SECRET-BODY"}},
            extra_env={"PLAN_GATE_MCP_WRITE_TOOLS": "mcp__drive__create"},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual((row["surface"], row["decision"]), ("mcp", "deny"))
        self.assertEqual(row["extra"]["title"], "Q3 report")
        self.assertIn("content", row["extra"]["_keys"])
        self.assertNotIn("SECRET-BODY", json.dumps(row))

    # --- emergency bypass ---

    def test_emergency_bypass_logged(self):
        p = self.gate(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
            extra_env={"PLAN_GATE_BYPASS": "on"},
        )
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(
            (row["decision"], row["surface"], row["class"]),
            ("allow", "gate", "emergency_bypass"),
        )
        self.assertEqual(row["command"], "rm -rf /")

    def test_emergency_bypass_logged_even_in_deny_mode(self):
        p = self.gate(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
            extra_env={"PLAN_GATE_BYPASS": "on", "PLAN_GATE_LOG_DECISIONS": "deny"},
        )
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(row["class"], "emergency_bypass")

    # --- opt-down mode ---

    def test_deny_mode_skips_allow_rows(self):
        p = self.gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
            extra_env={"PLAN_GATE_LOG_DECISIONS": "deny"},
        )
        self.assertEqual(decision(p), "allow")
        self.assertEqual(self.rows(), [])
        p = self.gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "no.txt")}},
            extra_env={"PLAN_GATE_LOG_DECISIONS": "deny"},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["decision"], "deny")

    # --- the hash chain ---

    def test_chain_links_and_verifies(self):
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "no.txt")}})
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["prev"], self.GENESIS)
        self.assertEqual(rows[1]["prev"], rows[0]["h"])
        self.assertEqual(rows[2]["prev"], rows[1]["h"])
        v = self.verify()
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        self.assertIn("chain intact", v.stdout)

    def test_tampered_row_detected(self):
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        lines = self.log_file.read_text(encoding="utf-8").splitlines()
        row0 = json.loads(lines[0])
        row0["decision"] = "deny"  # rewrite history
        lines[0] = json.dumps(row0, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = self.verify()
        self.assertEqual(v.returncode, 2)
        self.assertIn("hash invalid", v.stdout)

    def test_deleted_row_detected(self):
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        lines = self.log_file.read_text(encoding="utf-8").splitlines()
        self.log_file.write_text(lines[1] + "\n", encoding="utf-8")  # drop row 1
        v = self.verify()
        self.assertEqual(v.returncode, 2)
        self.assertIn("prev-hash mismatch", v.stdout)

    def test_tail_recovery_chains_past_damage(self):
        import hashlib
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        with open(self.log_file, "ab") as fh:
            fh.write(b"GARBAGE-PARTIAL-WRITE\n")
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        rows = self.rows()  # parseable rows only; garbage line skipped by rows()
        self.assertEqual(
            rows[-1]["prev"],
            hashlib.sha256(b"GARBAGE-PARTIAL-WRITE").hexdigest(),
        )
        v = self.verify()
        self.assertEqual(v.returncode, 2)  # damage IS reported...
        self.assertIn("unparseable", v.stdout)
        self.assertNotIn("prev-hash mismatch", v.stdout)  # ...but the chain re-anchors

    # --- log self-protection ---

    def test_self_protect_denies_plan_enumerated_log_path(self):
        write_active_plan(self.plans, str(self.log_file))
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.log_file)}})
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["class"], "log_self_protect")

    def test_self_protect_beats_always_writable(self):
        p = self.gate(
            {"tool_name": "Write", "tool_input": {"file_path": str(self.log_file)}},
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.logs)},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["class"], "log_self_protect")

    # --- fail-open telemetry on a fail-closed gate ---

    def test_unwritable_log_dir_never_changes_decisions(self):
        collision = self.work / "not-a-dir"
        collision.write_text("a FILE occupies the log-dir path", encoding="utf-8")
        p = self.gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
            log_dir=collision,
        )
        self.assertEqual(decision(p), "allow")
        p = self.gate(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.work / "no.txt")}},
            log_dir=collision,
        )
        self.assertEqual(decision(p), "deny")

    def test_no_file_contents_logged_and_command_truncated(self):
        long_cmd = "npm test " + "x" * 1000
        self.gate({"tool_name": "Bash", "tool_input": {"command": long_cmd}})
        self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.allowed), "content": "TOP-SECRET-CONTENT"}})
        rows = self.rows()
        self.assertLessEqual(len(rows[0]["command"]), 500)
        self.assertNotIn("TOP-SECRET-CONTENT", self.log_file.read_text(encoding="utf-8"))

    # --- concurrency ---

    def test_concurrent_appends_keep_chain_intact(self):
        env = dict(os.environ)
        env.update({
            "PLAN_GATE": "on", "PLAN_GATE_BYPASS": "",
            "PLAN_GATE_ALWAYS_WRITABLE": "", "PLAN_GATE_LOG_DECISIONS": "",
            "PLAN_GATE_MCP_WRITE_TOOLS": "",
            "PLAN_GATE_LOG_DIR": str(self.logs),
            "_PLAN_GATE_TEST_PLANS_DIR": str(self.plans),
        })
        payload = json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}}
        )
        procs = [
            subprocess.Popen(
                [sys.executable, str(GATE)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env, cwd=str(self.work),
            )
            for _ in range(8)
        ]
        for pr in procs:
            pr.communicate(input=payload, timeout=30)
        self.assertEqual(len(self.rows()), 8)
        v = self.verify()
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    # --- verifier edge ---

    def test_verify_missing_file_exits_2(self):
        v = self.verify()
        self.assertEqual(v.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
