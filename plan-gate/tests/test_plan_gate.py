#!/usr/bin/env python3
"""Standalone test suite for plan-gate.

Stdlib `unittest`, no third-party dependencies. Each test invokes the real
plan-gate.py as a subprocess — the true PreToolUse contract (tool-use JSON on
stdin, decision JSON on stdout) — so the shipped gate is exercised end to end.

Run:  python plan-gate/tests/test_plan_gate.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    env["PLAN_GATE_SCOPE_LEASE"] = ""
    env["PLAN_GATE_REQUIRE_LEASE"] = ""
    env["CLAUDE_PROJECT_DIR"] = ""  # a real session's value must not leak into the tests
    # Approval is required whenever a log dir is set; the legacy tests stage
    # unapproved plans, so they opt out (ApprovalTests overrides this).
    env["PLAN_GATE_REQUIRE_APPROVAL"] = "off"
    # Test-only override: scan our staged plans dir, never the real one.
    env["_PLAN_GATE_TEST_PLANS_DIR"] = str(plans_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(GATE)],
        # A str payload is sent verbatim (malformed-JSON tests); a dict is JSON.
        input=payload if isinstance(payload, str) else json.dumps(payload),
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

    def test_mcp_deny_row_logs_tool_only_no_arguments(self):
        p = self.gate(
            {"tool_name": "mcp__drive__create",
             "tool_input": {"title": "Q3 report", "content": "SECRET-BODY"}},
            extra_env={"PLAN_GATE_MCP_WRITE_TOOLS": "mcp__drive__create"},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual((row["surface"], row["decision"]), ("mcp", "deny"))
        self.assertNotIn("extra", row)
        self.assertEqual(row["tool"], "mcp__drive__create")
        self.assertNotIn("SECRET-BODY", json.dumps(row))
        self.assertNotIn("Q3 report", json.dumps(row))

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

    def test_self_protect_beats_dotdot_laundering(self):
        # Adversarial-verify repro: plans dir always-writable (the documented
        # usage), log dir a SIBLING — a `..`-laundered file_path OS-resolves
        # into the log dir but evades a lexical-only prefix check.
        laundered = str(self.plans) + os.sep + ".." + os.sep + "logs" + os.sep + "plan-gate-decisions.log"
        p = self.gate(
            {"tool_name": "Write", "tool_input": {"file_path": laundered}},
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.plans)},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["class"], "log_self_protect")

    def test_self_protect_beats_dotdot_under_always_writable_ancestor(self):
        # Variant: always-writable is an ANCESTOR of the log dir; `..` path
        # stays inside the grant yet lands on the log file.
        root = self.logs.parent
        laundered = str(root / "foo" / ".." / "logs" / "plan-gate-decisions.log")
        p = self.gate(
            {"tool_name": "Edit", "tool_input": {"file_path": laundered}},
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(root)},
        )
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["class"], "log_self_protect")

    def test_self_protect_beats_plan_enumerated_dotdot_path(self):
        # Variant: the plan itself enumerates the identical `..`-laden string,
        # so allowed_paths exact-match would allow it — self-protect must
        # still fire first.
        laundered = str(self.plans) + os.sep + ".." + os.sep + "logs" + os.sep + "plan-gate-decisions.log"
        write_active_plan(self.plans, laundered)
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": laundered}})
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

    def test_oversized_target_cannot_break_the_chain(self):
        # Adversarial-verify repro: a 70k-char file_path used to produce a row
        # bigger than the writer's tail window -> next row anchored wrong ->
        # false tamper alarm. Fields are capped now; the chain must verify.
        huge = str(self.work / ("x" * 70000))
        p = self.gate({"tool_name": "Edit", "tool_input": {"file_path": huge}})
        self.assertEqual(decision(p), "deny")
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(len(rows[0]["target"]), 2048)
        self.assertEqual(rows[1]["prev"], rows[0]["h"])
        v = self.verify()
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_lone_surrogate_still_writes_a_row(self):
        # Adversarial-verify repro: a lone UTF-16 surrogate (via a \ud800
        # escape in the stdin JSON) used to abort serialization and silently
        # drop the row. The decision row must survive, sanitized.
        p = self.gate({"tool_name": "Bash", "tool_input": {"command": "rm \ud800 evil"}})
        self.assertEqual(decision(p), "deny")
        (row,) = self.rows()
        self.assertEqual(row["decision"], "deny")
        self.assertIn("rm ", row["command"])
        v = self.verify()
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_cr_damaged_line_reanchors_identically_on_both_sides(self):
        # Adversarial-verify repro: writer used splitlines() (strips \r),
        # verifier splits on \n (keeps \r) -> divergent re-anchor hashes after
        # CRLF damage. Both sides now split on \n only.
        import hashlib
        self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}})
        with open(self.log_file, "ab") as fh:
            fh.write(b"GARBAGE\r\n")
        self.gate({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        rows = self.rows()
        self.assertEqual(rows[-1]["prev"], hashlib.sha256(b"GARBAGE\r").hexdigest())
        v = self.verify()
        self.assertEqual(v.returncode, 2)  # damage reported...
        self.assertIn("unparseable", v.stdout)
        self.assertNotIn("prev-hash mismatch", v.stdout)  # ...no false alarm


class BashSegmentTests(unittest.TestCase):
    """Fix B: every Bash command is split into segments (&& || ; | & newline,
    plus $(...) / backtick / <(...) bodies); EACH must independently pass.
    Write redirections are file writes gated like Edit/Write."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # resolve(): on macOS the temp dir is a symlink (/var -> /private/var)
        # and the gate resolves cwd physically.
        root = Path(self._tmp.name).resolve()
        self.root = root
        self.plans = root / "plans"
        self.plans.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        self.logs = root / "logs"
        self.out = self.work / "out.txt"

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, commands=("pytest", "git commit", "git add", "npm test"),
             paths=None):
        paths = [str(self.out)] if paths is None else paths
        text = "---\nname: p\nstatus: active\nallowed_paths:\n"
        text += "".join("  - %s\n" % p for p in paths)
        text += "allowed_commands:\n" + "".join("  - %s\n" % c for c in commands)
        text += "---\n\nbody\n"
        (self.plans / "active.md").write_text(text, encoding="utf-8")

    def run_cmd(self, cmd, **kw):
        kw.setdefault("cwd", self.work)
        # Tests splice temp paths into shell text. On Windows str(path) has
        # backslashes, which bash (and so the gate) reads as escapes in an
        # unquoted word; a real agent writes C:/... or quotes the path. Use the
        # forward-slash form so the command means what the test intends.
        if os.sep == "\\":
            cmd = re.sub(re.escape(str(self.root)) + r"[^\s'\"]*",
                         lambda m: m.group(0).replace("\\", "/"), cmd)
        return run_gate({"tool_name": "Bash", "tool_input": {"command": cmd}},
                        self.plans, **kw)

    def assertDenied(self, cmd, **kw):
        p = self.run_cmd(cmd, **kw)
        self.assertEqual(decision(p), "deny", "should DENY: %r\n%s" % (cmd, p.stdout))
        return p

    def assertAllowed(self, cmd, **kw):
        p = self.run_cmd(cmd, **kw)
        self.assertEqual(decision(p), "allow", "should ALLOW: %r\n%s" % (cmd, p.stdout))
        return p

    # --- no active plan: the pre-plan read-only bypass ---

    def test_no_plan_smuggling_denied(self):
        for cmd in [
            "echo pwned > app.py",
            "echo pwned >> app.py",
            "echo pwned >| app.py",
            "echo pwned &> app.py",
            "echo pwned 2> app.py",
            "cat a; rm -rf build",
            "cat a && rm -rf build",
            "cat a || rm -rf build",
            "cat a | tee app.py",
            "ls\nrm -rf build",
            "ls & rm -rf build",
            "ls |& rm -rf build",
            "find . -delete",
            "find . -name x -exec rm {} ;",
            "find . -fprint out.txt",
            "git branch -D main",
            "git branch newbranch",
            "git branch -m old new",
            "git branch --delete main",
            "git remote add o http://x",
            "git remote remove o",
            "git fetch",
            "git status; git push",
            "git log --output=leak.txt",
            "git diff --output leak.txt",
            "rg --pre sh pattern .",
            "git config --get a.b --add c.d",
            "echo $(rm -rf build)",
            "echo `rm -rf build`",
            "echo \"$(rm -rf build)\"",
            "cat <(rm -rf build)",
            "cat <<EOF\n$(rm -rf build)\nEOF",
            "(rm -rf build)",
            "git -C /tmp branch -D main",
            "lsof",  # word boundary: `ls` must not admit `lsof`
        ]:
            self.assertDenied(cmd)

    def test_no_plan_fail_closed_on_unparseable(self):
        for cmd in [
            'echo "unterminated',
            "echo 'unterminated",
            "echo $(unterminated",
            "echo `unterminated",
            "echo x > $TARGET",
            'echo x > "$TARGET"',
            "echo x > $(mktemp)",
            "echo x >",
        ]:
            p = self.assertDenied(cmd)
            self.assertIn("cannot safely parse", json.loads(p.stdout)[
                "hookSpecificOutput"]["permissionDecisionReason"], cmd)

    def test_no_plan_ansi_c_quote_does_not_hide_a_segment(self):
        # In bash $'\'' is ONE quoted quote char; a naive single-quote scan
        # would treat `rm -rf x` as quoted and miss it.
        self.assertDenied("echo $'\\'' && rm -rf x && echo $'\\''")

    def test_no_plan_legit_read_only_forms_allowed(self):
        for cmd in [
            "git status",
            "git log --oneline -5",
            "git diff --stat HEAD~1",
            "git diff -- --output=weird-file-name",
            "cat a | grep b | head -5",
            "ls && pwd",
            "ls -la; pwd",
            "echo hi > /dev/null",
            "git status 2>&1",
            "git status 2>&1 | head -5",
            "git status 2>/dev/null",
            "echo 'a && rm -rf b'",
            'echo "a ; rm -rf b"',
            "echo hi # && rm -rf b",
            "git branch",
            "git branch -a",
            "git branch -vv",
            "git branch --list 'feature/*'",
            "git branch --merged main",
            "git branch --show-current",
            "git remote",
            "git remote -v",
            "git remote show origin",
            "git remote get-url origin",
            "git config --get user.name",
            "git config --get-regexp 'remote.*'",
            "find . -name '*.py' -not -path './node_modules/*'",
            "rg -n pattern src",
            "cd sub && git status",
            "cd sub",
            "echo $(pwd)",
            "echo $((1+2))",
            "git -C /tmp status",
            "git -C /tmp log --oneline",
            "cat <<'EOF'\n$(rm -rf b) && y > z\nEOF",
        ]:
            self.assertAllowed(cmd)

    def test_no_plan_redirect_into_always_writable_dir_allowed(self):
        scratch = self.work / "scratch"
        scratch.mkdir()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(scratch)}
        self.assertAllowed("echo note > %s" % (scratch / "n.md"), extra_env=env)
        self.assertDenied("echo note > %s" % (self.work / "n.md"), extra_env=env)
        # `..` cannot launder a redirect out of the always-writable dir.
        self.assertDenied("echo note > %s/../n.md" % scratch, extra_env=env)

    # --- active plan: allowed_commands is checked per segment ---

    def test_plan_prefix_riding_compounds_denied(self):
        self.plan()
        for cmd in [
            "pytest -q && rm -rf build",
            "pytest -q; rm -rf build",
            "pytest -q || rm -rf build",
            "pytest -q | tee app.py",
            "pytest -q\nrm -rf build",
            "pytest -q & rm -rf build",
            "pytest $(rm -rf build)",
            "pytest `rm -rf build`",
            'pytest "$(rm -rf build)"',
            "pytest <(rm -rf build)",
            "git add . && git push",
            "cat a; rm -rf build",
            "cd sub && rm -rf build",
            "cd sub; pytest; rm -rf build",
            "(pytest; rm -rf build)",
            # A plan entry that spells out a whole compound does NOT authorize
            # that compound; only per-segment prefixes count (migration note).
        ]:
            self.assertDenied(cmd)

    def test_plan_enumerating_a_whole_compound_no_longer_rides(self):
        self.plan(commands=["git add -A && git commit"])
        self.assertDenied("git add -A && git commit -m x")
        self.assertDenied("git add -A && git commit -m x && rm -rf build")

    def test_plan_legit_compounds_allowed(self):
        self.plan()
        for cmd in [
            "pytest -q",
            "pytest -q && git status",
            "pytest -q && pytest -x",
            "git add a.py && git commit -m 'fix && polish'",
            'git commit -m "a; b | c"',
            "pytest 2>&1 | head -20",
            "pytest > /dev/null 2>&1",
            "cd sub && pytest",
            "cd sub && pytest && cd .. && npm test",
            "git commit -m \"$(cat <<'EOF'\nsubject line\n\nbody with $(literal) and `ticks` && more\nEOF\n)\"",
            "git commit -m \"$(git log -1 --format=%s)\"",  # inner cmd is read-only
        ]:
            self.assertAllowed(cmd)

    def test_plan_empty_allowed_command_entry_matches_nothing(self):
        self.plan(commands=['""', "pytest"])
        self.assertDenied("rm -rf build")

    # --- redirection-as-write, with a plan ---

    def test_plan_redirect_gated_by_allowed_paths(self):
        self.plan()
        self.assertAllowed("pytest -q > out.txt")  # cwd-relative -> self.out
        self.assertAllowed("pytest -q >> %s" % self.out)
        self.assertAllowed("pytest -q &> %s" % self.out)
        self.assertAllowed("pytest -q 2> %s" % self.out)
        self.assertDenied("pytest -q > app.py")
        self.assertDenied("pytest -q >> app.py")
        self.assertDenied("pytest -q 2> app.py")
        self.assertDenied("npm test > %s" % (self.work / "other.txt"))
        # directory entries authorize nothing inside them (same as Edit/Write)
        self.assertDenied("pytest -q > %s" % (self.work / "sub" / "x.txt"))
        # `..` is normalized before the exact match
        self.assertAllowed("pytest -q > %s/sub/../out.txt" % self.work)
        self.assertDenied("pytest -q > %s/../out.txt" % self.work)

    def test_plan_read_only_command_with_unlisted_redirect_denied(self):
        self.plan()
        self.assertDenied("echo pwned > app.py")
        self.assertAllowed("echo ok > out.txt")

    def test_plan_dev_null_and_fd_dups_are_not_writes(self):
        self.plan()
        self.assertAllowed("pytest > /dev/null")
        self.assertAllowed("pytest 2>&1")
        self.assertAllowed("pytest >&2")
        self.assertAllowed("pytest 1>&2")
        self.assertAllowed("pytest < input.txt")
        self.assertAllowed("pytest <<< 'text'")
        # `>&file` is a real write (stdout+stderr to a file)
        self.assertDenied("pytest >& app.py")

    def test_plan_relative_redirect_after_cd_denied_absolute_ok(self):
        self.plan()
        self.assertDenied("cd sub && pytest > out.txt")
        self.assertAllowed("cd sub && pytest > %s" % self.out)

    def test_redirect_cannot_target_the_decision_log(self):
        log = self.logs / "plan-gate-decisions.log"
        self.plan(paths=[str(self.out), str(log)])  # even if a plan lists it
        p = self.assertDenied("pytest > %s" % log, log_dir=self.logs)
        self.assertIn("PLAN_GATE_LOG_DIR",
                      json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"])
        # ...nor via an always-writable ancestor, nor with `..` laundering.
        self.assertDenied(
            "echo x > %s" % log, log_dir=self.logs,
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.root)},
        )
        self.assertDenied(
            "echo x > %s/plans/../logs/plan-gate-decisions.log" % self.root,
            log_dir=self.logs,
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.plans)},
        )

    # --- verify-pass regressions (scanner/shell desync, validators) ---

    def both(self, cmd, expect, **kw):
        """Run `cmd` with no plan and with an active plan (pytest allowed)."""
        for with_plan in (False, True):
            if with_plan:
                self.plan()
            elif (self.plans / "active.md").exists():
                (self.plans / "active.md").unlink()
            if expect == "deny":
                self.assertDenied(cmd, **kw)
            else:
                self.assertAllowed(cmd, **kw)

    def test_comment_hash_after_escape_or_substitution_is_not_a_comment(self):
        for cmd in [
            "echo a\\(#;touch SECOND",
            "echo \\;#x; touch q",
            "echo foo\\ #bar; touch q",
            "echo $(true)#;touch SECOND",
            "echo $(( 1 ))#x; touch q",
            "echo <(true)#x; touch q",
            "echo `true`#x; touch q",
            "echo 'a'#x; touch q",
            "echo a\\\n#;touch SECOND",
        ]:
            self.both(cmd, "deny")
        self.plan()
        self.assertDenied("pytest $(echo a)#x; rm -rf zzz")

    def test_real_comments_still_work(self):
        self.both("git status # a; trailing comment", "allow")
        self.both("git status\n# whole line comment ; rm -rf x\ngit log", "allow")
        self.both("git status;# c", "allow")
        self.both("echo a \\\n# continued comment", "allow")

    def test_git_c_path_must_be_literal(self):
        for cmd in [
            "git -C $(cat<<<R${IFS}commit${IFS}--allow-empty${IFS}-m) status",
            "git -C `echo x` status",
            "git -C $(echo x) log",
            "git -C ${X} status",
            "git -C /tmp/* status",
            "git -C /tmp/?x status",
            "git -C \"$X\" status",
        ]:
            self.both(cmd, "deny")
        self.both("git -C /tmp status", "allow")
        self.both("git -C '/tmp/a b$c' status", "allow")
        self.both("git -C \"/tmp/a b\" status", "allow")

    def test_quoted_and_ref_syntax_stays_allowed(self):
        for cmd in [
            "git diff @{u}",
            "git diff stash@{0}",
            "git diff --name-only HEAD@{1}",
            "rg -n 'foo$' .",
            "rg 'import \\{' .",
            "git log --grep='^fix$'",
            "find . -regex '.*py$'",
            "find . -name '*.py'",
            "git log --output-indicator-new=+",
        ]:
            self.both(cmd, "allow")

    def test_unquoted_expansion_and_glob_still_denied_for_validated(self):
        for cmd in [
            "find . -d?lete",
            "rg --p?e=x foo",
            "git branch -?",
            "find . -name *.py",
            "git log $(echo --output=x)",
            "git diff {HEAD,--output=x}",
            "git log --output=x",
        ]:
            self.both(cmd, "deny")

    def test_heredoc_delimiter_mismatch_and_unterminated(self):
        for cmd in [
            "cat <<$'EOF'\nhi\nEOF\ntouch q",
            'cat <<$"EOF"\nhi\nEOF\ntouch q',
            "cat <<EOF\nhi\ntouch q",
            "cat <<'EOF'\nhi\ntouch q",
            "cat <<EOF",
            "cat <<$X\nhi\n$X\ntouch q",
        ]:
            self.both(cmd, "deny")
        self.plan(commands=("git commit",))
        self.assertAllowed("git commit -F - <<'EOF'\nmsg\nEOF")
        self.assertDenied("git commit -F - <<$'EOF'\nmsg\nEOF\nrm -rf x")

    def test_glob_in_redirect_target_denied(self):
        aw = {"PLAN_GATE_ALWAYS_WRITABLE": str(self.root / "aw")}
        for cmd in [
            "echo pwned > %s/aw/.?/victim.txt" % self.root,
            "echo x > %s/aw/*" % self.root,
            "echo x > %s/aw/[a]" % self.root,
            "echo x > %s/aw/{a,b}" % self.root,
        ]:
            self.both(cmd, "deny", extra_env=aw)
        self.both("echo x > '%s/aw/lit*' " % self.root, "allow", extra_env=aw)

    def test_subshell_group_of_allowed_commands(self):
        self.both("(cd sub && git status)", "allow")
        self.plan()
        self.assertAllowed("(pytest -q)")
        self.assertDenied("(pytest -q; rm -rf x)")
        self.assertDenied("(pytest -q) rm")

    def test_dev_std_streams_are_not_writes(self):
        self.plan()
        for t in ("/dev/stderr", "/dev/stdout", "/dev/tty", "/dev/fd/2"):
            self.assertAllowed("pytest > %s" % t)

    def test_edit_always_writable_normalizes_dotdot(self):
        aw = self.root / "aw"
        aw.mkdir()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}
        sneaky = str(aw) + "/../elsewhere/x"
        p = run_gate({"tool_name": "Write", "tool_input": {"file_path": sneaky}},
                     self.plans, cwd=self.work, extra_env=env)
        self.assertEqual(decision(p), "deny")
        p = run_gate({"tool_name": "Write", "tool_input": {"file_path": str(aw / "ok")}},
                     self.plans, cwd=self.work, extra_env=env)
        self.assertEqual(decision(p), "allow")

    def test_powershell_blank_allowed_command_matches_nothing(self):
        self.plan(commands=('""',))
        p = run_gate({"tool_name": "PowerShell",
                      "tool_input": {"command": "Remove-Item -Recurse x"}},
                     self.plans, cwd=self.work)
        self.assertEqual(decision(p), "deny")

    # --- audit rows ---

    def rows(self):
        f = self.logs / "plan-gate-decisions.log"
        if not f.exists():
            return []
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_decision_classes(self):
        self.plan()
        self.run_cmd("git status", log_dir=self.logs)
        self.run_cmd("cd sub", log_dir=self.logs)
        self.run_cmd("pytest -q && git status", log_dir=self.logs)
        self.run_cmd("pytest -q && rm -rf build", log_dir=self.logs)
        self.run_cmd('echo "oops', log_dir=self.logs)
        self.run_cmd("pytest > %s" % (self.logs / "plan-gate-decisions.log"),
                     log_dir=self.logs)
        got = [(r["decision"], r["class"]) for r in self.rows()]
        self.assertEqual(got, [
            ("allow", "read_only_bypass"),
            ("allow", "navigation"),
            ("allow", "allowed_command"),
            ("deny", "denied"),
            ("deny", "unparseable_command"),
            ("deny", "log_self_protect"),
        ])


