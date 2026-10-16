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


if __name__ == "__main__":
    unittest.main(verbosity=2)
