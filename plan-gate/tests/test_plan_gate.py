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
    """"allow" / "deny" from the hook JSON; "defer" when the gate emitted NO decision
    (exit 0, empty stdout): the harness then runs its own permission flow."""
    if proc.returncode == 0 and not proc.stdout.strip():
        return "defer"
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
        # Gate off: no opinion, so the harness runs its own permission flow.
        self.assertEqual(decision(p), "defer")
        self.assertEqual(p.stdout.strip(), "")
        self.assertEqual(p.returncode, 0)

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
        # Read-only: the gate does not object but did not check it against the
        # plan, so it defers to the harness (no explicit allow).
        self.assertEqual(decision(p), "defer")

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
        self.assertEqual(decision(p), "defer")


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
        self.assertEqual(decision(p), "defer")
        (row,) = self.rows()
        self.assertEqual(
            (row["decision"], row["surface"], row["class"]),
            ("defer", "bash", "read_only_bypass"),
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
        self.assertEqual(decision(p), "defer")
        (row,) = self.rows()
        self.assertEqual(row["class"], "always_writable")
        self.assertEqual(row["decision"], "defer")

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
        self.assertEqual(decision(p), "defer")
        (row,) = self.rows()
        self.assertEqual(
            (row["decision"], row["surface"], row["class"]),
            ("defer", "gate", "emergency_bypass"),
        )
        self.assertEqual(row["command"], "rm -rf /")

    def test_emergency_bypass_logged_even_in_deny_mode(self):
        p = self.gate(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
            extra_env={"PLAN_GATE_BYPASS": "on", "PLAN_GATE_LOG_DECISIONS": "deny"},
        )
        self.assertEqual(decision(p), "defer")
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
        # An explicit allow: the active plan checked and approved the command.
        p = self.run_cmd(cmd, **kw)
        self.assertEqual(decision(p), "allow", "should ALLOW: %r\n%s" % (cmd, p.stdout))
        return p

    def assertDeferred(self, cmd, **kw):
        # No decision (read-only / bare cd / always-writable): the harness decides.
        p = self.run_cmd(cmd, **kw)
        self.assertEqual(decision(p), "defer", "should DEFER: %r\n%s" % (cmd, p.stdout))
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
            self.assertDeferred(cmd)

    def test_no_plan_redirect_into_always_writable_dir_allowed(self):
        scratch = self.work / "scratch"
        scratch.mkdir()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(scratch)}
        self.assertDeferred("echo note > %s" % (scratch / "n.md"), extra_env=env)
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
            elif expect == "defer":
                self.assertDeferred(cmd, **kw)
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
        self.both("git status # a; trailing comment", "defer")
        self.both("git status\n# whole line comment ; rm -rf x\ngit log", "defer")
        self.both("git status;# c", "defer")
        self.both("echo a \\\n# continued comment", "defer")

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
        self.both("git -C /tmp status", "defer")
        self.both("git -C '/tmp/a b$c' status", "defer")
        self.both("git -C \"/tmp/a b\" status", "defer")

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
            self.both(cmd, "defer")

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
        self.both("echo x > '%s/aw/lit*' " % self.root, "defer", extra_env=aw)

    def test_subshell_group_of_allowed_commands(self):
        self.both("(cd sub && git status)", "defer")
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
        self.assertEqual(decision(p), "defer")

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
            ("defer", "read_only_bypass"),
            ("defer", "navigation"),
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
        self.assertNoDecision(self.TOOL, {"path": note}, extra_env=env)  # no plan check ran
        # mixed: the always-writable one passes, the other still needs the plan
        self.assertAllowed(self.MOVE, {"source": note, "destination": self.a}, extra_env=env)
        self.assertDenied(self.MOVE, {"source": note, "destination": self.c}, extra_env=env)

    def test_always_writable_works_before_any_plan_exists(self):
        aw = self.root / "mem"
        aw.mkdir()
        (self.plans / "active.md").unlink()
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}
        self.assertNoDecision(self.TOOL, {"path": (aw / "n.md").as_posix()}, extra_env=env)
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
                         ("defer", "mcp", "mcp_unconfigured", "mcp__other__read"))

    def test_unconfigured_mcp_allow_row_respects_deny_only_mode(self):
        self.assertNoDecision("mcp__other__read", {"q": "x"}, log_dir=self.logs,
                              extra_env={"PLAN_GATE_LOG_DECISIONS": "deny"})
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())

    def test_non_mcp_unknown_tool_still_unlogged(self):
        self.assertNoDecision("Read", {"file_path": self.a}, log_dir=self.logs)
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
        self.assertEqual(decision(p), "defer")  # gate off: no tool gets an allow

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

    def assertDeferred(self, cmd, tool="Bash", **kw):
        p = self.run_tool(tool, cmd, **kw)
        self.assertEqual(decision(p), "defer", "should DEFER: %r\n%s" % (cmd, p.stdout))
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
        self.assertDeferred("grep -n python %s" % self.WRITER)
        self.assertDeferred("cat %s" % self.WRITER)
        self.plan(commands=("echo",))
        self.assertDeferred("echo python3 %s" % self.WRITER)

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
            self.assertDeferred("git status", extra_env=env)

    def test_malformed_env_mentions_one_based(self):
        self.plan()
        p = self.assertDenied("python3 %s %s" % (self.WRITER, self.out),
                              extra_env={"PLAN_GATE_WRITER_SCRIPTS": "w.py:0"})
        self.assertIn("1-based", p.stdout)

    def test_bypass_still_honored_with_malformed_env(self):
        self.plan()
        self.assertDeferred("python3 %s %s" % (self.WRITER, self.other),
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
        self.assertDeferred("Get-ChildItem", tool="PowerShell",
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
            self.assertEqual(decision(self.ps(cmd)), "defer", cmd)  # read-only: no opinion

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
        self.assertEqual(decision(self.ps("Get-ChildItem; Get-Location | Get-Content x")), "defer")
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
            self.assertEqual(decision(p), "defer", msg=f"PLAN_GATE_BYPASS={v!r}")

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
            self.assertEqual(decision(p), "defer", msg=f"PLAN_GATE={v!r}")

    def test_bypass_row_records_the_actual_value(self):
        # A bypass emits no decision JSON, so the value is on the audit row.
        logs = Path(self._tmp.name) / "logs"
        p = run_gate(self.denied_payload, self.plans, cwd=self.work, log_dir=logs,
                     extra_env={"PLAN_GATE_BYPASS": "Yes"})
        self.assertEqual(p.stdout.strip(), "")
        (row,) = [json.loads(x) for x in
                  (logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(row["class"], "emergency_bypass")
        self.assertIn("PLAN_GATE_BYPASS=Yes", row["reason"])

    def test_crash_path_honors_truthy_bypass(self):
        for v in ("yes", "1", "TRUE"):
            p = run_gate(self.denied_payload, self.plans, cwd=self.work,
                         extra_env={"PLAN_GATE_BYPASS": v, "_PLAN_GATE_TEST_CRASH": "early"})
            self.assertEqual(decision(p), "defer", msg=f"PLAN_GATE_BYPASS={v!r}")
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
        self.assertEqual(decision(p), "defer")
        (row,) = self.rows()
        self.assertEqual(row["class"], "emergency_bypass")

    def test_bad_json_with_gate_off_allows(self):
        p = self.gate("{nope", gate_on=False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decision(p), "defer")

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
        self.assertEqual(decision(p), "defer")
        (row,) = self.rows()
        self.assertEqual((row["decision"], row["class"]), ("defer", "emergency_bypass"))
        self.assertIn("fault path", row["reason"])

    def test_gate_off_still_allows_in_the_crash_path(self):
        p = self.gate(self.good, gate_on=False,
                      extra_env={"_PLAN_GATE_TEST_CRASH": "early"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(decision(p), "defer")

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
        self.assertEqual(decision(bash("git status")), "defer")
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

    @unittest.skipIf(os.name == "nt", "Windows resolves `..` lexically before following "
                     "symlinks (GetFullPathName), so lnk/../x.py is x.py there: no alias to test.")
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

    def test_force_is_always_denied_to_a_gated_agent(self):
        self._plan_allowing("python3", "py", "env", "bash", "echo", "cd")
        sl = SCOPE_LEASE.as_posix()
        for cmd in (
            "python3 %s release --force" % sl,
            "python3 %s release --force --paths x" % sl,
            "python3 %s release --forc" % sl,
            "python3 %s release --fo=1" % sl,
            "python3 -u %s release --force" % sl,
            "py -3 %s release --force" % sl,
            "env python3 %s release --force" % sl,
            "echo ok; python3 %s release --force" % sl,
            "cd /tmp && python3 %s release --force" % sl,
            "%s release --force" % sl,
            "python3 %s release $FLAGS" % sl,
            "python3 %s release `echo --force`" % sl,
            "python3 %s holder --force" % sl,
            "bash -c 'python3 %s release --force'" % sl,
        ):
            p = self.run_bash(cmd, "bob")
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertIn("--force", self.reason(p), cmd)
        for cmd in ("python %s release --force" % sl, "& python %s release --force" % sl,
                    "Get-ChildItem; python %s release --force" % sl):
            p = run_gate({"tool_name": "PowerShell", "tool_input": {"command": cmd}},
                         self.plans, cwd=self.repo, extra_env=self.env("bob"))
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)

    def test_force_is_denied_even_when_scope_lease_is_not_configured(self):
        self._plan_allowing("python3", "bash", "env")
        sl = SCOPE_LEASE.as_posix()
        for cmd in (
            "python3 %s release --force" % sl,
            "python3 %s release --forc" % sl,
            "env python3 %s release --force" % sl,
            "python3 %s release $FLAGS" % sl,
            "bash -c 'python3 %s release --force'" % sl,
        ):
            p = self.run_bash(cmd, "bob", PLAN_GATE_SCOPE_LEASE="", PLAN_GATE_REQUIRE_LEASE="")
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertIn("--force", self.reason(p), cmd)
        # Plain commands and the identity flags are NOT touched when unconfigured.
        for cmd in ("python3 %s release" % sl, "python3 %s acquire --owner alice" % sl):
            p = self.run_bash(cmd, "bob", PLAN_GATE_SCOPE_LEASE="", PLAN_GATE_REQUIRE_LEASE="")
            self.assertEqual(decision(p), "allow", cmd + " -> " + p.stdout)
        q = run_gate({"tool_name": "PowerShell",
                      "tool_input": {"command": "python %s release --force" % sl}},
                     self.plans, cwd=self.repo,
                     extra_env=self.env("bob", PLAN_GATE_SCOPE_LEASE=""))
        self.assertEqual(decision(q), "deny", q.stdout)

    def test_owner_and_abbreviations_on_release_and_acquire_are_denied(self):
        self._plan_allowing("python3")
        sl = SCOPE_LEASE.as_posix()
        for cmd in ("python3 %s release --owner alice" % sl,
                    "python3 %s release --own alice" % sl,
                    "python3 %s acquire --lock alice" % sl,
                    "python3 %s acquire --owner=alice" % sl):
            p = self.run_bash(cmd, "bob")
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)

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
            # read-only commands defer to the harness; plan-admitted ones allow
            expected = "defer" if cmd.startswith(("echo ", "git for-each-ref")) else "allow"
            self.assertEqual(decision(p), expected, cmd + " -> " + p.stdout)

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
        self.assertEqual(decision(p), "defer")

    def test_reads_are_never_lease_checked(self):
        self.lease("alice", self.leased)
        self.assertEqual(decision(self.run_bash("cat %s" % self.leased, "bob")), "defer")

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
        self.assertEqual(decision(p), "defer", p.stdout)

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
        if cmd.startswith("& "):  # PowerShell call operator, printed on Windows
            cmd = cmd[2:]
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

    # --- Q11: revoke retires a plan name ------------------------------------------------
    def revoke(self, *extra):
        return self.cli("revoke", str(self.plan), "--no-tty-check", *extra)

    def test_approve_revoke_then_old_bytes_back_is_not_active(self):
        old = self.plan.read_bytes()
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")
        r = self.revoke()
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.edit()
        self.assertEqual(decision(p), "deny", p.stdout)
        self.assertIn("not approved", self.reason(p))
        self.plan.write_text("---\nname: x\nstatus: archived\n---\n", encoding="utf-8")
        self.plan.write_bytes(old)  # the exact approved bytes written back
        self.assertEqual(decision(self.edit()), "deny")

    def test_reapprove_after_revoke_activates_again(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.revoke().returncode, 0)
        self.assertEqual(decision(self.edit()), "deny")
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(decision(self.edit()), "allow")

    def test_revoke_row_fields_and_verify_log_lists_it(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.revoke().returncode, 0)
        rows = [r for r in self.rows() if r["class"] == "revoke"]
        self.assertEqual(len(rows), 1)
        for key in ("ts", "plan", "os_user", "hostname", "git_user_name", "git_user_email",
                    "no_tty_check", "sha256", "prev", "h"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["plan"], "active.md")
        v = self.cli("verify-log")
        self.assertEqual(v.returncode, 0, v.stdout)
        self.assertIn("1 plan revocation(s) recorded", v.stdout)
        self.assertIn("REVOKED active.md", v.stdout)
        self.assertIn("superseded by line 2", v.stdout)  # the approval is replaced

    def test_revoke_works_after_the_file_is_gone(self):
        self.assertEqual(self.approve().returncode, 0)
        self.plan.unlink()
        r = self.revoke()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.plan.write_text(PLAN_TEMPLATE.format(allowed=str(self.allowed)), encoding="utf-8")
        self.assertEqual(decision(self.edit()), "deny")

    def test_revoke_refuses_without_a_terminal(self):
        self.assertEqual(self.approve().returncode, 0)
        r = self.cli("revoke", str(self.plan))
        self.assertEqual(r.returncode, 3)
        self.assertEqual(decision(self.edit()), "allow")  # nothing was recorded

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "openpty"), "needs a pty")
    def test_revoke_on_a_terminal_needs_yes(self):
        self.assertEqual(self.approve().returncode, 0)
        for answer, code in ((b"no\n", 1), (b"yes\n", 0)):
            master, slave = os.openpty()
            try:
                os.write(master, answer)
                r = self.cli("revoke", str(self.plan), stdin=slave)
            finally:
                os.close(master)
                os.close(slave)
            self.assertEqual(r.returncode, code, (answer, r.stdout, r.stderr))
        self.assertEqual(decision(self.edit()), "deny")

    def test_a_transplanted_revoke_row_is_not_honoured(self):
        other = Path(self._tmp.name) / "other-logs"
        self.assertEqual(self.cli("revoke", str(self.plan), "--no-tty-check", "--log-dir",
                                  str(other)).returncode, 0)
        stolen = (other / "plan-gate-decisions.log").read_bytes()
        self.assertEqual(self.approve().returncode, 0)
        with open(str(self.logs / "plan-gate-decisions.log"), "ab") as fh:
            fh.write(stolen)
        self.assertEqual(decision(self.edit()), "allow")  # the unlinked revoke did nothing
        v = self.cli("verify-log")
        self.assertIn("BROKEN LINK, not honoured", v.stdout)

    def test_an_unlinked_approve_after_a_revoke_cannot_unrevoke(self):
        other = Path(self._tmp.name) / "other-logs"
        self.assertEqual(self.cli("approve", str(self.plan), "--no-tty-check", "--log-dir",
                                  str(other)).returncode, 0)
        stolen = (other / "plan-gate-decisions.log").read_bytes()
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.revoke().returncode, 0)
        self.assertEqual(decision(self.edit()), "deny")
        with open(str(self.logs / "plan-gate-decisions.log"), "ab") as fh:
            fh.write(stolen)  # a self-consistent approve row with the wrong predecessor
        self.assertEqual(decision(self.edit()), "deny")

    def test_a_row_replayed_from_this_log_does_not_unrevoke(self):
        # Copy the original approve line to the end: its prev no longer matches.
        self.assertEqual(self.approve().returncode, 0)
        log = self.logs / "plan-gate-decisions.log"
        approve_line = log.read_bytes()
        self.assertEqual(self.revoke().returncode, 0)
        with open(str(log), "ab") as fh:
            fh.write(approve_line)
        self.assertEqual(decision(self.edit()), "deny")

    def test_gated_agent_cannot_run_revoke(self):
        plan = self.plan.read_text(encoding="utf-8").replace(
            "  - npm test", "  - npm test\n  - python3 \n  - bash \n  - env \n  - /opt")
        self.plan.write_text(plan, encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        gate = "/opt/strictlock/plan-gate/plan-gate.py"
        for cmd in (
            "%s revoke p.md" % gate,
            "python3 %s revoke p.md" % gate,
            "PYTHONUTF8=1 python %s revoke p.md --no-tty-check" % gate,
            "echo ok; python3 %s revoke p.md" % gate,
            "cd /opt && python3 plan-gate.py revoke p.md",
            "python3 $GATE revoke p.md",
            "bash -c 'python3 %s revoke p.md'" % gate,
            "env python3 %s revoke p.md" % gate,
        ):
            p = self.bash(cmd)
            self.assertEqual(decision(p), "deny", cmd + " -> " + p.stdout)
            self.assertEqual(self.rows()[-1]["class"], "approve_self_protect", cmd)
        for cmd in ("python C:\\tools\\plan-gate.py revoke p.md",
                    "& python C:\\tools\\plan-gate.py revoke p.md",
                    "Get-ChildItem; python plan-gate.py revoke p.md"):
            p = run_gate({"tool_name": "PowerShell", "tool_input": {"command": cmd}},
                         self.plans, cwd=self.work, log_dir=self.logs,
                         extra_env={"PLAN_GATE_REQUIRE_APPROVAL": ""})
            self.assertEqual(decision(p), "deny", cmd)

    def test_approve_command_with_spaces_gets_the_call_operator_only_on_windows(self):
        r = self.reason(self.edit())
        cmd = r.split("in a terminal: ", 1)[1]
        if os.name != "nt":
            self.assertFalse(cmd.startswith("& "))

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
            expected = "defer" if cmd.startswith("echo ") else "allow"
            self.assertEqual(decision(p), expected, cmd + " -> " + p.stdout)

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
        self.assertEqual(decision(p), "defer")

    def test_read_only_commands_and_always_writable_dirs_need_no_approval(self):
        self.assertEqual(decision(self.bash("git status")), "defer")
        p = self.edit(self.plans / "new-draft.md",
                      extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.plans)})
        self.assertEqual(decision(p), "defer")  # agents may keep drafting plans

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