class McpPathTests(unittest.TestCase):
    """PLAN_GATE_MCP_WRITE_TOOLS entries may be `tool:arg` / `tool:arg1+arg2`:
    every path in the named arguments is checked like an Edit/Write file_path.
    A bare tool name stays default-denied."""

    TOOL = "mcp__fs__write_file"
    MOVE = "mcp__fs__move"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.logs = self.root / "logs"
        self.a = (self.work / "a.txt").as_posix()
        self.b = (self.work / "b.txt").as_posix()
        self.c = (self.work / "c.txt").as_posix()
        self.cfg = "mcp__fs__write_file:path,mcp__fs__move:source+destination,mcp__fs__multi:paths"
        write_active_plan(self.plans, self.a)
        self.add_paths(self.b)

    def tearDown(self):
        self._tmp.cleanup()

    def add_paths(self, *extra):
        txt = (self.plans / "active.md").read_text(encoding="utf-8")
        txt = txt.replace("allowed_commands:", "".join("  - %s\n" % e for e in extra)
                          + "allowed_commands:")
        (self.plans / "active.md").write_text(txt, encoding="utf-8")

    def call(self, tool, tool_input, cfg=None, **kw):
        env = {"PLAN_GATE_MCP_WRITE_TOOLS": self.cfg if cfg is None else cfg}
        env.update(kw.pop("extra_env", {}))
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": tool, "tool_input": tool_input}, self.plans,
                        extra_env=env, **kw)

    def assertDenied(self, tool, tool_input, contains=None, **kw):
        p = self.call(tool, tool_input, **kw)
        self.assertEqual(decision(p), "deny", "should DENY: %r %r\n%s" % (tool, tool_input, p.stdout))
        if contains:
            self.assertIn(contains, p.stdout)
        return p

    def assertAllowed(self, tool, tool_input, **kw):
        p = self.call(tool, tool_input, **kw)
        self.assertEqual(decision(p), "allow", "should ALLOW: %r %r\n%s" % (tool, tool_input, p.stdout))
        return p

    def assertNoDecision(self, tool, tool_input, **kw):
        """Exit 0 and NO stdout: the harness keeps its own permission prompt."""
        p = self.call(tool, tool_input, **kw)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""),
                         "should give NO decision: %r %r\n%s" % (tool, tool_input, p.stdout))
        return p

    def rows(self):
        text = (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8")
        return [json.loads(l) for l in text.splitlines()]

    def test_single_arg_allowed_path_passes(self):
        self.assertAllowed(self.TOOL, {"path": self.a, "content": "x"})

    def test_single_arg_out_of_plan_denies(self):
        self.assertDenied(self.TOOL, {"path": self.c, "content": "x"},
                          contains="NOT in allowed_paths")

    def test_multi_arg_all_must_pass(self):
        self.assertAllowed(self.MOVE, {"source": self.a, "destination": self.b})
        self.assertDenied(self.MOVE, {"source": self.a, "destination": self.c})
        self.assertDenied(self.MOVE, {"source": self.c, "destination": self.b})

    def test_list_of_strings_every_string_is_a_target(self):
        self.assertAllowed("mcp__fs__multi", {"paths": [self.a, self.b]})
        self.assertDenied("mcp__fs__multi", {"paths": [self.a, self.c]})

    def test_bad_argument_values_deny(self):
        for ti in ({}, {"content": "x"}, {"path": ""}, {"path": "   "}, {"path": None},
                   {"path": 5}, {"path": {"x": 1}}, {"path": True}):
            self.assertDenied(self.TOOL, ti)
        for paths in ([], [""], [self.a, ""], [self.a, 3], [self.a, None], [[self.a]]):
            self.assertDenied("mcp__fs__multi", {"paths": paths})
        # one good, one missing argument of a two-argument spec
        self.assertDenied(self.MOVE, {"source": self.a})
        self.assertDenied(self.MOVE, {"source": self.a, "destination": ""})

    def test_bad_argument_deny_names_the_argument(self):
        p = self.assertDenied(self.TOOL, {"content": "x"}, contains="'path'")
        self.assertIn("Failing closed", p.stdout)

    def test_bare_entry_is_default_denied_even_for_allowed_paths(self):
        self.assertDenied(self.TOOL, {"path": self.a}, cfg=self.TOOL,
                          contains="default-denied")
        self.assertDenied(self.TOOL, {"path": self.a}, cfg=self.TOOL + ":")

    def test_bare_entry_wins_over_spec_for_same_tool(self):
        self.assertDenied(self.TOOL, {"path": self.a},
                          cfg=self.TOOL + ":path," + self.TOOL)
        self.assertDenied(self.TOOL, {"path": self.a},
                          cfg=self.TOOL + "," + self.TOOL + ":path")

    def test_spec_whitespace_and_repeats_are_tolerated(self):
        cfg = " mcp__fs__move : source + destination , "
        self.assertAllowed(self.MOVE, {"source": self.a, "destination": self.b}, cfg=cfg)
        self.assertDenied(self.MOVE, {"source": self.a, "destination": self.c}, cfg=cfg)
        cfg2 = "mcp__fs__move:source,mcp__fs__move:destination"
        self.assertDenied(self.MOVE, {"source": self.a, "destination": self.c}, cfg=cfg2)

    def test_empty_tool_input_still_denies(self):
        self.assertDenied(self.TOOL, {}, contains="empty tool_input")

    def test_log_dir_target_denies(self):
        self.logs.mkdir()
        logfile = (self.logs / "plan-gate-decisions.log").as_posix()
        self.add_paths(logfile)
        p = self.assertDenied(self.TOOL, {"path": logfile}, log_dir=self.logs,
                              contains="PLAN_GATE_LOG_DIR")
        self.assertEqual(self.rows()[-1]["class"], "log_self_protect")
        # one log-dir target among good ones denies the whole call
        self.assertDenied(self.MOVE, {"source": self.a, "destination": logfile},
                          log_dir=self.logs)

    def test_always_writable_targets_pass_without_a_plan_entry(self):
        aw = self.root / "mem"
        aw.mkdir()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}
        note = (aw / "n.md").as_posix()
        self.assertAllowed(self.TOOL, {"path": note}, extra_env=env)
        # mixed: the always-writable one passes, the other still needs the plan
        self.assertAllowed(self.MOVE, {"source": note, "destination": self.a}, extra_env=env)
        self.assertDenied(self.MOVE, {"source": note, "destination": self.c}, extra_env=env)

    def test_always_writable_works_before_any_plan_exists(self):
        aw = self.root / "mem"
        aw.mkdir()
        (self.plans / "active.md").unlink()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}
        self.assertAllowed(self.TOOL, {"path": (aw / "n.md").as_posix()}, extra_env=env)
        self.assertDenied(self.TOOL, {"path": self.a}, extra_env=env,
                          contains="no plan file")

    def test_dotdot_cannot_ride_an_always_writable_prefix(self):
        aw = self.root / "mem"
        aw.mkdir()
        sneaky = aw.as_posix() + "/../work/c.txt"
        self.assertDenied(self.TOOL, {"path": sneaky},
                          extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(aw)})

    def test_no_active_plan_denies(self):
        (self.plans / "active.md").unlink()
        self.assertDenied(self.TOOL, {"path": self.a}, contains="no plan file")

    def test_relative_target_never_matches_an_absolute_entry(self):
        self.assertDenied(self.TOOL, {"path": "a.txt"})

    def test_unconfigured_mcp_tool_gets_no_decision_and_is_logged(self):
        self.assertNoDecision("mcp__other__read", {"q": "x"}, log_dir=self.logs)
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["surface"], row["class"], row["tool"]),
                         ("allow", "mcp", "mcp_unconfigured", "mcp__other__read"))

    def test_unconfigured_mcp_allow_row_respects_deny_only_mode(self):
        self.assertNoDecision("mcp__other__read", {"q": "x"}, log_dir=self.logs,
                              extra_env={"PLAN_GATE_LOG_DECISIONS": "deny"})
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())

    def test_non_mcp_unknown_tool_still_unlogged(self):
        self.assertAllowed("Read", {"file_path": self.a}, log_dir=self.logs)
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())

    def test_unconfigured_mcp_allowed_with_no_plan(self):
        (self.plans / "active.md").unlink()
        self.assertNoDecision("mcp__other__read", {"q": "x"})

    def test_log_rows_record_tool_and_each_target(self):
        self.assertAllowed(self.MOVE, {"source": self.a, "destination": self.b},
                           log_dir=self.logs)
        self.assertDenied("mcp__fs__multi", {"paths": [self.a, self.c]}, log_dir=self.logs)
        ok, no = self.rows()
        self.assertEqual((ok["surface"], ok["tool"], ok["decision"]), ("mcp", self.MOVE, "allow"))
        self.assertEqual(ok["extra"]["targets"], [self.a, self.b])
        self.assertEqual(ok["target"], self.a)
        self.assertEqual(no["extra"]["targets"], [self.a, self.c])
        self.assertEqual(no["decision"], "deny")

    def test_log_rows_cap_target_length_and_count_and_omit_content(self):
        long_path = (self.work / ("d" * 3000)).as_posix()
        many = [(self.work / ("f%d.txt" % i)).as_posix() for i in range(25)]
        self.assertDenied("mcp__fs__multi", {"paths": [long_path] + many, "content": "SECRET-BODY"},
                          log_dir=self.logs)
        (row,) = self.rows()
        self.assertEqual(len(row["extra"]["targets"]), 20)
        self.assertEqual(row["extra"]["targets_n"], 26)
        self.assertLessEqual(len(row["extra"]["targets"][0]), 2048)
        self.assertNotIn("SECRET-BODY", json.dumps(row))

    def test_row_for_bad_argument_logs_without_targets(self):
        self.assertDenied(self.TOOL, {"content": "SECRET-BODY"}, log_dir=self.logs)
        (row,) = self.rows()
        self.assertEqual(row["class"], "mcp_target")
        self.assertNotIn("SECRET-BODY", json.dumps(row))

    def test_bypass_defers_to_the_harness_prompt(self):
        p = self.assertNoDecision(self.TOOL, {"path": self.c},
                                  extra_env={"PLAN_GATE_BYPASS": "on"}, log_dir=self.logs)
        self.assertEqual(self.rows()[-1]["class"], "emergency_bypass")

    def test_gate_off_mcp_call_gets_no_decision_but_edit_still_allows(self):
        self.assertNoDecision("mcp__other__read", {"q": "x"}, gate_on=False)
        self.assertNoDecision(self.TOOL, {"path": self.c}, gate_on=False)
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": self.c}},
                     self.plans, gate_on=False, cwd=self.work)
        self.assertEqual(decision(p), "allow")

    def test_crash_path_mcp_with_bypass_gets_no_decision(self):
        p = self.call("mcp__other__read", {"q": "x"},
                      extra_env={"PLAN_GATE_BYPASS": "on", "_PLAN_GATE_TEST_CRASH": "late"})
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))

    def test_checked_and_passing_call_still_emits_allow(self):
        p = self.assertAllowed(self.TOOL, {"path": self.a})
        self.assertIn('"permissionDecision": "allow"', p.stdout)

    def test_matcher_in_example_settings_selects_exactly_the_intended_tools(self):
        cfg = json.loads((HERE.parent / "examples" / "settings.json").read_text(encoding="utf-8"))
        matcher = cfg["hooks"]["PreToolUse"][0]["matcher"]
        # The harness treats a matcher with non-alphanumerics as an UNANCHORED regex.
        hit = lambda name: re.search(matcher, name) is not None
        for name in ("Edit", "Write", "NotebookEdit", "Bash", "PowerShell",
                     "mcp__fs__write_file", "mcp__x"):
            self.assertTrue(hit(name), name)
        for name in ("TodoWrite", "MultiEdit", "MultiEditX", "WriteX", "XEdit", "BashOutput",
                     "Read", "Grep", "Task", "xmcp__fs__write"):
            self.assertFalse(hit(name), name)

    def test_worst_case_mcp_row_stays_under_budget_and_chain_verifies(self):
        self.logs.mkdir()
        nasty = [("\x01" * 2048)] * 20
        self.assertDenied("mcp__fs__multi", {"paths": nasty}, log_dir=self.logs)
        self.assertAllowed(self.TOOL, {"path": self.a}, log_dir=self.logs)
        lines = (self.logs / "plan-gate-decisions.log").read_bytes().splitlines()
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertLessEqual(len(ln), 32 * 1024)
        first = json.loads(lines[0])
        self.assertTrue(first["extra"].get("targets_truncated"))
        self.assertEqual(first["extra"]["targets_n"], 20)
        # the second row chained correctly onto the first
        v = subprocess.run([sys.executable, str(GATE), "verify-log",
                            str(self.logs / "plan-gate-decisions.log")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_unconfigured_row_names_only_the_tool(self):
        self.assertNoDecision("mcp__mail__send", {"to": "a@b.c", "title": "T", "body": "SECRET-BODY"},
                              log_dir=self.logs)
        (row,) = self.rows()
        self.assertNotIn("extra", row)
        self.assertNotIn("SECRET-BODY", json.dumps(row))
        self.assertNotIn("a@b.c", json.dumps(row))


class WriterScriptTests(unittest.TestCase):
    """A python/python3/py segment running a writer script (built-in:
    atomic-write.py) is a file write to its target argument, gated like a `>`
    redirect target: allowed_paths, log self-protection, always-writable dirs."""

    WRITER = "externalized-memory/atomic-write.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()  # cwd is the real path
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.logs = self.root / "logs"
        self.out = (self.work / "STATE.md").as_posix()
        self.other = (self.work / "OTHER.md").as_posix()

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, commands=("python3 externalized-memory/atomic-write.py",), paths=None):
        paths = [self.out] if paths is None else paths
        text = "---\nname: p\nstatus: active\nallowed_paths:\n"
        text += "".join("  - %s\n" % p for p in paths)
        text += "allowed_commands:\n" + "".join("  - %s\n" % c for c in commands)
        text += "---\n\nbody\n"
        (self.plans / "active.md").write_text(text, encoding="utf-8")

    def run_tool(self, tool, cmd, **kw):
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": tool, "tool_input": {"command": cmd}},
                        self.plans, **kw)

    def run_cmd(self, cmd, **kw):
        return self.run_tool("Bash", cmd, **kw)

    def assertDenied(self, cmd, tool="Bash", contains=None, **kw):
        p = self.run_tool(tool, cmd, **kw)
        self.assertEqual(decision(p), "deny", "should DENY: %r\n%s" % (cmd, p.stdout))
        if contains:
            self.assertIn(contains, p.stdout)
        return p

    def assertAllowed(self, cmd, tool="Bash", **kw):
        p = self.run_tool(tool, cmd, **kw)
        self.assertEqual(decision(p), "allow", "should ALLOW: %r\n%s" % (cmd, p.stdout))
        return p

    def test_allowed_target_passes(self):
        self.plan()
        self.assertAllowed("python3 %s %s" % (self.WRITER, self.out))

    def test_out_of_plan_target_denies(self):
        self.plan()
        self.assertDenied("python3 %s %s" % (self.WRITER, self.other),
                          contains="NOT in allowed_paths")

    def test_target_matches_allowed_path_exactly_not_by_prefix_of_command(self):
        # allowed_commands admits the segment; it does not admit the target.
        self.plan(commands=("python3",))
        self.assertDenied("python3 %s %s" % (self.WRITER, self.other))

    def test_segment_still_needs_allowed_commands(self):
        self.plan(commands=("npm test",))
        self.assertDenied("python3 %s %s" % (self.WRITER, self.out))

    def test_relative_target_resolves_against_cwd(self):
        self.plan()
        self.assertAllowed("python3 %s STATE.md" % self.WRITER)
        self.assertDenied("python3 %s OTHER.md" % self.WRITER)

    def test_relative_target_after_cd_denies(self):
        self.plan(commands=("python3 externalized-memory/atomic-write.py", "cd"))
        self.assertDenied("cd sub && python3 %s STATE.md" % self.WRITER,
                          contains="relative")

    def test_log_dir_target_denies(self):
        self.logs.mkdir()
        logfile = (self.logs / "plan-gate-decisions.log").as_posix()
        self.plan(paths=[self.out, logfile])
        p = self.assertDenied("python3 %s %s" % (self.WRITER, logfile),
                              log_dir=self.logs, contains="PLAN_GATE_LOG_DIR")

    def test_always_writable_target_passes_without_plan_path(self):
        aw = self.root / "mem"
        aw.mkdir()
        self.plan()
        self.assertAllowed(
            "python3 %s %s" % (self.WRITER, (aw / "note.md").as_posix()),
            extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(aw)})

    def test_missing_target_denies(self):
        self.plan()
        self.assertDenied("python3 %s" % self.WRITER, contains="without its target")
        self.assertDenied("python3 -u %s" % self.WRITER, contains="without its target")

    def test_unresolvable_target_denies(self):
        self.plan()
        for tgt in ('$TARGET', '"$TARGET"', '${TARGET}', '$(echo STATE.md)',
                    '`echo STATE.md`', '*.md', 'STATE.m?', 'ST{A,B}TE.md',
                    '~/STATE.md/../$X'):
            self.assertDenied("python3 %s %s" % (self.WRITER, tgt))

    def test_flags_are_not_positionals(self):
        self.plan()
        self.assertAllowed("python3 %s -- %s" % (self.WRITER, self.out))
        self.assertDenied("python3 %s -- %s" % (self.WRITER, self.other))

    def test_interpreter_spellings_are_recognized(self):
        self.plan(commands=("python", "/usr/bin/python3", "py", "FOO=1", "env",
                            "C:/Python312/python.exe", "python3.12"))
        for prefix in ("python", "/usr/bin/python3", "py -3", "FOO=1 python3",
                       "env python3", "python3 -u -B", "python3.12",
                       "C:/Python312/python.exe", "py -3.11"):
            self.assertDenied("%s %s %s" % (prefix, self.WRITER, self.other))
            self.assertAllowed("%s %s %s" % (prefix, self.WRITER, self.out))

    def test_script_path_spellings_match_by_basename(self):
        self.plan(commands=("python3",))
        for script in ("atomic-write.py", "./atomic-write.py",
                       "/opt/tools/atomic-write.py", "C:/tools/ATOMIC-WRITE.PY"):
            self.assertDenied("python3 %s %s" % (script, self.other))
            self.assertAllowed("python3 %s %s" % (script, self.out))

    def test_writer_in_compound_and_substitution_is_checked(self):
        self.plan(commands=("python3", "echo"))
        self.assertDenied("echo hi && python3 %s %s" % (self.WRITER, self.other))
        self.assertDenied("echo $(python3 %s %s)" % (self.WRITER, self.other))
        self.assertAllowed("echo hi && python3 %s %s" % (self.WRITER, self.out))

    def test_unrelated_python_is_unaffected(self):
        self.plan(commands=("python3",))
        self.assertAllowed("python3 -m pytest")
        self.assertAllowed("python3 -m atomic-write %s" % self.other)
        self.assertAllowed("python3 -c 'print(1)'")
        self.assertAllowed("python3 other_script.py %s" % self.other)
        self.assertAllowed("python3 other_script.py")

    def test_mentioning_the_script_without_running_it_is_unaffected(self):
        # No plan at all: read-only inspection of the script stays allowed.
        self.assertAllowed("grep -n python %s" % self.WRITER)
        self.assertAllowed("cat %s" % self.WRITER)
        self.plan(commands=("echo",))
        self.assertAllowed("echo python3 %s" % self.WRITER)

    def test_env_extension_with_argpos(self):
        self.plan(commands=("python3 tools/my-writer.py",))
        env = {"PLAN_GATE_WRITER_SCRIPTS": "my-writer.py:2"}
        self.assertAllowed("python3 tools/my-writer.py src %s" % self.out, extra_env=env)
        self.assertDenied("python3 tools/my-writer.py src %s" % self.other, extra_env=env)
        self.assertDenied("python3 tools/my-writer.py %s" % self.out, extra_env=env,
                          contains="without its target")
        # The built-in stays on alongside the extension.
        self.assertDenied("python3 tools/atomic-write.py %s" % self.other, extra_env=env)

    def test_env_extension_is_off_without_the_env(self):
        self.plan(commands=("python3 tools/my-writer.py",))
        self.assertAllowed("python3 tools/my-writer.py src %s" % self.other)

    def test_env_may_override_builtin_argpos(self):
        self.plan(commands=("python3",))
        env = {"PLAN_GATE_WRITER_SCRIPTS": " Atomic-Write.py : 2 , other.py:1 ,"}
        self.assertAllowed("python3 atomic-write.py x %s" % self.out, extra_env=env)
        self.assertDenied("python3 atomic-write.py x %s" % self.other, extra_env=env)

    def test_malformed_env_fails_closed(self):
        self.plan(commands=("python3", "npm test"))
        for bad in ("my-writer.py", "my-writer.py:", ":2", "my-writer.py:0",
                    "my-writer.py:-1", "my-writer.py:x", "my-writer.py:1.5",
                    "tools/my-writer.py:1", "a.py:1,b.py"):
            env = {"PLAN_GATE_WRITER_SCRIPTS": bad}
            self.assertDenied("npm test", extra_env=env, contains="PLAN_GATE_WRITER_SCRIPTS")
            self.assertDenied("python3 %s %s" % (self.WRITER, self.out), extra_env=env,
                              contains="PLAN_GATE_WRITER_SCRIPTS")
            # Read-only commands are not wedged by the bad value.
            self.assertAllowed("git status", extra_env=env)

    def test_malformed_env_mentions_one_based(self):
        self.plan()
        p = self.assertDenied("python3 %s %s" % (self.WRITER, self.out),
                              extra_env={"PLAN_GATE_WRITER_SCRIPTS": "w.py:0"})
        self.assertIn("1-based", p.stdout)

    def test_bypass_still_honored_with_malformed_env(self):
        self.plan()
        self.assertAllowed("python3 %s %s" % (self.WRITER, self.other),
                           extra_env={"PLAN_GATE_WRITER_SCRIPTS": "bad",
                                      "PLAN_GATE_BYPASS": "on"})

    def test_decision_log_records_writer_deny(self):
        self.plan()
        self.assertDenied("python3 %s" % self.WRITER, log_dir=self.logs)
        rows = [json.loads(l) for l in
                (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["class"], "writer_script")
        self.assertEqual(rows[-1]["decision"], "deny")

    # --- env parsing (F, I) ---

    def test_separator_typos_in_env_are_malformed(self):
        self.plan(commands=("python3", "npm test"))
        for bad in ("w.py:1 gen.py:2", "gen.py:2;w.py:1", "gen.py:2:w.py:1", "w.py:1:2",
                    "w.py", "w.txt:1", "dir/w.py:1", "dir\\w.py:1", "w .py:1", ".py:1",
                    "w.py:65", "w.py:1;", "w.py:+1"):
            p = self.assertDenied("npm test", extra_env={"PLAN_GATE_WRITER_SCRIPTS": bad},
                                  contains="PLAN_GATE_WRITER_SCRIPTS")
        for ok in ("w.py:1", " w.py : 2 , Gen-Tool_2.py:64 ,"):
            self.assertAllowed("npm test", extra_env={"PLAN_GATE_WRITER_SCRIPTS": ok})

    def test_huge_argpos_fails_closed_not_crash_at_import(self):
        self.plan(commands=("python3", "npm test"))
        p = self.assertDenied("npm test", extra_env={
            "PLAN_GATE_WRITER_SCRIPTS": "w.py:" + "1" * 5000}, contains="PLAN_GATE_WRITER_SCRIPTS")
        self.assertNotIn("INTERNAL ERROR", p.stdout)
        self.assertNotIn("Traceback", p.stderr)

    # --- option values must not shift positionals (G) ---

    def test_option_before_target_denies(self):
        self.plan(commands=("python3",))
        env = {"PLAN_GATE_WRITER_SCRIPTS": "gen.py:1"}
        evil = (self.root / "evil.txt").as_posix()
        for args in ("--encoding %s %s" % (self.out, evil), "-o %s %s" % (self.out, evil),
                     "--verbose %s" % self.out):
            self.assertDenied("python3 gen.py %s" % args, extra_env=env, contains="--flag=value")
        # --flag=value and options after the target are fine
        self.assertAllowed("python3 gen.py --encoding=utf8 %s" % self.out, extra_env=env)
        self.assertAllowed("python3 gen.py %s --verbose" % self.out, extra_env=env)
        self.assertAllowed("python3 gen.py -- %s" % self.out, extra_env=env)

    def test_builtin_atomic_write_unchanged(self):
        self.plan()
        self.assertAllowed("python3 %s %s" % (self.WRITER, self.out))

    # --- broader recognition (J) ---

    def test_wrappers_with_flags_are_seen_through(self):
        wr = self.WRITER
        self.plan(commands=("env", "time", "sudo", "nice", "timeout", "nohup", "exec", "FOO=1"))
        for pre in ("env -i python3", "env -- python3", "env FOO=x python3",
                    "env -u FOO python3", "env -i FOO=x BAR=y python3", "time -p python3",
                    "sudo -u bob python3", "nice -n 5 python3", "nice -5 python3",
                    "timeout 5 python3", "timeout -k 2 5 python3", "nohup python3",
                    "exec -a x python3", "FOO=1 env python3"):
            self.assertDenied("%s %s %s" % (pre, wr, self.other))
            self.assertAllowed("%s %s %s" % (pre, wr, self.out))

    def test_more_interpreters_are_recognized(self):
        wr = self.WRITER
        self.plan(commands=("pythonw", "python3.13t", "pypy", "pypy3", "python3-dbg", "py",
                            "C:/P/pythonw.exe", "python3.12.exe", "/opt/py/bin/python3.11"))
        for prog in ("pythonw", "python3.13t", "pypy", "pypy3", "python3-dbg", "py.exe",
                     "C:/P/pythonw.exe", "python3.12.exe", "/opt/py/bin/python3.11"):
            self.assertDenied("%s %s %s" % (prog, wr, self.other))
            self.assertAllowed("%s %s %s" % (prog, wr, self.out))

    def test_direct_execution_of_a_writer(self):
        self.plan(commands=("./externalized-memory/atomic-write.py", "env"))
        self.assertDenied("./externalized-memory/atomic-write.py %s" % self.other)
        self.assertAllowed("./externalized-memory/atomic-write.py %s" % self.out)
        self.assertDenied("env -i ./externalized-memory/atomic-write.py %s" % self.other)
        self.assertDenied("./externalized-memory/atomic-write.py")

    def test_interpreter_options_with_values_and_combined_flags(self):
        wr = self.WRITER
        self.plan(commands=("python3", "py"))
        for opts in ("--check-hash-based-pycs never", "-W ignore", "-Wignore", "-X dev",
                     "-uX utf8", "-u -B -O", "-I -s -S -E", "-OO"):
            self.assertDenied("python3 %s %s %s" % (opts, wr, self.other))
            self.assertAllowed("python3 %s %s %s" % (opts, wr, self.out))
        for opts in ("-3", "-3.11", "-V:3.11", "-V:ContinuumAnalytics/Anaconda3", "-3.11-64"):
            self.assertDenied("py %s %s %s" % (opts, wr, self.other))
            self.assertAllowed("py %s %s %s" % (opts, wr, self.out))
        # -m / -c clusters are not script runs
        for opts in ("-m pkg", "-um pkg", "-c code", "-uc code"):
            self.assertAllowed("python3 %s %s %s" % (opts, wr, self.other))

    # --- symlink + .. (H) ---

    def _symlink_into_log_dir(self):
        (self.logs / "sub").mkdir(parents=True)
        link = self.work / "ldir"
        try:
            os.symlink(str(self.logs / "sub"), str(link), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks unavailable")
        return link

    @unittest.skipIf(os.name == "nt", "Windows resolves `..` lexically before following "
                     "symlinks (GetFullPathName), so there is no escape to test")
    def test_dotdot_through_symlink_cannot_reach_log_dir_via_writer(self):
        link = self._symlink_into_log_dir()
        target = link.as_posix() + "/../x.txt"  # resolves to <logs>/x.txt
        self.plan(paths=[self.out, target])
        self.assertDenied("python3 %s %s" % (self.WRITER, target), log_dir=self.logs,
                          contains="PLAN_GATE_LOG_DIR")

    @unittest.skipIf(os.name == "nt", "Windows resolves `..` lexically before following "
                     "symlinks (GetFullPathName), so there is no escape to test")
    def test_dotdot_through_symlink_cannot_reach_log_dir_via_redirect(self):
        link = self._symlink_into_log_dir()
        target = link.as_posix() + "/../x.txt"
        self.plan(commands=("echo",), paths=[self.out, target])
        self.assertDenied("echo hi > %s" % target, log_dir=self.logs,
                          contains="PLAN_GATE_LOG_DIR")

    # --- PowerShell ---

    def test_ps_allowed_and_denied_targets(self):
        self.plan(commands=("python3 externalized-memory/atomic-write.py",
                            "py -3 externalized-memory/atomic-write.py"))
        self.assertAllowed("python3 %s %s" % (self.WRITER, self.out), tool="PowerShell")
        self.assertAllowed("py -3 %s '%s'" % (self.WRITER, self.out), tool="PowerShell")
        self.assertDenied("python3 %s %s" % (self.WRITER, self.other), tool="PowerShell",
                          contains="NOT in allowed_paths")
        self.assertDenied("py -3 %s \"%s\"" % (self.WRITER, self.other), tool="PowerShell")

    def test_ps_missing_and_unresolvable_targets_deny(self):
        self.plan()
        self.assertDenied("python3 %s" % self.WRITER, tool="PowerShell")
        self.assertDenied("python3 %s $env:TARGET" % self.WRITER, tool="PowerShell")
        self.assertDenied("python3 %s (Get-Item x)" % self.WRITER, tool="PowerShell")
        self.assertDenied("python3 %s '%s" % (self.WRITER, self.out), tool="PowerShell")

    def test_ps_compound_writer_is_checked(self):
        self.plan(commands=("python3",))
        self.assertDenied("python3 %s %s; python3 %s %s" % (
            self.WRITER, self.out, self.WRITER, self.other), tool="PowerShell")

    def test_ps_malformed_env_fails_closed(self):
        self.plan()
        self.assertDenied("python3 %s %s" % (self.WRITER, self.out), tool="PowerShell",
                          extra_env={"PLAN_GATE_WRITER_SCRIPTS": "bad"},
                          contains="PLAN_GATE_WRITER_SCRIPTS")
        self.assertAllowed("Get-ChildItem", tool="PowerShell",
                           extra_env={"PLAN_GATE_WRITER_SCRIPTS": "bad"})


class PowerShellTests(unittest.TestCase):
    """PowerShell read-only fast path and writer tokenizing."""

    WRITER = "externalized-memory/atomic-write.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.out = (self.work / "STATE.md").as_posix()
        self.other = (self.work / "OTHER.md").as_posix()

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, commands=("npm test",), paths=None):
        paths = [self.out] if paths is None else paths
        text = "---\nname: p\nstatus: active\nallowed_paths:\n"
        text += "".join("  - %s\n" % p for p in paths)
        text += "allowed_commands:\n" + "".join("  - %s\n" % c for c in commands)
        text += "---\n\nbody\n"
        (self.plans / "active.md").write_text(text, encoding="utf-8")

    def ps(self, cmd, **kw):
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": "PowerShell", "tool_input": {"command": cmd}},
                        self.plans, **kw)

    def test_read_only_fast_path_allows_a_single_simple_command(self):
        self.plan()
        for cmd in ("Get-ChildItem", "Get-ChildItem -Recurse C:/x", "Get-Content 'a b.txt'",
                    "Test-Path $env:HOME", "Get-Location"):
            self.assertEqual(decision(self.ps(cmd)), "allow", cmd)

    def test_read_only_fast_path_refuses_anything_compound(self):
        self.plan()
        for cmd in ("Get-ChildItem; Remove-Item -Recurse C:\\x",
                    "Get-ChildItem | Remove-Item", "Get-ChildItem && Remove-Item x",
                    "Get-ChildItem & Remove-Item x", "Get-ChildItem\nRemove-Item x",
                    "Get-ChildItem\r\nRemove-Item x", "Get-ChildItem $(Remove-Item x)",
                    "Get-ChildItem (Remove-Item x)", "Get-ChildItem { Remove-Item x }",
                    "Get-ChildItem `\nRemove-Item x", "Get-ChildItem > out.txt",
                    "Get-Content a >> b.txt", "Get-ChildItemX", "Get-Content-Evil x"):
            self.assertEqual(decision(self.ps(cmd)), "deny", cmd)

    def test_each_segment_must_be_read_only_or_allowed(self):
        self.plan(commands=("npm test", "Select-Object"))
        deny = ("npm test; Remove-Item -Recurse C:/x", "npm test && evil", "npm test || evil",
                "npm test | evil", "npm test & evil", "npm test\nevil", "evil; npm test",
                "npm testing", "Get-ChildItem | Remove-Item")
        for cmd in deny:
            self.assertEqual(decision(self.ps(cmd)), "deny", cmd)
        allow = ("npm test", "npm test; npm test", "npm test && npm test -- --x",
                 "Get-ChildItem | Select-Object Name",       # read-only + allowed
                 "Get-ChildItem; npm test", "npm test\nGet-Location",
                 "Get-ChildItem | Select-Object Name; npm test")
        for cmd in allow:
            self.assertEqual(decision(self.ps(cmd)), "allow", cmd)

    def test_two_allowed_segments_allow(self):
        self.plan(commands=("npm test", "npm run build"))
        self.assertEqual(decision(self.ps("npm test; npm run build")), "allow")
        self.assertEqual(decision(self.ps("npm test && npm run build | evil")), "deny")

    def test_unplaceable_constructs_deny_even_after_an_allowed_prefix(self):
        self.plan(commands=("npm test", "Get-Content"))
        for cmd in ("npm test $(evil)", "npm test @(evil)", 'npm test "$(evil)"',
                    "npm test { evil }", "npm test (evil)", "npm test `; evil",
                    "npm test > out.txt", "npm test >> out.txt", "npm test 2> err.txt",
                    "npm test < in.txt", "npm test 'unterminated", "Get-Content (evil)",
                    "Get-Content a > b", "npm test @{a=1}"):
            p = self.ps(cmd)
            self.assertEqual(decision(p), "deny", cmd)
        p = self.ps("npm test $(evil)")
        self.assertIn("Failing closed", p.stdout)

    def test_quoted_separators_do_not_split(self):
        self.plan(commands=("npm test",))
        self.assertEqual(decision(self.ps("npm test 'a; evil' \"b | c\"")), "allow")

    def test_unchecked_file_writing_cmdlets_deny(self):
        self.plan(commands=("npm test", "Out-File", "Set-Content", "Copy-Item"))
        for cmd in ("Out-File x.txt", "npm test | Out-File x.txt", "Set-Content x 1",
                    "Add-Content x 1", "New-Item x", "Copy-Item a b", "Move-Item a b",
                    "& Out-File x", "sc x 1", "cp a b"):
            p = self.ps(cmd)
            self.assertEqual(decision(p), "deny", cmd)
        self.assertIn("cannot check", self.ps("Out-File x.txt").stdout)

    def test_call_operator_segment_matches_an_ampersand_entry(self):
        self.plan(commands=("& python3",))
        self.assertEqual(decision(self.ps("& python3 tool.py")), "allow")
        self.assertEqual(decision(self.ps("& python3 tool.py; & evil")), "deny")

    def test_no_plan_read_only_chain_still_allowed(self):
        self.assertEqual(decision(self.ps("Get-ChildItem; Get-Location | Get-Content x")), "allow")
        self.assertEqual(decision(self.ps("Get-ChildItem; Remove-Item x")), "deny")

    def test_doubled_quote_escapes_do_not_hide_a_writer(self):
        self.plan(commands=("python3",))
        # 'it''s' is one token; the writer target after it is still found.
        self.assertEqual(decision(self.ps("python3 %s '%s'" % (self.WRITER, self.out))), "allow")
        self.assertEqual(decision(self.ps("python3 %s '%s'" % (self.WRITER, self.other))), "deny")
        # a doubled quote inside the target: target is the literal path with a quote
        self.assertEqual(decision(self.ps("python3 %s 'a''b.md'" % self.WRITER)), "deny")
        # "" inside "..." must not end the string early and expose a fake separator
        self.assertEqual(
            decision(self.ps('python3 %s "%s"; python3 %s "x"" ; ""y"' % (
                self.WRITER, self.out, self.WRITER))), "deny")
        # backtick inside double quotes in a command that mentions a writer: fail closed
        self.assertEqual(decision(self.ps('python3 %s "a`"b"' % self.WRITER)), "deny")

    def test_ps_wrapper_and_interpreter_forms(self):
        self.plan(commands=("& python3", "python3", "py"))
        self.assertEqual(decision(self.ps("& python3 %s %s" % (self.WRITER, self.out))), "allow")
        self.assertEqual(decision(self.ps("& python3 %s %s" % (self.WRITER, self.other))), "deny")
        self.assertEqual(decision(self.ps("py -3 %s %s" % (self.WRITER, self.other))), "deny")


class SwitchValueTests(unittest.TestCase):
    """PLAN_GATE / PLAN_GATE_BYPASS accept on/1/true/yes, case-insensitive and
    whitespace-trimmed; every other value is off."""

    TRUTHY = ["on", "1", "true", "yes", "ON", "True", " yes ", "\ton\n"]
    FALSY = ["0", "off", "false", "no", "", "garbage", "onn"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        self.allowed = self.work / "allowed_file.txt"
        write_active_plan(self.plans, str(self.allowed))
        self.denied_payload = {
            "tool_name": "Edit", "tool_input": {"file_path": str(self.work / "other.txt")}}

    def tearDown(self):
        self._tmp.cleanup()

    def test_bypass_truthy_values_bypass(self):
        for v in self.TRUTHY:
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE_BYPASS": v})
            self.assertEqual(decision(p), "allow", msg=f"PLAN_GATE_BYPASS={v!r}")

    def test_bypass_other_values_do_not_bypass(self):
        for v in self.FALSY:
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE_BYPASS": v})
            self.assertEqual(decision(p), "deny", msg=f"PLAN_GATE_BYPASS={v!r}")

    def test_master_switch_truthy_values_enable(self):
        for v in self.TRUTHY:
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE": v})
            self.assertEqual(decision(p), "deny", msg=f"PLAN_GATE={v!r}")

    def test_master_switch_other_values_are_inert(self):
        for v in self.FALSY:
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE": v})
            self.assertEqual(decision(p), "allow", msg=f"PLAN_GATE={v!r}")

    def test_bypass_reason_prints_the_actual_value(self):
        p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                     extra_env={"PLAN_GATE_BYPASS": "Yes"})
        self.assertIn("PLAN_GATE_BYPASS=Yes", p.stdout)

    def test_crash_path_honors_truthy_bypass(self):
        for v in ("yes", "1", "TRUE"):
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE_BYPASS": v, "_PLAN_GATE_TEST_CRASH": "early"})
            self.assertEqual(decision(p), "allow", msg=f"PLAN_GATE_BYPASS={v!r}")
        p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                     extra_env={"PLAN_GATE_BYPASS": "garbage", "_PLAN_GATE_TEST_CRASH": "early"})
        self.assertEqual(decision(p), "deny")