class FileCommandLeaseTests(unittest.TestCase):
    """cp / mv / install / tee / sed -i / dd of= admitted through allowed_commands
    are looked up in the scope-lease like any other write; with
    PLAN_GATE_REQUIRE_LEASE a destination that cannot be determined denies."""

    PROGS = ("cp", "mv", "install", "tee", "sed", "dd", "env", "bash", "xargs", "cd",
             "gcp", "gmv", "ginstall", "gsed", "gtee", "gdd")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.root = root
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
        self.src = self.repo / "src.txt"
        self.logs = root / "logs"
        # `sed -i` only edits files that exist, so the destinations must
        for f in (self.leased, self.free):
            f.write_text("a\n")
        (self.plans / "active.md").write_text(
            "---\nname: fc\nstatus: active\nallowed_paths:\n  - %s\n  - %s\n"
            "allowed_commands:\n%s---\n" % (
                self.leased, self.free, "".join("  - %s\n" % c for c in self.PROGS)),
            encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def lease(self, owner, *paths):
        env = dict(os.environ, SCOPE_LEASE_OWNER=owner)
        args = [sys.executable, str(SCOPE_LEASE), "acquire", "--root", str(self.repo),
                "--source", "paths", "--lock-id", "lock-" + owner, "--paths"]
        args += [str(p) for p in paths]
        proc = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def run_bash(self, cmd, owner="bob", leases=True, **more):
        e = {"PLAN_GATE_SCOPE_LEASE": str(SCOPE_LEASE) if leases else "",
             "SCOPE_LEASE_OWNER": owner, "SCOPE_LEASE_LOCK_ID": "",
             "PLAN_GATE_REQUIRE_LEASE": ""}
        e.update(more)
        return run_gate({"tool_name": "Bash", "tool_input": {"command": cmd.replace("\\", "/")}},
                        self.plans, cwd=self.repo, extra_env=e)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def p(self, path):
        return Path(path).as_posix()

    def assertHeld(self, cmd, **kw):
        proc = self.run_bash(cmd, **kw)
        self.assertEqual(decision(proc), "deny", "%s -> %s" % (cmd, proc.stdout))
        self.assertIn("leased by another agent", self.reason(proc), cmd)
        self.assertIn("alice", self.reason(proc), cmd)

    def test_each_program_is_denied_when_its_destination_is_held_by_another_agent(self):
        self.lease("alice", self.leased)
        L, S = self.p(self.leased), self.p(self.src)
        for cmd in (
            "cp %s %s" % (S, L),
            "cp -f -- %s %s" % (S, L),
            "cp -rp %s %s" % (S, L),
            "cp --force --target-directory=%s %s" % (self.p(self.repo),
                                                    self.p(self.root / "x" / "leased.txt")),
            "cp -t %s %s" % (self.p(self.repo), self.p(self.root / "x" / "leased.txt")),
            "cp -vt%s %s" % (self.p(self.repo), self.p(self.root / "x" / "leased.txt")),
            "cp %s %s/" % (self.p(self.root / "x" / "leased.txt"), self.p(self.repo)),
            "cp %s %s %s" % (S, self.p(self.root / "x" / "leased.txt"), self.p(self.repo)),
            "mv %s %s" % (S, L),
            "mv %s %s" % (L, self.p(self.free)),            # the SOURCE of a mv is written
            "mv -T %s %s" % (S, L),
            "install -m 644 %s %s" % (S, L),
            "install -D %s %s" % (S, L),
            "install -d %s" % L,
            "tee %s" % L,
            "tee -a %s %s" % (self.p(self.free), L),
            "echo hi | tee -- %s" % L,
            "sed -i s/a/b/ %s" % L,         # bare -i: GNU and BSD readings differ
            "sed -i '' s/a/b/ %s" % L,      # macOS idiom
            "sed -i -e s/a/b/ %s" % L,
            "sed -I '' s/a/b/ %s" % L,      # BSD in-place with a suffix word
            "sed -I.bak s/a/b/ %s" % L,
            "sed -l -i '' s/a/b/ %s" % L,   # BSD -l has no value; GNU -l takes one
            "sed -i.bak s/a/b/ %s" % L,
            "sed --in-place=.bak -e s/a/b/ %s" % L,
            "sed -n -i -e p %s" % L,
            "sed -ni p %s" % L,
            "sed -E -i -f script.sed %s" % L,
            "dd if=/dev/zero of=%s bs=1 count=1" % L,
            "dd of=%s" % L,
            "env FOO=1 cp %s %s" % (S, L),
            "echo a && cp %s %s" % (S, L),
            "cp %s %s/sub/../leased.txt" % (S, self.p(self.repo)),
        ):
            self.assertHeld(cmd)

    def test_gnu_g_prefixed_names_are_the_same_programs(self):
        self.lease("alice", self.leased)
        L, S = self.p(self.leased), self.p(self.src)
        for cmd in ("gcp %s %s" % (S, L), "gmv %s %s" % (S, L), "ginstall %s %s" % (S, L),
                    "gtee %s" % L, "gsed -i s/a/b/ %s" % L, "gdd of=%s" % L,
                    "/opt/homebrew/bin/gcp %s %s" % (S, L)):
            self.assertHeld(cmd)

    def test_known_long_options_in_exact_form_still_resolve(self):
        self.lease("alice", self.leased)
        L, S = self.p(self.leased), self.p(self.src)
        self.assertHeld("cp --target-directory=%s %s" % (self.p(self.repo),
                                                         self.p(self.root / "x" / "leased.txt")))
        self.assertHeld("cp --preserve=mode --backup=numbered %s %s" % (S, L))
        self.assertHeld("sed --quiet --in-place=.bak --expression=s/a/b/ %s" % L)
        self.assertHeld("sed -i --regexp-extended -e s/a/b/ %s" % L)
        # routine GNU long options of cp / mv / install are known, so they resolve
        # (and are looked up) instead of being treated as undetermined
        for cmd in ("cp --no-preserve=mode --update %s %s" % (S, L),
                    "cp --update=none --dereference --no-dereference %s %s" % (S, L),
                    "mv --update --no-clobber --verbose %s %s" % (S, L),
                    "install --preserve-context --verbose --compare %s %s" % (S, L)):
            self.assertHeld(cmd)

    def test_the_same_commands_on_a_free_path_are_allowed_by_the_plan(self):
        self.lease("alice", self.leased)
        F, S = self.p(self.free), self.p(self.src)
        for cmd in ("cp %s %s" % (S, F), "mv %s %s" % (S, F), "tee %s" % F,
                    "sed -i s/a/b/ %s" % F, "install %s %s" % (S, F),
                    "dd if=/dev/zero of=%s" % F):
            proc = self.run_bash(cmd)
            self.assertEqual(decision(proc), "allow", "%s -> %s" % (cmd, proc.stdout))

    def test_a_lease_held_by_me_is_fine(self):
        self.lease("bob", self.leased)
        proc = self.run_bash("cp %s %s" % (self.p(self.src), self.p(self.leased)))
        self.assertEqual(decision(proc), "allow", proc.stdout)

    def test_non_writing_forms_are_not_lease_checked(self):
        self.lease("alice", self.leased)
        L = self.p(self.leased)
        for cmd in ("sed s/a/b/ %s" % L,                 # no -i: output to stdout
                    "sed -n p %s" % L,
                    "cp %s %s" % (L, self.p(self.free)),  # leased file only READ
                    "tee",                                # stdout only
                    "dd if=%s of=/dev/null" % L,
                    "dd if=%s" % L,
                    "tee /dev/null"):
            proc = self.run_bash(cmd)
            self.assertEqual(decision(proc), "allow", "%s -> %s" % (cmd, proc.stdout))

    def test_directory_destination_resolves_to_the_file_written_inside_it(self):
        self.lease("alice", self.leased)
        sub = self.repo / "sub"
        sub.mkdir()
        # `cp x repo` (an existing directory, no trailing slash) writes repo/x
        other = self.p(self.root / "x" / "leased.txt")
        self.assertHeld("cp %s %s" % (other, self.p(self.repo)))
        # a file destination that does not exist is the file itself
        proc = self.run_bash("cp %s %s" % (other, self.p(sub / "new.txt")))
        self.assertNotEqual(decision(proc), "deny", proc.stdout)

    def test_macos_sed_idiom_under_require_lease(self):
        L = self.p(self.leased)
        # I hold the lease: the macOS `sed -i '' s/a/b/ file` is allowed, not tripped by
        # the script word the GNU reading reports as a file
        self.lease("bob", self.leased)
        for cmd in ("sed -i '' s/a/b/ %s" % L, "sed -I '' s/a/b/ %s" % L,
                    "sed -i s/a/b/ %s" % L):
            proc = self.run_bash(cmd, PLAN_GATE_REQUIRE_LEASE="on")
            self.assertEqual(decision(proc), "allow", "%s -> %s" % (cmd, proc.stdout))

    def test_macos_sed_idiom_on_a_file_held_by_another_is_denied(self):
        self.lease("alice", self.leased)
        L = self.p(self.leased)
        for require in ("", "on"):
            self.assertHeld("sed -i '' s/a/b/ %s" % L, PLAN_GATE_REQUIRE_LEASE=require)

    def test_relative_sed_name_is_resolved_against_the_agents_directory_too(self):
        # The agent's shell sits in the repo (the payload `cwd`); the hook process
        # runs somewhere else that has no such file. The name must not be dropped.
        self.lease("alice", self.leased)
        elsewhere = self.root / "hookcwd"
        elsewhere.mkdir()
        e = {"PLAN_GATE_SCOPE_LEASE": str(SCOPE_LEASE), "SCOPE_LEASE_OWNER": "bob",
             "SCOPE_LEASE_LOCK_ID": "", "PLAN_GATE_REQUIRE_LEASE": "on"}
        def run(cmd, payload_cwd):
            payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
            if payload_cwd:
                payload["cwd"] = str(payload_cwd)
            return run_gate(payload, self.plans, cwd=elsewhere, extra_env=e)
        for cmd in ("sed -i '' s/a/b/ leased.txt", "sed -i s/a/b/ leased.txt",
                    "sed -i.bak s/a/b/ leased.txt"):
            proc = run(cmd, self.repo)
            self.assertEqual(decision(proc), "deny", "%s -> %s" % (cmd, proc.stdout))
            self.assertIn("alice", self.reason(proc), cmd)
        # my own lease: allowed from the same directory split
        self.lease("bob", self.free)
        proc = run("sed -i '' s/a/b/ free.txt", self.repo)
        self.assertEqual(decision(proc), "allow", proc.stdout)
        # a name that exists under NO base is still dropped (the script word)
        proc = run("sed -i '' s/a/b/ free.txt", None)   # no payload cwd, hook cwd lacks it
        self.assertEqual(decision(proc), "allow", proc.stdout)

    def test_sed_backup_copy_needs_its_own_lease_under_require(self):
        L = self.p(self.leased)
        self.lease("bob", self.leased)
        proc = self.run_bash("sed -i.bak s/a/b/ %s" % L, PLAN_GATE_REQUIRE_LEASE="on")
        self.assertEqual(decision(proc), "deny", proc.stdout)
        self.assertIn("leased.txt.bak", self.reason(proc))
        self.assertIn("acquire", self.reason(proc))
        # held by someone else: the backup path is checked too
        self.lease("alice", Path(L + ".bak"))
        self.assertHeld("sed -i.bak s/a/b/ %s" % L)
        # an empty suffix writes no backup
        proc = self.run_bash("sed -i '' s/a/b/ %s" % L, PLAN_GATE_REQUIRE_LEASE="on")
        self.assertEqual(decision(proc), "allow", proc.stdout)

    def test_without_a_lease_setup_nothing_changes(self):
        self.lease("alice", self.leased)
        proc = self.run_bash("cp %s %s" % (self.p(self.src), self.p(self.leased)), leases=False)
        self.assertEqual(decision(proc), "allow", proc.stdout)

    # --- strict mode ---

    def test_require_lease_denies_an_unleased_destination(self):
        for cmd in ("cp %s %s" % (self.p(self.src), self.p(self.free)),
                    "tee %s" % self.p(self.free),
                    "sed -i s/a/b/ %s" % self.p(self.free),
                    "sed -I '' s/a/b/ %s" % self.p(self.free),
                    "dd of=%s" % self.p(self.free)):
            proc = self.run_bash(cmd, PLAN_GATE_REQUIRE_LEASE="on")
            self.assertEqual(decision(proc), "deny", cmd)
            self.assertIn("acquire", self.reason(proc), cmd)
        self.lease("bob", self.free)
        proc = self.run_bash("cp %s %s" % (self.p(self.src), self.p(self.free)),
                             PLAN_GATE_REQUIRE_LEASE="on")
        self.assertEqual(decision(proc), "allow", proc.stdout)

    def test_require_lease_denies_when_the_destination_cannot_be_determined(self):
        F = self.p(self.free)
        cmds = (
            "cp %s $DEST" % self.p(self.src),
            "cp %s/*.txt %s/" % (self.p(self.repo), F),
            "cp %s %s/{a,b}" % (self.p(self.src), self.p(self.repo)),
            "tee $F",
            "tee %s/*" % self.p(self.repo),
            "sed -i s/a/b/ %s/*.py" % self.p(self.repo),
            "sed -i --expr=s/a/b/ %s" % F,           # GNU abbreviation of --expression
            "sed -i --in-pl=.bak s/a/b/ %s" % F,
            "sed -i --frobnicate s/a/b/ %s" % F,
            "sed -i -Q s/a/b/ %s" % F,
            "gsed -i --fil=x.sed %s" % F,
            "cp --target=%s a b" % self.p(self.repo),   # GNU abbreviation of --target-directory
            "cp --frobnicate=x %s %s" % (self.p(self.src), F),
            "gcp --targ=%s a" % self.p(self.repo),
            "dd of=$X",
            "mv %s $D" % F,
            "cp --frobnicate %s %s" % (self.p(self.src), F),
            "cp %s" % F,
            "cp -t",
            "cd sub && cp a b",
            "cp . %s/" % self.p(self.repo),
            "bash -c 'cp a b'",
            "xargs cp -t dest",
        )
        for cmd in cmds:
            proc = self.run_bash(cmd, PLAN_GATE_REQUIRE_LEASE="on")
            self.assertEqual(decision(proc), "deny", "%s -> %s" % (cmd, proc.stdout))
            self.assertIn("cannot be determined", self.reason(proc), cmd)
            self.assertIn("PLAN_GATE_BYPASS", self.reason(proc), cmd)

    def test_undetermined_destination_without_require_is_not_blocked_by_the_lease(self):
        # leases configured, not required: only a destination that IS found and held denies
        for cmd in ("cp %s $DEST" % self.p(self.src), "tee $F", "dd of=$X"):
            proc = self.run_bash(cmd)
            self.assertEqual(decision(proc), "allow", "%s -> %s" % (cmd, proc.stdout))

    def test_a_destination_under_the_log_dir_is_denied_even_without_leases(self):
        proc = self.run_bash("cp %s %s/plan-gate-decisions.log" % (self.p(self.src), self.p(self.logs)),
                             leases=False, PLAN_GATE_LOG_DIR=str(self.logs),
                             PLAN_GATE_REQUIRE_APPROVAL="off")
        self.assertEqual(decision(proc), "deny")
        self.assertIn("PLAN_GATE_LOG_DIR", self.reason(proc))
        self.logs.mkdir(exist_ok=True)
        (self.logs / "x").write_text("x")   # sed -i only edits what exists
        for cmd in ("tee %s/x" % self.p(self.logs), "dd of=%s/x" % self.p(self.logs),
                    "sed -i s/a/b/ %s/x" % self.p(self.logs)):
            proc = self.run_bash(cmd, leases=False, PLAN_GATE_LOG_DIR=str(self.logs),
                                 PLAN_GATE_REQUIRE_APPROVAL="off")
            self.assertEqual(decision(proc), "deny", cmd)

    def test_commands_not_admitted_by_the_plan_are_still_denied(self):
        proc = self.run_bash("rm %s" % self.p(self.free))
        self.assertEqual(decision(proc), "deny")
        self.assertNotIn("leased", self.reason(proc))

    def test_powershell_sed_destination_is_lease_checked(self):
        self.lease("alice", self.leased)
        e = {"PLAN_GATE_SCOPE_LEASE": str(SCOPE_LEASE), "SCOPE_LEASE_OWNER": "bob",
             "SCOPE_LEASE_LOCK_ID": "", "PLAN_GATE_REQUIRE_LEASE": ""}
        proc = run_gate({"tool_name": "PowerShell",
                         "tool_input": {"command": "sed -i s/a/b/ '%s'" % self.leased}},
                        self.plans, cwd=self.repo, extra_env=e)
        self.assertEqual(decision(proc), "deny", proc.stdout)
        self.assertIn("alice", self.reason(proc))


def _load_gate_module():
    """The gate as a module, for tests of its pure helpers (parsers, probes)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("plan_gate_under_test", str(GATE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CaseProbeTests(unittest.TestCase):
    """_name_folds decides case-insensitivity by inode identity. A case-sensitive
    volume is not available on every host (this suite's author ran on macOS), so
    the filesystem is simulated by patching os.lstat / os.listdir / os.path.lexists
    inside the gate module's `os`; the real-FS check at the end runs everywhere."""

    @classmethod
    def setUpClass(cls):
        cls.g = _load_gate_module()

    class _Stat(object):
        def __init__(self, ino, dev=1):
            self.st_dev, self.st_ino = dev, ino

    def fake_fs(self, entries, insensitive):
        """entries: {dirpath: {name: ino}}. insensitive: lookups fold case."""
        _Stat = self._Stat
        def lookup(path):
            d, n = os.path.split(path)
            ents = entries.get(d, {})
            if n in ents:
                return ents[n]
            if insensitive:
                for k, v in ents.items():
                    if k.lower() == n.lower():
                        return v
            return None
        def lstat(path):
            ino = lookup(path)
            if ino is None:
                raise FileNotFoundError(path)
            return _Stat(ino)
        def listdir(path):
            return list(entries.get(path, {}))
        return lstat, listdir

    def folds(self, entries, insensitive, parent, name):
        from unittest import mock
        lstat, listdir = self.fake_fs(entries, insensitive)
        self.g._FOLD_CACHE.clear()
        try:
            with mock.patch.object(self.g.os, "lstat", lstat), \
                    mock.patch.object(self.g.os, "listdir", listdir):
                return self.g._name_folds(parent, name)
        finally:
            self.g._FOLD_CACHE.clear()

    def test_case_insensitive_volume_folds(self):
        ents = {"/m": {"Plans": 7}}
        self.assertIs(self.folds(ents, True, "/m", "Plans"), True)
        self.assertIs(self.folds(ents, True, "/m", "PLANS"), True)  # not-stored spelling

    def test_case_sensitive_volume_does_not_fold(self):
        ents = {"/m": {"Plans": 7}}
        self.assertIs(self.folds(ents, False, "/m", "Plans"), False)

    def test_a_case_swapped_symlink_does_not_make_a_sensitive_volume_look_insensitive(self):
        # real dir PLANS/evil.txt (ino 5) and a symlink EVIL.TXT (its own inode, 6)
        ents = {"/m/PLANS": {"evil.txt": 5, "EVIL.TXT": 6}}
        self.assertIs(self.folds(ents, False, "/m/PLANS", "evil.txt"), False)
        self.assertIs(self.folds(ents, False, "/m/PLANS", "EVIL.TXT"), False)

    def test_two_hardlinked_spellings_on_a_sensitive_volume_do_not_fold(self):
        ents = {"/m": {"a.txt": 9, "A.TXT": 9}}
        self.assertIs(self.folds(ents, False, "/m", "a.txt"), False)
        self.assertIs(self.folds(ents, False, "/m", "A.TXT"), False)

    def test_a_single_cased_letter_has_no_third_variant_so_it_never_folds(self):
        self.assertIs(self.folds({"/m": {"a": 3}}, True, "/m", "a"), False)

    def test_a_large_directory_is_never_listed(self):
        from unittest import mock
        lstat, _ = self.fake_fs({"/m": {"Plans": 7}}, True)
        def boom(path):
            raise AssertionError("os.listdir must not be called by the probe")
        self.g._FOLD_CACHE.clear()
        with mock.patch.object(self.g.os, "lstat", lstat), \
                mock.patch.object(self.g.os, "listdir", boom):
            self.assertIs(self.g._name_folds("/m", "Plans"), True)
        self.g._FOLD_CACHE.clear()

    def test_names_without_letters_and_errors_are_inconclusive(self):
        self.assertIsNone(self.folds({"/m": {"123": 1}}, True, "/m", "123"))
        self.assertIsNone(self.folds({"/m": {}}, True, "/m", "gone"))

    def keys_on(self, tree, insens_dirs, *paths):
        """_real_key for `paths` on a simulated tree {path: (ino, dev)}; directories in
        insens_dirs fold case on lookup."""
        from unittest import mock
        _Stat = self._Stat

        def lookup(path):
            if path in tree:
                return tree[path]
            d = os.path.dirname(path)
            if d in insens_dirs:
                for k, v in tree.items():
                    if os.path.dirname(k) == d and os.path.basename(k).lower() == os.path.basename(path).lower():
                        return v
            return None

        def lstat(path):
            if path == "/":
                return _Stat(1, 1)
            v = lookup(path)
            if v is None:
                raise FileNotFoundError(path)
            return _Stat(*v)

        self.g._FOLD_CACHE.clear()
        try:
            with mock.patch.object(self.g.os, "lstat", lstat), \
                    mock.patch.object(self.g.os.path, "realpath", lambda p: p):
                return [self.g._real_key(p) for p in paths]
        finally:
            self.g._FOLD_CACHE.clear()

    @unittest.skipIf(os.name == "nt", "mount-boundary case logic is POSIX-only; Windows folds all")
    def test_a_new_name_directly_below_a_mount_point_does_not_fold(self):
        # "/" (dev 1) is case-insensitive, "/m" is a case-SENSITIVE volume mounted on it
        tree = {"/vol": (2, 2), "/vol/plans2": (3, 2)}
        a, b, c, d = self.keys_on(tree, {"/"}, "/vol/NEW.txt", "/vol/new.txt",
                                  "/vol/PLANS2/new.txt", "/vol/plans2/new.txt")
        self.assertNotEqual(a, b)   # was equal: the parent's answer leaked across the mount
        self.assertNotEqual(c, d)
        # the mount point's own name is spelled in the insensitive parent, so it folds
        e, f = self.keys_on(tree, {"/"}, "/VOL/plans2/x", "/vol/plans2/x")
        self.assertEqual(e, f)

    @unittest.skipIf(os.name == "nt", "mount-boundary case logic is POSIX-only; Windows folds all")
    def test_below_a_probed_component_inside_the_mount_folding_follows_that_volume(self):
        # inside the sensitive volume: "Sub" exists and is not foldable -> stays exact
        tree = {"/vol": (2, 2), "/vol/Sub": (3, 2), "/vol/sub": (4, 2)}
        a, b = self.keys_on(tree, {"/"}, "/vol/Sub/New", "/vol/sub/New")
        self.assertNotEqual(a, b)
        # same device, insensitive all the way down: a new name still folds
        tree2 = {"/vol": (2, 1), "/vol/Sub": (3, 1)}
        a, b = self.keys_on(tree2, {"/", "/vol"}, "/vol/SUB/New.txt", "/vol/sub/new.txt")
        self.assertEqual(a, b)

    def test_real_filesystem_matches_an_independent_probe(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.path.realpath(d)
            (Path(real) / "CaseProbe").mkdir()
            expected = (Path(real) / "caseprobe").exists()
            self.assertIs(self.g._name_folds(real, "CaseProbe"), expected)
            # a file system that folds case: keys for two spellings agree; else they differ
            ka = self.g._real_key(os.path.join(real, "CaseProbe", "New.txt"))
            kb = self.g._real_key(os.path.join(real, "caseprobe", "new.txt"))
            self.assertEqual(ka == kb, expected)


class DotDotAfterLinkTests(unittest.TestCase):
    """`..` after a link is ambiguous between Win32 (lexical) and POSIX/MSYS (through
    the link) resolution. _real_key treats it as never matching on Windows only."""

    @classmethod
    def setUpClass(cls):
        cls.g = _load_gate_module()

    def test_detects_dotdot_after_a_link_and_only_then(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            os.mkdir(os.path.join(d, "a"))
            os.mkdir(os.path.join(d, "a", "sub"))
            os.mkdir(os.path.join(d, "out"))
            _symlink(os.path.join(d, "a", "lnk"), os.path.join(d, "out"))
            j = lambda *p: os.sep.join((d,) + p)
            self.assertTrue(self.g._dotdot_after_link(j("a", "lnk", "..", "f.txt")))
            self.assertTrue(self.g._dotdot_after_link(j("a", "lnk", "sub", "..", "..", "f")))
            self.assertFalse(self.g._dotdot_after_link(j("a", "sub", "..", "f.txt")))
            self.assertFalse(self.g._dotdot_after_link(j("a", "lnk", "f.txt")))  # no ..
            self.assertFalse(self.g._dotdot_after_link(j("a", "..", "a", "lnk")))  # .. BEFORE the link
            self.assertFalse(self.g._dotdot_after_link(j("a", "missing", "..", "f")))

    @unittest.skipUnless(os.name == "nt", "the ambiguous-.. rule applies on Windows only")
    def test_real_key_refuses_such_a_path_on_windows(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            os.mkdir(os.path.join(d, "a"))
            os.mkdir(os.path.join(d, "out"))
            _symlink(os.path.join(d, "a", "lnk"), os.path.join(d, "out"))
            self.assertIsNone(self.g._real_key(os.path.join(d, "a", "lnk", "..", "f.txt")))
            self.assertIsNotNone(self.g._real_key(os.path.join(d, "a", "f.txt")))


class FileCommandParserTests(unittest.TestCase):
    """The pure option parsers behind FileCommandLeaseTests."""

    @classmethod
    def setUpClass(cls):
        cls.g = _load_gate_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._tmp.name)
        self.f = os.path.join(self.d, "f.txt")
        with open(self.f, "w") as fh:
            fh.write("a\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_sed_drops_words_that_are_not_existing_non_directory_files(self):
        sub = os.path.join(self.d, "sub")
        os.mkdir(sub)
        # the macOS idiom: GNU reads `s/a/b/` (and '') as files; they do not exist
        targets, why = self.g._fc_sed(["-i", "", "s/a/b/", self.f])
        self.assertIsNone(why)
        self.assertEqual(targets, [self.f])
        # a directory is not editable in place; a missing file cannot be created
        self.assertEqual(self.g._fc_sed(["-i", "", "s/a/b/", sub]), ([], None))
        self.assertEqual(self.g._fc_sed(["-i", "", "s/a/b/", os.path.join(self.d, "nope")]),
                         ([], None))

    def test_sed_keeps_symlinks_whatever_they_point_at(self):
        link_file = os.path.join(self.d, "lnk")
        link_dangling = os.path.join(self.d, "dangling")
        link_dir = os.path.join(self.d, "dlnk")
        try:
            os.symlink(self.f, link_file)
            os.symlink(os.path.join(self.d, "missing"), link_dangling)
            os.symlink(self.d, link_dir)
        except (OSError, NotImplementedError, AttributeError) as exc:
            raise unittest.SkipTest("cannot create symlinks here: %s" % exc)
        for link in (link_file, link_dangling, link_dir):
            targets, why = self.g._fc_sed(["-i", "", "s/a/b/", link])
            self.assertIsNone(why)
            self.assertEqual(targets, [link], link)

    def test_sed_relative_names_resolve_against_the_cwd_unless_a_cd_intervenes(self):
        cwd = os.getcwd()
        try:
            os.chdir(self.d)
            # reported resolved, as the absolute path where it exists
            self.assertEqual(self.g._fc_sed(["-i", "", "s/a/b/", "f.txt"]),
                             ([os.path.join(os.getcwd(), "f.txt")], None))
            self.assertEqual(self.g._fc_sed(["-i", "", "s/a/b/", "nope.txt"]), ([], None))
            # after a `cd` a relative name cannot be resolved: kept (the caller then
            # reports it as undetermined, as before)
            targets, _ = self.g._fc_sed(["-i", "", "s/a/b/", "nope.txt"], has_cd=True)
            self.assertIn("nope.txt", targets)
        finally:
            os.chdir(cwd)

    def test_sed_backup_copies_are_reported_and_exempt_from_the_existence_filter(self):
        f = self.f
        def targets(*args):
            t, why = self.g._fc_sed(list(args))
            self.assertIsNone(why, args)
            return t
        self.assertIn(f + ".bak", targets("-i.bak", "s/a/b/", f))
        self.assertIn(f + ".bak", targets("--in-place=.bak", "-e", "s/a/b/", f))
        self.assertIn(f + ".orig", targets("-I", ".orig", "s/a/b/", f))     # BSD forms
        self.assertIn(f + ".orig", targets("-i", ".orig", "s/a/b/", f))
        self.assertIn(f + "-e", targets("-i", "-e", "s/a/b/", f))           # BSD: suffix "-e"
        self.assertEqual(targets("-i", "", "s/a/b/", f), [f])                 # empty = no backup
        self.assertEqual(targets("-i", "-e", "s/a/b/", os.path.join(self.d, "nope")), [])
        for pattern in ("bak/*", "$X"):
            t, why = self.g._fc_sed(["-i" + pattern, "s/a/b/", f])
            self.assertTrue(why, pattern)

    def test_sed_is_read_both_ways_and_the_union_is_reported(self):
        f = self.f
        # bare -i: GNU reads files [f]; BSD takes the next word as its suffix
        for args in (["-i", "s/a/b/", f], ["-i", "", "s/a/b/", f], ["-i", ".bak", "s/a/b/", f],
                     ["-i.bak", "s/a/b/", f], ["-I", "", "s/a/b/", f], ["-I.bak", "s/a/b/", f],
                     ["-l", "-i", "", "s/a/b/", f],      # BSD -l has no value; GNU -l takes N
                     ["-n", "-l", "5", "-i", "-e", "p", f], ["-ni", "p", f],
                     ["--in-place=.bak", "-e", "s/a/b/", f], ["-E", "-i", "-e", "s/a/b/", f]):
            targets, why = self.g._fc_sed(args)
            self.assertIsNone(why, args)
            self.assertIn(f, targets, args)

    def test_sed_without_in_place_writes_nothing(self):
        for args in (["s/a/b/", "/abs/f"], ["-n", "p", "/abs/f"], ["-l", "5", "-e", "p", "/abs/f"]):
            self.assertEqual(self.g._fc_sed(args), ([], None), args)

    def test_sed_union_never_misses_the_file_either_reading_edits(self):
        # GNU: files [f]; BSD: suffix "s/a/b/", script f, no files. f must be reported.
        targets, why = self.g._fc_sed(["-i", "s/a/b/", self.f])
        self.assertIsNone(why)
        self.assertEqual(targets, [self.f])

    def test_sed_unknown_options_are_undetermined_even_without_dash_i(self):
        for args in (["-i", "--expr=s/a/b/", "/abs/f"], ["--in-pl", "s/a/b/", "/abs/f"],
                     ["--fil=x", "/abs/f"], ["-i", "-Q", "s/a/b/", "/abs/f"]):
            targets, why = self.g._fc_sed(args)
            self.assertEqual(targets, [], args)
            self.assertTrue(why, args)

    def test_cp_unknown_long_options_are_undetermined(self):
        for prog in ("cp", "mv", "install"):
            for opt in ("--target=/victim/dir", "--frob=1", "--frob", "--target-dir=/v"):
                targets, why = self.g._fc_cp_like(prog, [opt, "a", "b"], False)
                self.assertTrue(why, (prog, opt))

    def test_install_bsd_value_options_do_not_shift_the_destination(self):
        self.assertEqual(self.g._fc_cp_like("install", ["-B", ".orig", "-f", "uchg", "/s", "/d"], False),
                         (["/d"], None))
        self.assertEqual(self.g._fc_cp_like("install", ["-B.orig", "-fuchg", "/s", "/d"], False),
                         (["/d"], None))


class ApprovalClassTests(unittest.TestCase):
    """An explicit `permissionDecision: "allow"` skips Claude Code's own permission
    prompt, so the gate emits it only when the active plan was checked and approved
    the call (allowed_paths / allowed_commands). Gate off, bypass, read-only and
    always-writable cases emit NO decision (exit 0, empty stdout) and the harness
    decides."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.aw = self.root / "aw"
        self.aw.mkdir()
        self.logs = self.root / "logs"
        self.f = self.work / "f.txt"
        write_active_plan(self.plans, str(self.f))
        self.env = {"PLAN_GATE_ALWAYS_WRITABLE": str(self.aw),
                    "PLAN_GATE_MCP_WRITE_TOOLS": "mcp__fs__w:path"}

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, tool, tool_input, **kw):
        kw.setdefault("cwd", self.work)
        env = dict(self.env)
        env.update(kw.pop("extra_env", {}))
        return run_gate({"tool_name": tool, "tool_input": tool_input}, self.plans,
                        extra_env=env, **kw)

    def assertDeferred(self, proc, msg=None):
        self.assertEqual((proc.returncode, proc.stdout), (0, ""), msg)

    def assertExplicitAllow(self, proc, msg=None):
        self.assertEqual(decision(proc), "allow", msg)

    def test_plan_approved_calls_emit_an_explicit_allow(self):
        self.assertExplicitAllow(self.call("Edit", {"file_path": str(self.f)}), "Edit")
        self.assertExplicitAllow(self.call("Write", {"file_path": str(self.f)}), "Write")
        self.assertExplicitAllow(self.call("Bash", {"command": "npm test"}), "allowed command")
        self.assertExplicitAllow(
            self.call("Bash", {"command": "npm test > %s" % self.f.as_posix()}),
            "allowed command + allowed redirect")
        self.assertExplicitAllow(
            self.call("mcp__fs__w", {"path": str(self.f)}), "MCP target in allowed_paths")

    def test_read_only_and_navigation_defer(self):
        for cmd in ("git status", "ls -la", "cd sub", "git status && pwd", "cat a | head"):
            self.assertDeferred(self.call("Bash", {"command": cmd}), cmd)
        self.assertDeferred(self.call("PowerShell", {"command": "Get-ChildItem"}), "PS")

    def test_always_writable_writes_defer(self):
        self.assertDeferred(self.call("Write", {"file_path": str(self.aw / "n.md")}), "Write")
        self.assertDeferred(self.call("Edit", {"file_path": str(self.aw / "n.md")}), "Edit")
        self.assertDeferred(
            self.call("Bash", {"command": "echo hi > %s" % (self.aw / "n.md").as_posix()}),
            "redirect-only")
        self.assertDeferred(
            self.call("mcp__fs__w", {"path": str(self.aw / "n.md")}), "MCP")

    def test_gate_off_defers_for_every_tool(self):
        for tool, ti in (("Edit", {"file_path": str(self.work / "x")}),
                         ("Bash", {"command": "rm -rf x"}),
                         ("PowerShell", {"command": "Remove-Item x"}),
                         ("Read", {"file_path": "x"}),
                         ("mcp__fs__w", {"path": "x"})):
            self.assertDeferred(self.call(tool, ti, gate_on=False), tool)

    def test_bypass_defers_for_every_tool_and_is_logged(self):
        for tool, ti in (("Edit", {"file_path": str(self.work / "x")}),
                         ("Bash", {"command": "rm -rf x"}),
                         ("PowerShell", {"command": "Remove-Item x"}),
                         ("mcp__fs__w", {"path": "x"})):
            self.assertDeferred(
                self.call(tool, ti, extra_env={"PLAN_GATE_BYPASS": "on"}, log_dir=self.logs), tool)
        rows = [json.loads(x) for x in
                (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["class"] for r in rows}, {"emergency_bypass"})
        self.assertEqual(len(rows), 4)

    def test_tools_outside_the_gated_set_defer(self):
        for tool in ("Read", "Grep", "WebFetch", "Task"):
            self.assertDeferred(self.call(tool, {"x": 1}), tool)

    def test_deferred_calls_get_a_log_row_that_says_defer(self):
        self.call("Bash", {"command": "git status"}, log_dir=self.logs)
        self.call("Write", {"file_path": str(self.aw / "n.md")}, log_dir=self.logs)
        self.call("Bash", {"command": "npm test"}, log_dir=self.logs)
        self.call("Bash", {"command": "rm -rf x"}, log_dir=self.logs)
        rows = [json.loads(x) for x in
                (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["decision"], r["class"]) for r in rows],
                         [("defer", "read_only_bypass"), ("defer", "always_writable"),
                          ("allow", "allowed_command"), ("deny", "denied")])
        # the chain still verifies with the new decision value
        proc = subprocess.run([sys.executable, str(GATE), "verify-log", str(self.logs / "plan-gate-decisions.log")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_deny_only_log_mode_skips_defer_rows_but_keeps_bypass(self):
        env = {"PLAN_GATE_LOG_DECISIONS": "deny"}
        self.call("Bash", {"command": "git status"}, log_dir=self.logs, extra_env=env)
        self.call("Write", {"file_path": str(self.aw / "n.md")}, log_dir=self.logs, extra_env=env)
        self.assertFalse((self.logs / "plan-gate-decisions.log").exists())
        self.call("Bash", {"command": "x"}, log_dir=self.logs,
                  extra_env=dict(env, PLAN_GATE_BYPASS="on"))
        rows = [json.loads(x) for x in
                (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["class"] for r in rows], ["emergency_bypass"])

    def test_denies_are_unaffected(self):
        p = self.call("Edit", {"file_path": str(self.work / "other.txt")})
        self.assertEqual(decision(p), "deny")
        self.assertEqual(decision(self.call("Bash", {"command": "rm -rf x"})), "deny")


class WorktreeBypassValueTests(unittest.TestCase):
    """`worktree_bypass:` is honored only as the unquoted, lowercase literal
    `true`. Any other value is REFUSED (a denial naming the field), never guessed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self.target = self.root / "elsewhere" / "app.py"
        self.logs = self.root / "logs"

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, value_line, status="active"):
        (self.plans / "active.md").write_text(
            "---\nname: x\nstatus: %s\n%s\nallowed_paths:\n  - %s\n---\n"
            % (status, value_line, self.target), encoding="utf-8")

    def edit(self, path=None, **kw):
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": "Edit", "tool_input": {"file_path": str(path or self.target)}},
                        self.plans, **kw)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def test_literal_true_is_honored(self):
        for line in ("worktree_bypass: true", "worktree_bypass: true   # cross-tree",
                     "worktree_bypass:true"):
            self.plan(line)
            self.assertEqual(decision(self.edit()), "allow", msg=line)

    def test_every_other_value_is_refused_with_a_clear_message(self):
        for value in ("yes", "True", "TRUE", "1", "on", "False", "FALSE", "no", "y", "t",
                      '"true"', "'true'", '"True"', '"false"', "'false'", "tru",
                      "true false", "[]", "0", "off"):
            self.plan("worktree_bypass: %s" % value)
            p = self.edit(log_dir=self.logs)
            self.assertEqual(decision(p), "deny", msg=value)
            r = self.reason(p)
            self.assertIn("worktree_bypass", r, msg=value)
            self.assertIn("`true`", r, msg=value)
            self.assertIn("Set it to `true` or `false`, or delete the line", r, msg=value)
        rows = [json.loads(x) for x in
                (self.logs / "plan-gate-decisions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["class"] for r in rows}, {"worktree_bypass_invalid"})

    def test_literal_false_is_accepted_and_means_off(self):
        # (maintainer ruling 2026-10-27) same as the field being absent
        for line in ("worktree_bypass: false", "worktree_bypass: false   # not cross-tree"):
            self.plan(line)
            self.assertEqual(decision(self.edit()), "allow", msg=line)
            self.assertEqual(decision(self.edit(self.work / "other.txt")), "deny")
        # inside a worktree it does NOT claim every tree like `true` does
        main = self.root / "main"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = self.root / "wt"
        wt.mkdir()
        (wt / ".git").write_text(
            "gitdir: %s\n" % (main / ".git" / "worktrees" / "wt"), encoding="utf-8")
        p = self.edit(cwd=wt)
        self.assertEqual(decision(p), "deny")
        self.assertIn("none claims this worktree", self.reason(p))
        self.assertNotIn("refuses", self.reason(p))

    def test_empty_value_is_refused(self):
        self.plan("worktree_bypass:")
        p = self.edit()
        self.assertEqual(decision(p), "deny")
        self.assertIn("(empty)", self.reason(p))

    def test_refused_value_is_refused_in_a_worktree_session_too(self):
        main = self.root / "main"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = self.root / "wt"
        wt.mkdir()
        (wt / ".git").write_text(
            "gitdir: %s\n" % (main / ".git" / "worktrees" / "wt"), encoding="utf-8")
        self.plan("worktree_bypass: yes")
        p = self.edit(cwd=wt)
        self.assertEqual(decision(p), "deny")
        self.assertIn("worktree_bypass", self.reason(p))

    def test_non_active_plan_with_a_bad_value_does_not_block(self):
        self.plan("worktree_bypass: yes", status="archived")
        # no active plan -> the ordinary "no active plan" denial, not the bypass one
        p = self.edit()
        self.assertEqual(decision(p), "deny")
        self.assertNotIn("worktree_bypass", self.reason(p).replace("worktree_bypass: true", ""))

    def test_plan_without_the_field_is_unaffected(self):
        (self.plans / "active.md").write_text(
            "---\nname: x\nstatus: active\nallowed_paths:\n  - %s\n---\n" % self.target,
            encoding="utf-8")
        self.assertEqual(decision(self.edit()), "allow")

    # --- scope: only a plan that would count for THIS session can block it ---

    def two_worktrees(self):
        main = self.root / "main"
        wts = {}
        for name in ("a", "b"):
            (main / ".git" / "worktrees" / name).mkdir(parents=True)
            wt = self.root / ("wt_" + name)
            wt.mkdir()
            (wt / ".git").write_text(
                "gitdir: %s\n" % (main / ".git" / "worktrees" / name), encoding="utf-8")
            wts[name] = wt
        return wts

    def write_plan(self, name, wt, extra=""):
        (self.plans / name).write_text(
            "---\nname: %s\nstatus: active\n%sallowed_paths:\n  - %s\n---\n"
            % (name, extra, wt / "f.txt"), encoding="utf-8")

    def test_another_worktrees_bad_bypass_plan_does_not_block_this_one(self):
        wts = self.two_worktrees()
        self.write_plan("a.md", wts["a"])
        self.write_plan("b.md", wts["b"], "worktree_bypass: yes\n")
        p = self.edit(wts["a"] / "f.txt", cwd=wts["a"])
        self.assertEqual(decision(p), "allow", p.stdout)
        # ...while the session that plan DOES belong to is told.
        (self.plans / "a.md").unlink()
        p = self.edit(wts["b"] / "f.txt", cwd=wts["b"])
        self.assertEqual(decision(p), "deny")
        self.assertIn("worktree_bypass", self.reason(p))

    def test_an_unapproved_bad_bypass_plan_does_not_block_anyone(self):
        wts = self.two_worktrees()
        logs = self.root / "logs"
        env = dict(os.environ, PLAN_GATE_LOG_DIR=str(logs), PLAN_GATE_REQUIRE_APPROVAL="",
                   _PLAN_GATE_TEST_PLANS_DIR=str(self.plans))
        def approve(name):
            return subprocess.run(
                [sys.executable, str(GATE), "approve", str(self.plans / name), "--no-tty-check"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, cwd=str(wts["a"]))
        self.write_plan("a.md", wts["a"])
        # b.md claims worktree A by path but nobody approved it
        self.write_plan("b.md", wts["a"] / "sub", "worktree_bypass: yes\n")
        self.assertEqual(approve("a.md").returncode, 0)
        envx = {"PLAN_GATE_REQUIRE_APPROVAL": ""}
        p = self.edit(wts["a"] / "f.txt", cwd=wts["a"], log_dir=logs, extra_env=envx)
        self.assertEqual(decision(p), "allow", p.stdout)
        # once approved it is in play, and it is refused
        self.assertEqual(approve("b.md").returncode, 0)
        p = self.edit(wts["a"] / "f.txt", cwd=wts["a"], log_dir=logs, extra_env=envx)
        self.assertEqual(decision(p), "deny")
        self.assertIn("worktree_bypass", self.reason(p))

    def test_plan_text_cannot_forge_the_quoted_marker(self):
        # a line named like the parser's own bookkeeping changes nothing
        self.plan('worktree_bypass: "true"\n_worktree_bypass_quoted:')
        self.assertEqual(decision(self.edit()), "deny")
        self.plan("worktree_bypass: true\n_worktree_bypass_quoted: yes")
        self.assertEqual(decision(self.edit()), "allow")

    def test_the_plan_can_still_be_fixed_through_an_always_writable_plans_dir(self):
        self.plan("worktree_bypass: yes")
        p = self.edit(self.plans / "active.md",
                      extra_env={"PLAN_GATE_ALWAYS_WRITABLE": str(self.plans)})
        self.assertEqual(decision(p), AW_OK)


AW_OK = "defer"  # what a write into an always-writable dir decides (no opinion)


def _symlink(link, target):
    """Create a symlink or skip the test (Windows without the privilege)."""
    try:
        os.symlink(str(target), str(link), target_is_directory=Path(target).is_dir())
    except (OSError, NotImplementedError, AttributeError) as exc:
        raise unittest.SkipTest("cannot create symlinks here: %s" % exc)


def _fs_is_case_insensitive(directory):
    """Independent probe (the gate has its own): does `directory`'s FS fold case?"""
    probe = Path(directory) / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return (Path(directory) / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


class PathResolutionTests(unittest.TestCase):
    """allowed_paths and always-writable grants compare RESOLVED paths: a symlink
    (with or without `..`) cannot carry a write out of what the plan or the
    always-writable dir covers; case is folded only on a case-insensitive FS."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def edit(self, path, tool="Edit", **kw):
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": tool, "tool_input": {"file_path": str(path)}},
                        self.plans, **kw)

    def bash(self, command, **kw):
        kw.setdefault("cwd", self.work)
        return run_gate({"tool_name": "Bash", "tool_input": {"command": command}},
                        self.plans, **kw)

    def reason(self, proc):
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    # --- allowed_paths ---

    def test_symlink_dotdot_escape_from_allowed_path_is_denied(self):
        a = self.work / "a"
        a.mkdir()
        deep = self.work / "elsewhere" / "deep"
        deep.mkdir(parents=True)
        _symlink(a / "lnk", deep)
        write_active_plan(self.plans, str(a / "f.txt"))
        # Lexically `a/lnk/../f.txt` collapses to the allowed `a/f.txt`; the OS
        # opens it next to the link's TARGET (`elsewhere/f.txt`).
        sneaky = "%s/lnk/../f.txt" % a
        for tool in ("Edit", "Write"):
            p = self.edit(sneaky, tool=tool)
            self.assertEqual(decision(p), "deny", msg=tool)
            self.assertIn("NOT in allowed_paths", self.reason(p))
        self.assertEqual(decision(self.edit(a / "f.txt")), "allow")  # control

    def test_symlink_dotdot_escape_via_bash_redirect_is_denied(self):
        a = self.work / "a"
        a.mkdir()
        deep = self.work / "elsewhere" / "deep"
        deep.mkdir(parents=True)
        _symlink(a / "lnk", deep)
        write_active_plan(self.plans, str(a / "f.txt"))
        # (forward-slash paths: bash would read the backslashes of a Windows path as escapes)
        p = self.bash("npm test > %s/lnk/../f.txt" % a.as_posix())
        self.assertEqual(decision(p), "deny")
        # the reason is the plan match, not some earlier failure
        self.assertIn("NOT in allowed_paths", json.loads(p.stdout)["hookSpecificOutput"]
                      ["permissionDecisionReason"])
        p = self.bash("npm test > %s/f.txt" % a.as_posix())
        self.assertEqual(decision(p), "allow", p.stdout)

    def test_symlinked_directory_in_front_of_target_is_resolved_both_sides(self):
        real = self.work / "real"
        real.mkdir()
        _symlink(self.work / "via", real)
        # Plan lists the file through the link; the write names the real path
        # (and the reverse): same file, so it matches. Another file does not.
        write_active_plan(self.plans, str(self.work / "via" / "f.txt"))
        self.assertEqual(decision(self.edit(real / "f.txt")), "allow")
        self.assertEqual(decision(self.edit(self.work / "via" / "f.txt")), "allow")
        self.assertEqual(decision(self.edit(real / "other.txt")), "deny")
        write_active_plan(self.plans, str(real / "f.txt"))
        self.assertEqual(decision(self.edit(self.work / "via" / "f.txt")), "allow")
        self.assertEqual(decision(self.edit(self.work / "via" / "other.txt")), "deny")

    def test_not_yet_existing_target_resolves_through_its_existing_parent(self):
        outside = self.work / "outside"
        outside.mkdir()
        allowed_dir = self.work / "plan_area"
        allowed_dir.mkdir()
        _symlink(allowed_dir / "lnk", outside)
        # Neither `new.txt` exists. The plan lists a file in the plan area; the
        # write goes through a link in it to `outside/`: a different location.
        write_active_plan(self.plans, str(allowed_dir / "new.txt"))
        self.assertEqual(decision(self.edit(allowed_dir / "lnk" / "new.txt")), "deny")
        self.assertEqual(decision(self.edit(allowed_dir / "new.txt")), "allow")

    def test_dangling_symlink_target_is_judged_by_where_it_points(self):
        outside = self.work / "outside"
        outside.mkdir()
        allowed_dir = self.work / "plan_area"
        allowed_dir.mkdir()
        _symlink(allowed_dir / "dangling.txt", outside / "not_created_yet.txt")
        write_active_plan(self.plans, str(allowed_dir / "other.txt"))
        # A write through the dangling link would CREATE outside/not_created_yet.txt.
        self.assertEqual(decision(self.edit(allowed_dir / "dangling.txt")), "deny")

    def test_case_only_difference_follows_the_file_system(self):
        write_active_plan(self.plans, str(self.work / "Notes.TXT"))
        other_case = self.work / "notes.txt"
        p = self.edit(other_case)
        if _fs_is_case_insensitive(self.work):
            self.assertEqual(decision(p), "allow")  # the same file
        else:
            self.assertEqual(decision(p), "deny")   # a different file
        self.assertEqual(decision(self.edit(self.work / "Notes.TXT")), "allow")

    def test_case_difference_in_a_directory_name_follows_the_file_system(self):
        (self.work / "Sub").mkdir()
        write_active_plan(self.plans, str(self.work / "Sub" / "f.txt"))
        p = self.edit(self.work / "sub" / "f.txt")
        expected = "allow" if _fs_is_case_insensitive(self.work) else "deny"
        self.assertEqual(decision(p), expected)

    # --- always-writable dirs ---

    def aw_setup(self):
        aw = self.root / "aw"
        aw.mkdir()
        out = self.root / "out"
        out.mkdir()
        _symlink(aw / "lnk", out)
        # An unrelated active plan, so a denial is a real "not allowed", not
        # "no plan".
        write_active_plan(self.plans, str(self.work / "unrelated.txt"))
        return aw, out, {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}

    def test_symlink_inside_always_writable_dir_cannot_escape_it(self):
        aw, out, env = self.aw_setup()
        for tool in ("Edit", "Write"):
            p = self.edit(aw / "lnk" / "x.txt", tool=tool, extra_env=env)
            self.assertEqual(decision(p), "deny", msg=tool)
            self.assertNotIn("always-writable", self.reason(p))
        # control: a real file in the dir is still free to write
        p = self.edit(aw / "ok.txt", extra_env=env)
        self.assertEqual(decision(p), AW_OK)

    def test_dotdot_after_symlink_inside_always_writable_dir_is_denied(self):
        aw, out, env = self.aw_setup()
        # Lexically aw/lnk/../y.txt is aw/y.txt; really it is <root>/y.txt.
        p = self.edit("%s/lnk/../y.txt" % aw, extra_env=env)
        self.assertEqual(decision(p), "deny")

    def test_bash_redirect_through_symlink_in_always_writable_dir_is_denied(self):
        aw, out, env = self.aw_setup()
        # (forward-slash paths: bash would read the backslashes of a Windows path as escapes)
        for tail in ("lnk/z.txt", "lnk/../z.txt"):
            p = self.bash("echo hi > %s/%s" % (aw.as_posix(), tail), extra_env=env)
            self.assertEqual(decision(p), "deny", tail)
            # denied by the plan check (not always-writable), not by an earlier failure
            self.assertIn("NOT in allowed_paths", json.loads(p.stdout)["hookSpecificOutput"]
                          ["permissionDecisionReason"], tail)
        p = self.bash("echo hi > %s/z.txt" % aw.as_posix(), extra_env=env)
        self.assertEqual(decision(p), AW_OK, p.stdout)

    def test_symlink_inside_always_writable_dir_pointing_inside_it_is_fine(self):
        aw, out, env = self.aw_setup()
        (aw / "sub").mkdir()
        _symlink(aw / "inner", aw / "sub")
        p = self.edit(aw / "inner" / "x.txt", extra_env=env)
        self.assertEqual(decision(p), AW_OK)

    def test_always_writable_dir_that_is_itself_a_symlink(self):
        real = self.root / "realplans"
        real.mkdir()
        link = self.root / "plans_link"
        _symlink(link, real)
        write_active_plan(self.plans, str(self.work / "unrelated.txt"))
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(link)}
        self.assertEqual(decision(self.edit(link / "p.md", extra_env=env)), AW_OK)
        self.assertEqual(decision(self.edit(real / "p.md", extra_env=env)), AW_OK)
        self.assertEqual(decision(self.edit(self.root / "nope.md", extra_env=env)), "deny")

    def test_mcp_target_through_symlink_in_always_writable_dir_is_denied(self):
        aw, out, env = self.aw_setup()
        env = dict(env, PLAN_GATE_MCP_WRITE_TOOLS="mcp__fs__write:path")
        def mcp(path):
            return run_gate({"tool_name": "mcp__fs__write", "tool_input": {"path": str(path)}},
                            self.plans, cwd=self.work, extra_env=env)
        self.assertEqual(decision(mcp(aw / "lnk" / "m.txt")), "deny")
        self.assertEqual(decision(mcp(aw / "m.txt")), AW_OK)

    def test_always_writable_case_follows_the_file_system(self):
        aw = self.root / "AwDir"
        aw.mkdir()
        write_active_plan(self.plans, str(self.work / "unrelated.txt"))
        env = {"PLAN_GATE_ALWAYS_WRITABLE": str(aw)}
        p = self.edit(self.root / "awdir" / "f.txt", extra_env=env)
        expected = AW_OK if _fs_is_case_insensitive(self.root) else "deny"
        self.assertEqual(decision(p), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