class CrashPostureTests(unittest.TestCase):
    """Q5: a gate fault DENIES (exit 0 + deny JSON), honors PLAN_GATE_BYPASS in
    the crash path, and still allows when PLAN_GATE != on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        self.logs = root / "logs"
        write_active_plan(self.plans, str(self.work / "allowed_file.txt"))
        self.good = {"tool_name": "Bash", "tool_input": {"command": "git status"}}

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, payload, **kw):
        kw.setdefault("cwd", self.work)
        return run_gate(payload, self.plans, **kw)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def rows(self):
        f = self.logs / "plan-gate-decisions.log"
        if not f.exists():
            return []
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]

    def assertLoudDeny(self, proc):
        self.assertEqual(proc.returncode, 0, "deny must be exit 0 JSON, never exit 1")
        self.assertEqual(decision(proc), "deny")
        r = self.reason(proc)
        self.assertIn("INTERNAL ERROR", r)
        self.assertIn("PLAN_GATE_BYPASS=on", r)
        self.assertIn("emergency_bypass", r)

    def test_bad_json_denies(self):
        p = self.gate("{this is not json", log_dir=self.logs)
        self.assertLoudDeny(p)
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["surface"], row["class"]),
                         ("deny", "gate", "gate_error"))

    def test_empty_stdin_denies(self):
        self.assertLoudDeny(self.gate(""))

    def test_non_object_payload_denies(self):
        self.assertLoudDeny(self.gate("[1, 2, 3]"))
        self.assertLoudDeny(self.gate('"just a string"'))

    def test_wrong_shaped_tool_input_denies(self):
        self.assertLoudDeny(self.gate('{"tool_name": "Bash", "tool_input": "git status"}'))
        self.assertLoudDeny(self.gate('{"tool_name": 7, "tool_input": {}}'))

    def test_bad_json_with_bypass_allows_and_logs(self):
        p = self.gate("{nope", log_dir=self.logs, extra_env={"PLAN_GATE_BYPASS": "on"})
        self.assertEqual(decision(p), "allow")
        (row,) = self.rows()
        self.assertEqual(row["class"], "emergency_bypass")

    def test_bad_json_with_gate_off_allows(self):
        p = self.gate("{nope", gate_on=False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decision(p), "allow")

    def test_injected_exception_denies_loudly(self):
        for where in ("early", "late"):
            p = self.gate(self.good, log_dir=self.logs,
                          extra_env={"_PLAN_GATE_TEST_CRASH": where})
            self.assertLoudDeny(p)
            self.assertIn("RuntimeError", self.reason(p))
            self.assertIn("Traceback", p.stderr)  # traceback for the operator
        self.assertEqual([r["class"] for r in self.rows()], ["gate_error", "gate_error"])

    def test_injected_exception_row_names_the_call_when_known(self):
        self.gate(self.good, log_dir=self.logs,
                  extra_env={"_PLAN_GATE_TEST_CRASH": "late"})
        (row,) = self.rows()
        self.assertEqual((row["tool"], row["command"]), ("Bash", "git status"))

    def test_bypass_honored_in_the_crash_path(self):
        # The early injection fires BEFORE main()'s own bypass check, so this
        # allow can only come from the top-level handler re-checking the env.
        p = self.gate(self.good, log_dir=self.logs,
                      extra_env={"_PLAN_GATE_TEST_CRASH": "early",
                                 "PLAN_GATE_BYPASS": "on"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decision(p), "allow")
        self.assertIn("fault path", self.reason(p))
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["class"]), ("allow", "emergency_bypass"))

    def test_gate_off_still_allows_in_the_crash_path(self):
        p = self.gate(self.good, gate_on=False,
                      extra_env={"_PLAN_GATE_TEST_CRASH": "early"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decision(p), "allow")

    def test_unwritable_log_never_blocks_the_crash_deny(self):
        collision = self.work / "a-file"
        collision.write_text("x", encoding="utf-8")
        p = self.gate(self.good, log_dir=collision,
                      extra_env={"_PLAN_GATE_TEST_CRASH": "late"})
        self.assertLoudDeny(p)


class WorktreeClaimTests(unittest.TestCase):
    """Q8c: claim-by-absolute is the mechanism; the no-claim path gives the
    diagnostic deny; WHAT is denied is unchanged."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.root = root
        self.plans = root / "plans"
        self.plans.mkdir()
        # A main checkout with a linked worktree: wt/.git is a FILE pointing at
        # main/.git/worktrees/wt.
        self.main = root / "main"
        (self.main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        self.wt = root / "wt"
        self.wt.mkdir()
        (self.wt / ".git").write_text(
            "gitdir: %s\n" % (self.main / ".git" / "worktrees" / "wt"), encoding="utf-8")
        self.logs = root / "logs"

    def tearDown(self):
        self._tmp.cleanup()

    def edit(self, path, **kw):
        kw.setdefault("cwd", self.wt)
        return run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(path)}},
                        self.plans, **kw)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def rows(self):
        f = self.logs / "plan-gate-decisions.log"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_plan_pointing_at_main_tree_gives_drift_diagnostic(self):
        write_active_plan(self.plans, str(self.main / "app.py"))
        p = self.edit(self.wt / "app.py", log_dir=self.logs)
        self.assertEqual(decision(p), "deny")
        r = self.reason(p)
        self.assertIn("1 active plan(s) exist", r)
        self.assertIn("none claims this worktree", r)
        self.assertIn("likely drift", r)
        self.assertIn("active.md", r)
        (row,) = self.rows()
        self.assertEqual(row["class"], "silent_drift")

    def test_relative_only_plan_does_not_claim_a_worktree(self):
        write_active_plan(self.plans, "app.py")
        p = self.edit(self.wt / "app.py")
        self.assertEqual(decision(p), "deny")
        self.assertIn("none claims this worktree", self.reason(p))

    def test_no_active_plans_anywhere_keeps_generic_text(self):
        p = self.edit(self.wt / "app.py", log_dir=self.logs)
        self.assertEqual(decision(p), "deny")
        self.assertNotIn("likely drift", self.reason(p))
        (row,) = self.rows()
        self.assertEqual(row["class"], "no_active_plan")

    def test_plan_claiming_the_worktree_by_absolute_path_works(self):
        write_active_plan(self.plans, str(self.wt / "app.py"))
        self.assertEqual(decision(self.edit(self.wt / "app.py")), "allow")
        self.assertEqual(decision(self.edit(self.wt / "other.py")), "deny")

    def test_worktree_bypass_plan_claims_every_worktree(self):
        (self.plans / "active.md").write_text(
            "---\nname: x\nstatus: active\nworktree_bypass: true\n"
            "allowed_paths:\n  - %s\n---\n" % (self.main / "app.py"), encoding="utf-8")
        self.assertEqual(decision(self.edit(self.main / "app.py")), "allow")

    def test_drift_denies_commands_but_read_only_inspection_stays(self):
        write_active_plan(self.plans, str(self.main / "app.py"))
        def bash(c):
            return run_gate({"tool_name": "Bash", "tool_input": {"command": c}},
                            self.plans, cwd=self.wt)
        self.assertEqual(decision(bash("git status")), "allow")
        p = bash("npm test")
        self.assertEqual(decision(p), "deny")
        self.assertIn("likely drift", self.reason(p))


SCOPE_LEASE = HERE.parent.parent / "scope-lease" / "scope-lease.py"


def _git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr))
    return proc.stdout


class ScopeLeaseTests(unittest.TestCase):
    """PLAN_GATE_SCOPE_LEASE: every write target is looked up with `scope-lease.py
    holder` before the plan check; a path held by another owner is denied.
    PLAN_GATE_REQUIRE_LEASE=on also denies a path nobody holds."""

    WRITER = "externalized-memory/atomic-write.py"
    TOOL = "mcp__fs__write_file"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        (self.repo / "seed.txt").write_text("x\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed")
        self.leased = self.repo / "leased.txt"
        self.free = self.repo / "free.txt"
        self.write_plan(self.leased, self.free)

    def tearDown(self):
        self._tmp.cleanup()

    def write_plan(self, *paths):
        body = "".join("  - %s\n" % p for p in paths)
        (self.plans / "active.md").write_text(
            "---\nname: lease-test\nstatus: active\nallowed_paths:\n%s"
            "allowed_commands:\n  - python3 %s\n---\n" % (body, self.WRITER),
            encoding="utf-8")

    def lease(self, owner, *paths, lock_id=None, deadline=None):
        env = dict(os.environ, SCOPE_LEASE_OWNER=owner)
        args = [sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(self.repo),
                "--source", "paths", "--lock-id", lock_id or ("lock-" + owner), "--paths"]
        args += [str(p) for p in paths]
        if deadline:
            args += ["--deadline", deadline]
        proc = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def env(self, owner, **more):
        e = {"PLAN_GATE_SCOPE_LEASE": str(SCOPE_LEASE), "SCOPE_LEASE_OWNER": owner,
             "SCOPE_LEASE_LOCK_ID": "", "PLAN_GATE_REQUIRE_LEASE": "",
             "PLAN_GATE_MCP_WRITE_TOOLS": self.TOOL + ":path"}
        e.update(more)
        return e

    def run_edit(self, path, owner, **more):
        return run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(path)}},
                        self.plans, cwd=self.repo, extra_env=self.env(owner, **more))

    def run_bash(self, cmd, owner, **more):
        # Bash command text uses forward slashes: shlex would eat the backslashes of
        # a Windows path written unquoted.
        # (No command in this class uses a backslash on purpose, so convert them all:
        # the tail of a path such as repo\leased.txt has one too.)
        cmd = cmd.replace("\\", "/")
        return run_gate({"tool_name": "Bash", "tool_input": {"command": cmd}},
                        self.plans, cwd=self.repo, extra_env=self.env(owner, **more))

    def run_mcp(self, path, owner, **more):
        return run_gate({"tool_name": self.TOOL, "tool_input": {"path": str(path)}},
                        self.plans, cwd=self.repo, extra_env=self.env(owner, **more))

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    # --- held by someone else: deny on every write surface -------------------------
    def test_edit_denied_when_held_by_other_and_names_holder_and_age(self):
        self.lease("alice", self.leased)
        p = self.run_edit(self.leased, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        r = self.reason(p)
        self.assertIn("alice", r)
        self.assertIn("lease age", r)
        self.assertNotIn("lock-alice", r)  # the lock id is not handed to the agent

    def test_redirect_denied_when_held_by_other(self):
        self.lease("alice", self.leased)
        p = self.run_bash("echo hi > %s" % self.leased, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_writer_script_denied_when_held_by_other(self):
        self.lease("alice", self.leased)
        p = self.run_bash("python3 %s %s" % (self.WRITER, self.leased), "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_powershell_writer_script_denied_when_held_by_other(self):
        self.lease("alice", self.leased)
        p = run_gate({"tool_name": "PowerShell", "tool_input": {
            "command": "python3 %s '%s'" % (self.WRITER, self.leased)}},
            self.plans, cwd=self.repo, extra_env=self.env("bob"))
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))
        p = run_gate({"tool_name": "PowerShell", "tool_input": {
            "command": "python3 %s '%s'" % (self.WRITER, self.free)}},
            self.plans, cwd=self.repo, extra_env=self.env("bob"))
        self.assertEqual(decision(p), "allow", p.stdout)

    def test_writer_through_wrappers_is_lease_checked(self):
        self.lease("alice", self.leased)
        self.write_plan(self.leased, self.free)
        txt = (self.plans / "active.md").read_text()
        (self.plans / "active.md").write_text(txt.replace(
            "allowed_commands:\n", "allowed_commands:\n  - env\n  - timeout\n"))
        for pre in ("env -i python3", "timeout 5 python3"):
            p = self.run_bash("%s %s %s" % (pre, self.WRITER, self.leased), "bob")
            self.assertEqual(decision(p), "deny", pre + p.stdout)
            self.assertIn("alice", self.reason(p))

    def test_mcp_denied_when_held_by_other(self):
        self.lease("alice", self.leased)
        p = self.run_mcp(self.leased, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_deny_is_logged_with_its_own_class(self):
        self.lease("alice", self.leased)
        logs = Path(self._tmp.name) / "logs"
        self.run_edit(self.leased, "bob", PLAN_GATE_LOG_DIR=str(logs),
                      PLAN_GATE_REQUIRE_APPROVAL="off")
        rows = [json.loads(x) for x in
                (logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["class"], "scope_lease")

    # --- mine / free: the normal plan decision applies -----------------------------
    def test_held_by_me_gets_the_normal_plan_decision(self):
        self.lease("alice", self.leased)
        self.assertEqual(decision(self.run_edit(self.leased, "alice")), "allow")
        self.assertEqual(decision(self.run_bash("echo hi > %s" % self.leased, "alice")), "allow")
        self.assertEqual(decision(self.run_mcp(self.leased, "alice")), "allow")
        # mine, but the plan does not list it -> the plan still denies
        other = self.repo / "other.txt"
        self.lease("alice", other)
        self.assertEqual(decision(self.run_edit(other, "alice")), "deny")

    def test_held_by_me_via_lock_id(self):
        self.lease("alice", self.leased, lock_id="shared-lock")
        p = self.run_edit(self.leased, "bob", SCOPE_LEASE_LOCK_ID="shared-lock")
        self.assertEqual(decision(p), "allow", p.stdout)

    def test_free_path_gets_the_normal_plan_decision(self):
        self.assertEqual(decision(self.run_edit(self.free, "bob")), "allow")
        self.assertEqual(decision(self.run_edit(self.repo / "nope.txt", "bob")), "deny")

    def test_expired_lease_is_free(self):
        self.lease("alice", self.leased, deadline="2000-01-01T00:00:00+00:00")
        self.assertEqual(decision(self.run_edit(self.leased, "bob")), "allow")

    def test_unset_means_today_s_behavior_and_no_subprocess(self):
        self.lease("alice", self.leased)
        marker = Path(self._tmp.name) / "stub-ran"
        stub = Path(self._tmp.name) / "stub.py"
        stub.write_text("open(%r, 'w').write('ran')\n" % str(marker))
        # PLAN_GATE_SCOPE_LEASE empty: lease is ignored entirely (held path is allowed
        # and nothing is run). A stub script would leave a marker if it were invoked.
        p = self.run_edit(self.leased, "bob", PLAN_GATE_SCOPE_LEASE="")
        self.assertEqual(decision(p), "allow", p.stdout)
        self.assertFalse(marker.exists())
        # Control: with the stub configured, the same call does run it.
        self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertTrue(marker.exists(), "stub script should have been run")

    # --- Q9: identity is SCOPE_LEASE_OWNER, else the AGENT's own branch ---------------
    def _worktree(self, name, branch=None):
        wt = Path(self._tmp.name) / name
        args = ["worktree", "add", "-q"] + (["-b", branch] if branch else ["--detach"])
        _git(self.repo, *args, str(wt))
        return wt

    def test_agent_in_another_worktree_is_denied_on_a_file_leased_through_this_one(self):
        # B leases a file of the main checkout; A (its own worktree, branch agent-a) edits
        # the same path through the main checkout's absolute path: must be denied.
        wt_a = self._worktree("wt-a", "agent-a")
        branch_b = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.lease(branch_b, self.leased)  # owner = the main checkout's branch
        env = self.env("")
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.leased)}},
                     self.plans, cwd=wt_a, extra_env=env)
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn(branch_b, self.reason(p))
        # ... and the lease holder (cwd on its own branch) is not blocked by its own lease.
        q = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.leased)}},
                     self.plans, cwd=self.repo, extra_env=env)
        self.assertEqual(decision(q), "allow", q.stdout)

    def test_two_branches_are_two_agents(self):
        wt_a = self._worktree("wt-a2", "agent-a2")
        wt_b = self._worktree("wt-b2", "agent-b2")
        shared = wt_a.resolve() / "shared.txt"
        self.write_plan(shared)
        # agent-a2 leases a path in its own tree.
        env = dict(os.environ, SCOPE_LEASE_OWNER="")
        subprocess.run([sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(wt_a),
                        "--source", "paths", "--lock-id", "la", "--paths", str(shared)],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL)
        mine = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(shared)}},
                        self.plans, cwd=wt_a, extra_env=self.env(""))
        theirs = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(shared)}},
                          self.plans, cwd=wt_b, extra_env=self.env(""))
        self.assertEqual(decision(mine), "allow", mine.stdout)
        self.assertEqual(decision(theirs), "deny", theirs.stdout)

    def test_detached_head_agent_with_no_env_owner_is_denied_on_a_leased_file(self):
        wt = self._worktree("wt-detached")  # detached HEAD: no identity
        self.lease("alice", self.leased)
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.leased)}},
                     self.plans, cwd=wt, extra_env=self.env(""))
        self.assertEqual(decision(p), "deny", p.stdout)

    def test_same_branch_is_one_agent(self):
        # Documented behaviour: the lock's owner is a branch name; any agent running
        # with that branch (or that SCOPE_LEASE_OWNER) is "the same agent".
        branch = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.lease(branch, self.leased)
        for _ in range(2):
            p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.leased)}},
                         self.plans, cwd=self.repo, extra_env=self.env(""))
            self.assertEqual(decision(p), "allow", p.stdout)

    # --- aliasing --------------------------------------------------------------------
    def _symlink(self, link, target):
        try:
            os.symlink(str(target), str(link), target_is_directory=Path(target).is_dir())
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks unavailable")

    def test_symlink_to_a_leased_file_is_denied(self):
        self.lease("alice", self.leased)
        link = self.repo / "alias.txt"
        self._symlink(link, self.leased)
        self.write_plan(link)
        p = self.run_edit(link, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_symlink_in_one_repo_to_a_leased_file_in_another_is_denied(self):
        other = Path(self._tmp.name) / "repo-b"
        other.mkdir()
        _git(other, "init", "-q")
        _git(other, "config", "user.email", "t@example.com")
        _git(other, "config", "user.name", "T")
        (other / "f.txt").write_text("x\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-q", "-m", "s")
        env = dict(os.environ, SCOPE_LEASE_OWNER="alice")
        subprocess.run([sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(other),
                        "--source", "paths", "--lock-id", "lb", "--paths", str(other / "f.txt")],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL)
        link = self.repo / "to-b.txt"
        self._symlink(link, other / "f.txt")
        self.write_plan(link)
        p = self.run_edit(link, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_dotdot_through_a_symlinked_dir_checks_both_spellings(self):
        # lnk -> <repo>/deep/d. The target <repo>/lnk/../x.py is <repo>/x.py when `..` is
        # collapsed textually, but the OS opens <repo>/deep/x.py (the parent of the
        # link's TARGET). The lease is on the REAL file; the lexical spelling is free.
        repo = self.repo.resolve()
        (repo / "deep" / "d").mkdir(parents=True)
        self._symlink(repo / "lnk", repo / "deep" / "d")
        target = "%s/lnk/../x.py" % repo.as_posix()
        real = repo / "deep" / "x.py"
        self.assertNotEqual(os.path.realpath(target), os.path.normpath(target))
        self.write_plan(os.path.normpath(target), real)
        self.lease("alice", real)
        p = self.run_edit(target, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))
        # Control: with nothing leased the same call is a normal plan decision.
        q = self.run_edit(repo / "x.py", "bob")
        self.assertEqual(decision(q), "allow", q.stdout)

    def test_dotdot_through_a_symlinked_dir_lexical_lease_also_denies(self):
        repo = self.repo.resolve()
        (repo / "deep" / "d").mkdir(parents=True)
        self._symlink(repo / "lnk", repo / "deep" / "d")
        target = "%s/lnk/../x.py" % repo.as_posix()
        self.write_plan(os.path.normpath(target))
        self.lease("alice", repo / "x.py")  # the lexical spelling's file
        p = self.run_edit(target, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)

    # --- fail-closed spots -------------------------------------------------------------
    def _stub(self, body):
        stub = Path(self._tmp.name) / "stub.py"
        stub.write_text(body)
        return stub

    def test_unknown_status_denies_even_without_strict_mode(self):
        stub = self._stub("import sys,json\n"
                          "for p in sys.stdin.buffer.read().split(b'\\0'):\n"
                          "    p and print(json.dumps({'path': p.decode(), 'status': 'weird'}))\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("unknown status", self.reason(p))
        self.assertIn("PLAN_GATE_BYPASS", self.reason(p))

    def test_exit_5_without_a_held_row_denies(self):
        stub = self._stub("import sys,json\n"
                          "for p in sys.stdin.buffer.read().split(b'\\0'):\n"
                          "    p and print(json.dumps({'path': p.decode(), 'status': 'free'}))\n"
                          "sys.exit(5)\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("no held path", self.reason(p))

    def test_exit_0_with_empty_stdout_denies(self):
        stub = self._stub("pass\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny", p.stdout)

    def test_row_count_mismatch_denies(self):
        stub = self._stub("import json\n"
                          "for i in range(3): print(json.dumps({'path': 'x', 'status': 'free'}))\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny", p.stdout)

    def test_held_row_with_exit_0_still_denies(self):
        stub = self._stub("import sys,json\n"
                          "for p in sys.stdin.buffer.read().split(b'\\0'):\n"
                          "    p and print(json.dumps({'path': p.decode(), 'status': 'held',"
                          " 'lock_id': 'L', 'owner': 'zed'}))\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("zed", self.reason(p))

    def test_redirect_into_an_always_writable_dir_is_lease_checked(self):
        notes = self.repo / "notes"
        notes.mkdir()
        held = notes / "a.md"
        self.lease("alice", held)
        p = self.run_bash("echo hi > %s" % held, "bob", PLAN_GATE_ALWAYS_WRITABLE=str(notes))
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_relative_script_path_is_a_config_error(self):
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE="scope-lease/scope-lease.py")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("absolute path", self.reason(p))

    def test_timeout_is_one_budget_across_repos(self):
        import time
        other = Path(self._tmp.name) / "repo-c"
        other.mkdir()
        _git(other, "init", "-q")
        slow = self._stub("import time; time.sleep(1.2)\n")
        self.write_plan(self.free, other / "c.txt")
        p0 = time.time()
        proc = run_gate(
            {"tool_name": "Bash", "tool_input": {"command": "echo a > %s && echo b > %s" % (
                self.free.as_posix(), (other / "c.txt").as_posix())}},
            self.plans, cwd=self.repo,
            extra_env=self.env("bob", PLAN_GATE_SCOPE_LEASE=str(slow),
                               PLAN_GATE_SCOPE_LEASE_TIMEOUT="1.5"))
        self.assertEqual(decision(proc), "deny", proc.stdout)
        # Two lookups of 1.2s each would take 2.4s; one 1.5s budget stops at ~1.5s.
        self.assertLess(time.time() - p0, 2.35)

    # --- the agent cannot type its way into someone else's lease -----------------------
    def _plan_allowing(self, *commands):
        (self.plans / "active.md").write_text(
            "---\nname: lease-test\nstatus: active\nallowed_paths:\n  - %s\n"
            "allowed_commands:\n%s---\n" % (
                self.free, "".join("  - %s\n" % c for c in commands)), encoding="utf-8")

    def test_scope_lease_identity_flags_and_env_are_denied(self):
        self._plan_allowing("python3", "env", "SCOPE_LEASE_OWNER", "export", "git", "bash")
        sl = SCOPE_LEASE.as_posix()
        for cmd in (
            "python3 %s release --lock-id lock-alice --owner alice" % sl,
            "python3 %s acquire --owner alice" % sl,
            "python3 %s acquire --owner=alice" % sl,
            "python3 %s release --lock-id=lock-alice" % sl,
            "python3 %s acquire --no-owner" % sl,
            "python3 %s acquire --owner-root /x" % sl,
            "SCOPE_LEASE_OWNER=alice python3 %s release" % sl,
            "SCOPE_LEASE_LOCK_ID=lock-alice python3 %s release" % sl,
            "env SCOPE_LEASE_OWNER=alice python3 %s release" % sl,
            "export SCOPE_LEASE_OWNER=alice",
            "python3 %s acquire $ARGS" % sl,
            "python3 $S acquire --owner alice",
            "bash -c 'python3 %s release --owner alice'" % sl,
        ):
            p = self.run_bash(cmd, "bob")
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertIn("lease identity", self.reason(p), cmd)

    def test_plain_scope_lease_commands_and_reads_are_not_blocked(self):
        self._plan_allowing("python3", "git", "echo")
        sl = SCOPE_LEASE.as_posix()
        for cmd in (
            "python3 %s acquire" % sl,
            "python3 %s release" % sl,
            "python3 %s holder --owner alice %s" % (sl, self.free.as_posix()),
            "python3 %s fence-check --lock-id x" % sl,
            "echo $SCOPE_LEASE_OWNER",
            "git push",
            "git push origin main",
            "git update-ref refs/heads/topic HEAD",
            "git for-each-ref refs/locks/",
            "git show-ref",
        ):
            p = self.run_bash(cmd, "bob")
            self.assertEqual(decision(p), "allow", cmd + " -> " + p.stdout)

    def test_raw_lock_ref_changes_are_denied(self):
        self._plan_allowing("git", "bash")
        for cmd in (
            "git update-ref -d refs/locks/abc123",
            "git update-ref refs/locks/abc123 HEAD",
            "git update-ref --stdin",
            "git update-ref $REF HEAD",
            "git -C %s update-ref -d refs/locks/x" % self.repo.as_posix(),
            "git push origin :refs/locks/abc",
            "git push --delete origin refs/locks/abc",
            "git push origin HEAD:refs/locks/abc",
            "git fetch origin +refs/locks/*:refs/locks/*",
            "git branch -D refs/locks/abc",
            "git tag -d refs/locks/abc",
            "git symbolic-ref refs/locks/abc refs/heads/x",
            "git push origin $REFSPEC",
            "bash -c 'git update-ref -d refs/locks/abc'",
        ):
            p = self.run_bash(cmd, "bob")
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertIn("refs/locks", self.reason(p), cmd)

    def test_writing_the_lock_ref_files_directly_is_denied(self):
        git_dir = self.repo.resolve() / ".git"
        for target in (git_dir / "refs" / "locks" / "abc", git_dir / "packed-refs"):
            self.write_plan(target)
            p = self.run_edit(target, "bob")
            self.assertEqual(decision(p), "deny", p.stdout)
            self.assertIn("ref store", self.reason(p))
        self.write_plan(git_dir / "refs" / "locks" / "x")
        q = self.run_bash("echo x > %s" % (git_dir / "refs" / "locks" / "x").as_posix(), "bob")
        self.assertEqual(decision(q), "deny", q.stdout)

    def test_lease_self_protection_is_off_without_lease_config(self):
        self._plan_allowing("python3", "git")
        sl = SCOPE_LEASE.as_posix()
        p = self.run_bash("python3 %s acquire --owner alice" % sl, "bob",
                          PLAN_GATE_SCOPE_LEASE="")
        self.assertEqual(decision(p), "allow", p.stdout)

    # --- identity anchor: the session's project dir, not wherever the hook runs -------
    def test_claude_project_dir_is_the_identity_anchor(self):
        wt_a = self._worktree("wt-anchor-a", "anchor-a")
        wt_b = self._worktree("wt-anchor-b", "anchor-b")
        shared = wt_a.resolve() / "shared.txt"
        (self.plans / "active.md").write_text(
            "---\nname: x\nstatus: active\nworktree_bypass: true\nallowed_paths:\n  - %s\n---\n"
            % shared, encoding="utf-8")
        env = dict(os.environ, SCOPE_LEASE_OWNER="")
        subprocess.run([sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(wt_a),
                        "--source", "paths", "--lock-id", "la", "--paths", str(shared)],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL)
        edit = {"tool_name": "Edit", "tool_input": {"file_path": str(shared)}}
        # B's hook process runs in A's directory (a `cd`), but its session project is B's:
        # B stays B and is denied.
        p = run_gate(edit, self.plans, cwd=wt_a,
                     extra_env=self.env("", CLAUDE_PROJECT_DIR=str(wt_b)))
        self.assertEqual(decision(p), "deny", p.stdout)
        # A's session project is A's even if the hook process runs elsewhere.
        q = run_gate(edit, self.plans, cwd=wt_b,
                     extra_env=self.env("", CLAUDE_PROJECT_DIR=str(wt_a)))
        self.assertEqual(decision(q), "allow", q.stdout)

    def test_payload_cwd_is_the_anchor_when_no_project_dir(self):
        wt_a = self._worktree("wt-payload-a", "payload-a")
        wt_b = self._worktree("wt-payload-b", "payload-b")
        shared = wt_a.resolve() / "shared.txt"
        self.write_plan(shared)
        env = dict(os.environ, SCOPE_LEASE_OWNER="")
        subprocess.run([sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(wt_a),
                        "--source", "paths", "--lock-id", "la", "--paths", str(shared)],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL)
        edit = {"tool_name": "Edit", "tool_input": {"file_path": str(shared)},
                "cwd": str(wt_b)}
        p = run_gate(edit, self.plans, cwd=wt_a, extra_env=self.env(""))
        self.assertEqual(decision(p), "deny", p.stdout)

    def test_many_targets_go_over_stdin_not_argv(self):
        names = [self.repo / ("f%04d_" % i + "x" * 100 + ".txt") for i in range(400)]
        self.write_plan(*names)
        cmd = " && ".join("echo a > %s" % n.as_posix() for n in names)
        p = self.run_bash(cmd, "bob")
        self.assertEqual(decision(p), "allow", p.stdout[:300])

    def test_bypass_still_works(self):
        self.lease("alice", self.leased)
        p = self.run_edit(self.leased, "bob", PLAN_GATE_BYPASS="on")
        self.assertEqual(decision(p), "allow")

    def test_reads_are_never_lease_checked(self):
        self.lease("alice", self.leased)
        self.assertEqual(decision(self.run_bash("cat %s" % self.leased, "bob")), "allow")

    def test_batch_one_held_target_denies_the_whole_call(self):
        self.lease("alice", self.leased)
        self.write_plan(self.leased, self.free)
        p = self.run_bash("echo a > %s && echo b > %s" % (self.free, self.leased), "bob")
        self.assertEqual(decision(p), "deny", p.stdout)

    # --- strict mode ----------------------------------------------------------------
    def test_require_lease_denies_unleased_path_and_says_acquire(self):
        for p in (self.run_edit(self.free, "bob", PLAN_GATE_REQUIRE_LEASE="on"),
                  self.run_bash("echo hi > %s" % self.free, "bob", PLAN_GATE_REQUIRE_LEASE="1"),
                  self.run_mcp(self.free, "bob", PLAN_GATE_REQUIRE_LEASE="TRUE"),
                  self.run_bash("python3 %s %s" % (self.WRITER, self.free), "bob",
                                PLAN_GATE_REQUIRE_LEASE="yes")):
            self.assertEqual(decision(p), "deny", p.stdout)
            self.assertIn("acquire", self.reason(p))

    def test_require_lease_allows_my_lease(self):
        self.lease("bob", self.free)
        p = self.run_edit(self.free, "bob", PLAN_GATE_REQUIRE_LEASE="on")
        self.assertEqual(decision(p), "allow", p.stdout)

    def test_require_lease_default_off(self):
        self.assertEqual(decision(self.run_edit(self.free, "bob")), "allow")

    def test_require_lease_exempts_always_writable_dirs(self):
        notes = self.repo / "notes"
        notes.mkdir()
        p = self.run_edit(notes / "a.md", "bob", PLAN_GATE_REQUIRE_LEASE="on",
                          PLAN_GATE_ALWAYS_WRITABLE=str(notes))
        self.assertEqual(decision(p), "allow", p.stdout)

    def test_require_lease_without_script_fails_closed(self):
        p = self.run_edit(self.free, "bob", PLAN_GATE_REQUIRE_LEASE="on",
                          PLAN_GATE_SCOPE_LEASE="")
        self.assertEqual(decision(p), "deny")
        self.assertIn("PLAN_GATE_SCOPE_LEASE", self.reason(p))

    # --- failure modes: fail closed, with the bypass how-to ------------------------
    def test_holder_error_denies(self):
        stub = Path(self._tmp.name) / "stub.py"
        stub.write_text("import sys; sys.stderr.write('boom'); sys.exit(3)\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny")
        self.assertIn("PLAN_GATE_BYPASS", self.reason(p))
        self.assertIn("boom", self.reason(p))

    def test_garbage_output_denies(self):
        stub = Path(self._tmp.name) / "stub.py"
        stub.write_text("print('not json')\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub))
        self.assertEqual(decision(p), "deny")

    def test_missing_script_denies(self):
        p = self.run_edit(self.free, "bob",
                          PLAN_GATE_SCOPE_LEASE=str(Path(self._tmp.name) / "nope.py"))
        self.assertEqual(decision(p), "deny")
        self.assertIn("PLAN_GATE_BYPASS", self.reason(p))

    def test_timeout_denies(self):
        stub = Path(self._tmp.name) / "slow.py"
        stub.write_text("import time; time.sleep(30)\n")
        p = self.run_edit(self.free, "bob", PLAN_GATE_SCOPE_LEASE=str(stub),
                          PLAN_GATE_SCOPE_LEASE_TIMEOUT="0.5")
        self.assertEqual(decision(p), "deny")
        self.assertIn("timed out", self.reason(p))

    def test_not_in_a_repo_is_a_denied_error_not_a_crash(self):
        loose = Path(self._tmp.name) / "loose"
        loose.mkdir()
        self.write_plan(loose / "f.txt")
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(loose / "f.txt")}},
                     self.plans, cwd=loose, extra_env=self.env("bob"))
        self.assertEqual(decision(p), "deny")

    # --- canonicalization stays in scope-lease --------------------------------------
    def test_case_fold_matches_scope_lease_when_git_says_ignorecase(self):
        _git(self.repo, "config", "core.ignorecase", "true")
        self.write_plan(self.repo / "Foo.md", self.repo / "foo.md")
        self.lease("alice", self.repo / "Foo.md")
        p = self.run_edit(self.repo / "foo.md", "bob")
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("alice", self.reason(p))

    def test_case_is_distinct_when_git_says_case_sensitive(self):
        _git(self.repo, "config", "core.ignorecase", "false")
        self.write_plan(self.repo / "Foo.md", self.repo / "foo.md")
        self.lease("alice", self.repo / "Foo.md")
        self.assertEqual(decision(self.run_edit(self.repo / "foo.md", "bob")), "allow")

    def test_unicode_fold_matches_scope_lease(self):
        import unicodedata
        _git(self.repo, "config", "core.precomposeunicode", "true")
        nfc = unicodedata.normalize("NFC", "café.md")
        nfd = unicodedata.normalize("NFD", "café.md")
        self.write_plan(self.repo / nfc, self.repo / nfd)
        self.lease("alice", self.repo / nfc)
        p = self.run_edit(self.repo / nfd, "bob")
        self.assertEqual(decision(p), "deny", p.stdout)


class ApprovalTests(unittest.TestCase):
    """Plan approval: a plan is active only if `status: active` AND the decision
    log holds an `approve` row for its exact bytes (required whenever
    PLAN_GATE_LOG_DIR is set, unless PLAN_GATE_REQUIRE_APPROVAL is off)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.logs = root / "logs"
        self.work = root / "work"
        self.work.mkdir()
        self.allowed = self.work / "allowed.txt"
        self.plan = self.plans / "active.md"
        write_active_plan(self.plans, str(self.allowed))

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args, log_dir="default", env_extra=None, stdin=subprocess.DEVNULL):
        env = dict(os.environ)
        env["PLAN_GATE_LOG_DIR"] = str(self.logs) if log_dir == "default" else (log_dir or "")
        env["PLAN_GATE_REQUIRE_APPROVAL"] = ""
        env["_PLAN_GATE_TEST_PLANS_DIR"] = str(self.plans)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(GATE), *args], stdin=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=env, cwd=str(self.work))

    def approve(self, *extra):
        return self.cli("approve", str(self.plan), "--no-tty-check", *extra)

    def edit(self, path=None, **kw):
        kw.setdefault("log_dir", self.logs)
        kw.setdefault("cwd", self.work)
        env = {"PLAN_GATE_REQUIRE_APPROVAL": ""}
        env.update(kw.pop("extra_env", None) or {})
        return run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(path or self.allowed)}},
                        self.plans, extra_env=env, **kw)

    def bash(self, cmd, **kw):
        kw.setdefault("log_dir", self.logs)
        kw.setdefault("cwd", self.work)
        env = {"PLAN_GATE_REQUIRE_APPROVAL": ""}
        env.update(kw.pop("extra_env", None) or {})
        return run_gate({"tool_name": "Bash", "tool_input": {"command": cmd}},
                        self.plans, extra_env=env, **kw)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def rows(self):
        f = self.logs / "plan-gate-decisions.log"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]

    # --- the core loop -------------------------------------------------------------
    def test_unapproved_active_plan_is_denied_with_how_to_approve(self):
        p = self.edit()
        self.assertEqual(decision(p), "deny", p.stdout)
        r = self.reason(p)
        self.assertIn("marked active but not approved (or changed since approval)", r)
        self.assertIn("plan-gate.py approve", r)
        self.assertIn("active.md", r)
        # Addressed to a human, with the exact command: interpreter, absolute gate
        # path, absolute plan path and the log directory this gate is reading.
        self.assertIn("Ask a human to run, in a terminal:", r)
        self.assertIn(str(GATE), r.replace('"', ""))
        self.assertIn(str(self.plan), r.replace('"', ""))
        self.assertIn("--log-dir", r)
        self.assertIn("--plans-dir", r)
        self.assertIn(str(self.logs), r.replace('"', "").replace("'", ""))
        self.assertEqual(self.rows()[-1]["class"], "plan_not_approved")

    def test_the_command_in_the_deny_message_works(self):
        import shlex
        r = self.reason(self.edit())
        cmd = r.split("in a terminal: ", 1)[1]
        argv = shlex.split(cmd, posix=(os.name != "nt"))
        env = dict(os.environ)
        env.pop("PLAN_GATE_LOG_DIR", None)  # the human's shell has no PLAN_GATE_* vars
        env.pop("_PLAN_GATE_TEST_PLANS_DIR", None)
        a = subprocess.run(argv + ["--no-tty-check"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(decision(self.edit()), "allow")

    # --- Q8: the latest approval for a plan name wins ---------------------------------
    def test_latest_approval_wins_and_restoring_old_bytes_does_not_reactivate(self):
        v1 = self.plan.read_bytes()
        v2 = v1 + b"\nversion two\n"
        self.assertEqual(self.approve().returncode, 0)           # approve v1
        self.assertEqual(decision(self.edit()), "allow")
        self.plan.write_bytes(v2)
        self.assertEqual(self.approve().returncode, 0)           # approve v2
        self.assertEqual(decision(self.edit()), "allow")
        self.plan.write_bytes(v1)                                # restore v1
        p = self.edit()
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("changed since approval", self.reason(p))
        self.assertEqual(self.approve().returncode, 0)           # re-approve v1
        self.assertEqual(decision(self.edit()), "allow")
        self.plan.write_bytes(v2)                                # v2 is now the retired one
        self.assertEqual(decision(self.edit()), "deny")

    def test_verify_log_marks_superseded_approvals(self):
        v1 = self.plan.read_bytes()
        self.assertEqual(self.approve().returncode, 0)
        self.plan.write_bytes(v1 + b"\nv2\n")
        self.assertEqual(self.approve().returncode, 0)
        v = self.cli("verify-log")
        self.assertEqual(v.returncode, 0, v.stdout)
        self.assertIn("2 plan approval(s) recorded", v.stdout)
        self.assertIn("superseded by line 2", v.stdout)

    # --- chain linkage at gate time ---------------------------------------------------
    def test_a_row_transplanted_from_another_log_is_not_honored(self):
        other = Path(self._tmp.name) / "other-logs"
        a = self.cli("approve", str(self.plan), "--no-tty-check", "--log-dir", str(other))
        self.assertEqual(a.returncode, 0, a.stderr)
        stolen = (other / "plan-gate-decisions.log").read_bytes()
        self.edit()  # real log now has a normal row (a different chain head)
        with open(str(self.logs / "plan-gate-decisions.log"), "ab") as fh:
            fh.write(stolen)  # self-consistent row, wrong predecessor
        p = self.edit()
        self.assertEqual(decision(p), "deny", p.stdout)
        v = self.cli("verify-log")
        self.assertEqual(v.returncode, 2)
        self.assertIn("BROKEN LINK, not honoured", v.stdout)

    def test_a_row_right_after_the_genesis_is_honored_and_blank_lines_do_not_break_links(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")

    def test_large_blocks_and_row_at_a_block_boundary(self):
        # Approve rows spread across >1 MiB so the streaming scan crosses block edges.
        log = self.logs / "plan-gate-decisions.log"
        self.logs.mkdir()
        prev = "0" * 64
        fh = log.open("ab")
        for n in range(6000):
            row = {"class": "allowed_paths", "decision": "allow", "surface": "file",
                   "target": "/x/%d" % n, "reason": "r" * 200, "prev": prev}
            canon = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            row["h"] = hashlib.sha256((prev + "\n" + canon).encode("utf-8")).hexdigest()
            prev = row["h"]
            fh.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
        fh.close()
        self.assertGreater(log.stat().st_size, 1 << 20)
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")
        self.assertEqual(self.cli("verify-log").returncode, 0)

    # --- approve: --log-dir, plan resolution, what the human sees ---------------------
    def test_log_dir_flag_overrides_the_environment(self):
        elsewhere = Path(self._tmp.name) / "elsewhere"
        a = self.cli("approve", str(self.plan), "--no-tty-check", "--log-dir", str(elsewhere),
                     log_dir=str(self.logs / "ignored"))
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertTrue((elsewhere / "plan-gate-decisions.log").exists())
        self.assertFalse((self.logs / "ignored").exists())

    def test_relative_name_resolves_against_the_plans_dir_not_the_cwd(self):
        decoy = self.work / "active.md"
        decoy.write_text("---\nname: decoy\nstatus: active\n---\n", encoding="utf-8")
        a = self.cli("approve", "active.md", "--no-tty-check")  # cwd = work
        self.assertEqual(a.returncode, 0, a.stderr)
        (row,) = [r for r in self.rows() if r["class"] == "approve"]
        self.assertEqual(row["sha256"], hashlib.sha256(self.plan.read_bytes()).hexdigest())

    def test_plan_outside_the_plans_dir_is_refused(self):
        stray = self.work / "stray.md"
        stray.write_text(self.plan.read_text(encoding="utf-8"), encoding="utf-8")
        a = self.cli("approve", str(stray), "--no-tty-check")
        self.assertEqual(a.returncode, 2)
        self.assertIn("not in the plans directory", a.stderr)
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())

    def test_plans_dir_flag(self):
        other = Path(self._tmp.name) / "other-plans"
        other.mkdir()
        (other / "p.md").write_text("---\nname: p\nstatus: active\n---\n", encoding="utf-8")
        a = self.cli("approve", "p.md", "--no-tty-check", "--plans-dir", str(other))
        self.assertEqual(a.returncode, 0, a.stderr)
        b = self.cli("approve", str(self.plan), "--no-tty-check", "--plans-dir", str(other))
        self.assertEqual(b.returncode, 2)

    def test_approve_shows_what_is_being_approved(self):
        a = self.approve()
        out = a.stdout
        self.assertIn(str(self.plan.resolve()), out)
        self.assertIn("%d bytes" % len(self.plan.read_bytes()), out)
        self.assertIn(hashlib.sha256(self.plan.read_bytes()).hexdigest(), out)
        self.assertIn("allowed_paths (1)", out)
        self.assertIn(str(self.allowed), out)
        self.assertIn("allowed_commands (1)", out)
        self.assertIn("npm test", out)
        self.assertNotIn("Type 'yes'", out)  # --no-tty-check: no prompt

    def test_approve_then_allow(self):
        a = self.approve()
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn(hashlib.sha256(self.plan.read_bytes()).hexdigest(), a.stdout)
        self.assertEqual(decision(self.edit()), "allow")
        self.assertEqual(decision(self.edit(self.work / "other.txt")), "deny")  # plan still rules

    def test_edit_after_approve_voids_it_and_reapprove_restores_it(self):
        self.assertEqual(self.approve().returncode, 0)
        self.plan.write_text(self.plan.read_text(encoding="utf-8") + "\nsneaky widening\n",
                             encoding="utf-8")
        p = self.edit()
        self.assertEqual(decision(p), "deny")
        self.assertIn("changed since approval", self.reason(p))
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")

    def test_a_single_byte_changes_the_hash(self):
        self.assertEqual(self.approve().returncode, 0)
        data = self.plan.read_bytes()
        self.plan.write_bytes(data.replace(b"test body", b"test bodz"))
        self.assertEqual(decision(self.edit()), "deny")

    def test_approval_is_per_plan_name(self):
        self.assertEqual(self.approve().returncode, 0)
        copy = self.plans / "copy.md"
        copy.write_bytes(self.plan.read_bytes())
        self.plan.write_text("---\nname: x\nstatus: archived\n---\n", encoding="utf-8")
        p = self.edit()
        self.assertEqual(decision(p), "deny")
        self.assertIn("copy.md", self.reason(p))

    def test_unapproved_draft_does_not_disturb_an_approved_plan(self):
        self.assertEqual(self.approve().returncode, 0)
        (self.plans / "draft.md").write_text(
            "---\nname: draft\nstatus: active\nallowed_paths:\n  - %s\n---\n" % self.allowed,
            encoding="utf-8")
        self.assertEqual(decision(self.edit()), "allow")  # no multiple_active_plans deny

    def test_approve_accepts_plan_name_relative_to_plans_dir(self):
        a = self.cli("approve", "active.md", "--no-tty-check")
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(decision(self.edit()), "allow")

    def test_crlf_plan_approves_and_parses(self):
        self.plan.write_bytes(self.plan.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")

    # --- the row ---------------------------------------------------------------------
    def test_row_carries_identity_hash_and_flag(self):
        subprocess.run(["git", "init", "-q", str(self.plans)], check=True)
        _git(self.plans, "config", "user.name", "Pat Approver")
        _git(self.plans, "config", "user.email", "pat@example.com")
        self.assertEqual(self.approve().returncode, 0)
        (row,) = [r for r in self.rows() if r["class"] == "approve"]
        self.assertEqual(row["plan"], "active.md")
        self.assertEqual(row["sha256"], hashlib.sha256(self.plan.read_bytes()).hexdigest())
        self.assertEqual(row["git_user_name"], "Pat Approver")
        self.assertEqual(row["git_user_email"], "pat@example.com")
        self.assertEqual(row["no_tty_check"], "true")
        for key in ("ts", "os_user", "hostname", "target", "prev", "h"):
            self.assertIn(key, row)
        self.assertTrue(row["ts"].endswith("Z"))

    def test_git_identity_is_empty_when_unavailable(self):
        iso = Path(self._tmp.name) / "isolated-home"
        iso.mkdir()
        env = {"GIT_CONFIG_GLOBAL": str(iso / "none"), "GIT_CONFIG_NOSYSTEM": "1",
               "HOME": str(iso), "USERPROFILE": str(iso), "XDG_CONFIG_HOME": str(iso)}
        a = self.cli("approve", str(self.plan), "--no-tty-check", env_extra=env)
        self.assertEqual(a.returncode, 0, a.stderr)
        (row,) = [r for r in self.rows() if r["class"] == "approve"]
        self.assertEqual(row["git_user_name"], "")
        self.assertEqual(row["git_user_email"], "")

    def test_approve_rows_share_the_chain_and_verify(self):
        self.edit()  # a normal decision row first
        self.assertEqual(self.approve().returncode, 0)
        self.edit()
        v = self.cli("verify-log")
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        self.assertIn("chain intact", v.stdout)
        self.assertIn("1 plan approval(s) recorded", v.stdout)
        self.assertIn("active.md", v.stdout)

    def test_verify_log_flags_a_tampered_approve_row(self):
        self.assertEqual(self.approve().returncode, 0)
        log = self.logs / "plan-gate-decisions.log"
        text = log.read_text(encoding="utf-8")
        digest = hashlib.sha256(self.plan.read_bytes()).hexdigest()
        log.write_text(text.replace(digest, "0" * 64), encoding="utf-8")
        v = self.cli("verify-log")
        self.assertEqual(v.returncode, 2, v.stdout)
        self.assertIn("row hash invalid", v.stdout)
        self.assertIn("1 plan approval(s) recorded", v.stdout)
        self.assertIn("not honoured", v.stdout)
        self.assertEqual(decision(self.edit()), "deny")

    def test_a_forged_row_with_a_bad_hash_is_not_honored(self):
        self.logs.mkdir()
        digest = hashlib.sha256(self.plan.read_bytes()).hexdigest()
        forged = {"class": "approve", "surface": "approve", "plan": "active.md",
                  "sha256": digest, "prev": "0" * 64, "h": "f" * 64}
        (self.logs / "plan-gate-decisions.log").write_text(
            json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        self.assertEqual(decision(self.edit()), "deny")

    def test_deny_rows_still_logged_after_approval_rows(self):
        self.assertEqual(self.approve().returncode, 0)
        self.edit(self.work / "other.txt")
        self.assertEqual(self.rows()[-1]["decision"], "deny")
        self.assertEqual(self.cli("verify-log").returncode, 0)

    # --- who may run approve ---------------------------------------------------------
    def test_approve_refuses_without_a_terminal(self):
        a = self.cli("approve", str(self.plan))
        self.assertEqual(a.returncode, 3)
        self.assertIn("interactive terminal", a.stderr)
        self.assertIn("--no-tty-check", a.stderr)
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())
        self.assertEqual(decision(self.edit()), "deny")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "openpty"), "needs a pty")
    def test_approve_succeeds_on_a_terminal_and_records_no_flag(self):
        master, slave = os.openpty()
        try:
            os.write(master, b"yes\n")
            a = self.cli("approve", str(self.plan), stdin=slave)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("Type 'yes'", a.stdout)
        (row,) = [r for r in self.rows() if r["class"] == "approve"]
        self.assertEqual(row["no_tty_check"], "false")
        self.assertEqual(decision(self.edit()), "allow")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "openpty"), "needs a pty")
    def test_anything_but_yes_on_a_terminal_aborts_with_no_row(self):
        for answer in (b"no\n", b"\n", b"y\n", b"YES\n"):
            master, slave = os.openpty()
            try:
                os.write(master, answer)
                a = self.cli("approve", str(self.plan), stdin=slave)
            finally:
                os.close(master)
                os.close(slave)
            self.assertEqual(a.returncode, 1, (answer, a.stdout, a.stderr))
            self.assertFalse((self.logs / "plan-gate-decisions.log").exists(), answer)

    def test_approve_needs_a_log_dir(self):
        a = self.cli("approve", str(self.plan), "--no-tty-check", log_dir="")
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("PLAN_GATE_LOG_DIR", a.stderr)

    def test_approve_usage_errors(self):
        self.assertEqual(self.cli("approve").returncode, 2)
        self.assertEqual(self.cli("approve", str(self.plan), "--bogus").returncode, 2)
        self.assertNotEqual(self.cli("approve", str(self.work / "missing.md"),
                                     "--no-tty-check").returncode, 0)

    def test_gated_agent_cannot_run_approve(self):
        self.assertEqual(self.approve().returncode, 0)
        plan = self.plan.read_text(encoding="utf-8").replace(
            "  - npm test", "  - npm test\n  - python3 \n  - bash \n  - env \n  - /opt")
        self.plan.write_text(plan, encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)  # re-approve the widened plan
        gate = "/opt/strictlock/plan-gate/plan-gate.py"
        for cmd in (
            "python3 %s approve p.md" % gate,
            "PYTHONUTF8=1 python %s approve p.md" % gate,
            "env python3 %s approve p.md" % gate,
            "python3 -u %s approve p.md --no-tty-check" % gate,
            "py -3 %s approve p.md" % gate,
            "python3.12 C:/tools/PLAN-GATE.PY approve p.md",
            "python3 \"%s\" approve p.md" % gate,
            "cd /opt && python3 plan-gate.py approve p.md",
            "echo ok; python3 %s approve p.md" % gate,
            "%s approve p.md" % gate,
            "/opt/plan-gate approve p.md",
            "python3 $GATE approve p.md",
            "python3 ${HOME}/x approve p.md",
            "python3 %s $SUB p.md" % gate,
            "python3 %s `echo approve` p.md" % gate,
            "bash -c 'python3 %s approve p.md'" % gate,
            "bash -c \"python3 plan-gate.py approve p.md\"",
            "cat %s | python3 - approve p.md" % gate,
            "python3 -c 'import runpy; runpy.run_path(\"plan-gate.py\")' approve",
            "script -q /dev/null python3 %s approve p.md" % gate,
            "echo $(python3 %s approve p.md)" % gate,
        ):
            p = self.bash(cmd)
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertEqual(self.rows()[-1]["class"], "approve_self_protect", cmd)

    def test_approve_denied_through_wrappers_and_interpreter_spellings(self):
        gate = "/opt/strictlock/plan-gate/plan-gate.py"
        for cmd in (
            "env -i python3 %s approve p.md" % gate,
            "env -- python3 %s approve p.md" % gate,
            "env FOO=x python3 %s approve p.md" % gate,
            "env -u FOO python3 %s approve p.md" % gate,
            "time -p python3 %s approve p.md" % gate,
            "sudo -u bob python3 %s approve p.md" % gate,
            "nice -n 5 python3 %s approve p.md" % gate,
            "timeout 5 python3 %s approve p.md" % gate,
            "pythonw %s approve p.md" % gate,
            "pypy3 %s approve p.md" % gate,
            "python3.13t %s approve p.md" % gate,
            "python3 --check-hash-based-pycs never %s approve p.md" % gate,
            "python3 -uX utf8 %s approve p.md" % gate,
            "env -S 'python3 %s approve p.md'" % gate,
            "./plan-gate.py approve p.md",
        ):
            p = self.bash(cmd)
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertEqual(self.rows()[-1]["class"], "approve_self_protect", cmd)

    def test_powershell_segmented_approve_still_denied_before_fast_paths(self):
        for cmd in ("Get-ChildItem | python plan-gate.py approve p.md",
                    "Get-Location && py -3 plan-gate.py approve p.md",
                    "Get-Content x; & python3 'C:/t/plan-gate.py' approve p.md"):
            p = run_gate({"tool_name": "PowerShell", "tool_input": {"command": cmd}},
                         self.plans, cwd=self.work, log_dir=self.logs,
                         extra_env={"PLAN_GATE_REQUIRE_APPROVAL": ""})
            self.assertEqual(decision(p), "deny", cmd)
            self.assertEqual(self.rows()[-1]["class"], "approve_self_protect", cmd)

    def test_mcp_without_a_check_stays_no_decision_with_approval_and_lease_on(self):
        env = {"PLAN_GATE_REQUIRE_APPROVAL": "on", "PLAN_GATE_REQUIRE_LEASE": "on",
               "PLAN_GATE_SCOPE_LEASE": str(SCOPE_LEASE)}
        p = run_gate({"tool_name": "mcp__mail__send", "tool_input": {"to": "x"}},
                     self.plans, cwd=self.work, log_dir=self.logs, extra_env=env)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))
        p = run_gate({"tool_name": "mcp__mail__send", "tool_input": {"to": "x"}},
                     self.plans, cwd=self.work, log_dir=self.logs, gate_on=False, extra_env=env)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))
        p = run_gate({"tool_name": "mcp__mail__send", "tool_input": {"to": "x"}},
                     self.plans, cwd=self.work, log_dir=self.logs,
                     extra_env=dict(env, PLAN_GATE_BYPASS="on"))
        self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))

    def test_approve_denied_even_with_no_plan_and_approval_off(self):
        self.plan.unlink()
        p = self.bash("python3 /opt/plan-gate.py approve p.md",
                      extra_env={"PLAN_GATE_REQUIRE_APPROVAL": "off"})
        self.assertEqual(decision(p), "deny")
        self.assertIn("human act", self.reason(p))

    def test_powershell_approve_is_denied(self):
        for cmd in ("python C:\\tools\\plan-gate.py approve p.md",
                    "& python C:\\tools\\plan-gate.py approve p.md",
                    "Get-ChildItem; python plan-gate.py approve p.md",
                    "powershell -Command \"python plan-gate.py approve p.md\""):
            p = run_gate({"tool_name": "PowerShell", "tool_input": {"command": cmd}},
                         self.plans, cwd=self.work, log_dir=self.logs,
                         extra_env={"PLAN_GATE_REQUIRE_APPROVAL": ""})
            self.assertEqual(decision(p), "deny", cmd)

    def test_other_mentions_of_plan_gate_and_approve_are_not_blocked(self):
        self.assertEqual(self.approve().returncode, 0)
        plan = self.plan.read_text(encoding="utf-8").replace(
            "  - npm test", "  - npm test\n  - git commit\n  - python3 ")
        self.plan.write_text(plan, encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        for cmd in ('git commit -m "docs: plan-gate approve flow"',
                    'echo "run plan-gate.py approve yourself"',
                    "python3 /opt/plan-gate.py verify-log",
                    "python3 other.py approve p.md"):
            p = self.bash(cmd)
            self.assertEqual(decision(p), "allow", cmd + " -> " + p.stdout)

    def test_agent_cannot_write_the_log_or_forge_a_row(self):
        self.assertEqual(self.approve().returncode, 0)
        log = self.logs / "plan-gate-decisions.log"
        p = self.edit(log)
        self.assertEqual(decision(p), "deny")
        q = self.bash("echo x >> %s" % log.as_posix())
        self.assertIn("PLAN_GATE_LOG_DIR", self.reason(q))
        self.assertEqual(decision(q), "deny")

    # --- switches -------------------------------------------------------------------
    def test_require_approval_off_values_restore_legacy_behavior(self):
        for v in ("off", "0", "false", "no", "OFF", " No "):
            p = self.edit(extra_env={"PLAN_GATE_REQUIRE_APPROVAL": v})
            self.assertEqual(decision(p), "allow", v + " -> " + p.stdout)

    def test_only_an_explicit_off_disables_approval(self):
        for v in ("", "on", "maybe", "disabled", "2"):
            p = self.edit(extra_env={"PLAN_GATE_REQUIRE_APPROVAL": v})
            self.assertEqual(decision(p), "deny", v)

    def test_unrecognised_values_without_a_log_dir_are_a_config_error(self):
        for v in ("required", "enabled", "garbage", "on", "2"):
            p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
                         self.plans, cwd=self.work, extra_env={"PLAN_GATE_REQUIRE_APPROVAL": v})
            self.assertEqual(decision(p), "deny", v)
            self.assertIn("PLAN_GATE_LOG_DIR", self.reason(p), v)
        for v in ("off", "0", "false", "no"):  # explicit off values still work
            p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
                         self.plans, cwd=self.work, extra_env={"PLAN_GATE_REQUIRE_APPROVAL": v})
            self.assertEqual(decision(p), "allow", v)

    def test_no_log_dir_is_legacy_behavior(self):
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
                     self.plans, cwd=self.work, extra_env={"PLAN_GATE_REQUIRE_APPROVAL": ""})
        self.assertEqual(decision(p), "allow")

    def test_require_approval_on_without_log_dir_is_a_config_error(self):
        p = run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(self.allowed)}},
                     self.plans, cwd=self.work,
                     extra_env={"PLAN_GATE_REQUIRE_APPROVAL": "on"})
        self.assertEqual(decision(p), "deny")
        self.assertIn("PLAN_GATE_LOG_DIR", self.reason(p))

    def test_bypass_still_works(self):
        p = self.edit(extra_env={"PLAN_GATE_BYPASS": "on"})
        self.assertEqual(decision(p), "allow")

    def test_read_only_commands_and_always_writable_dirs_need_no_approval(self):
        self.assertEqual(decision(self.bash("git status")), "allow")
        p = self.edit(self.plans / "new-draft.md",
                      extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.plans)})
        self.assertEqual(decision(p), "allow")  # agents may keep drafting plans

    def test_a_large_log_is_scanned_within_a_generous_time_bound(self):
        import time
        self.assertEqual(self.approve().returncode, 0)
        log = self.logs / "plan-gate-decisions.log"
        # 20k chained filler rows after the approval: a scan must still find it.
        fh = log.open("ab")
        prev = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["h"]
        for n in range(20000):
            row = {"class": "allowed_paths", "decision": "allow", "surface": "file",
                   "target": "/x/%d" % n, "prev": prev}
            canon = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            row["h"] = hashlib.sha256((prev + "\n" + canon).encode("utf-8")).hexdigest()
            prev = row["h"]
            fh.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
        fh.close()
        self.assertEqual(self.cli("verify-log").returncode, 0)
        t0 = time.time()
        self.assertEqual(decision(self.edit()), "allow")
        # ~6 MB log; a streaming scan is tens of ms. 10 s is generous enough for
        # the slowest CI runner, tight enough to catch a quadratic regression.
        self.assertLess(time.time() - t0, 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
