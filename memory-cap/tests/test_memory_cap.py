#!/usr/bin/env python3
"""Standalone test suite for memory-cap.

Stdlib `unittest`, no third-party dependencies. Each test invokes the real
memory-cap.py as a subprocess (tool-use JSON on stdin, decision JSON on stdout).

Run:  python memory-cap/tests/test_memory_cap.py
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
GATE = HERE.parent / "memory-cap.py"

OVER_CAP_LINE = "- " + ("x" * 250)      # 252 chars, > default cap of 200
SHORT_LINE = "- a short index entry"


def run_gate(payload: dict, gate_on: bool = True, **env_overrides):
    env = dict(os.environ)
    env["MEMORY_CAP"] = "on" if gate_on else "off"
    env["MEMORY_CAP_BYPASS"] = ""
    env["MEMORY_CAP_CHARS"] = ""
    for k in ("MEMORY_CAP_MAX_ENTRIES", "MEMORY_CAP_TOTAL_CHARS",
              "MEMORY_CAP_DECISION_LOG", "MEMORY_CAP_LOG_DIR"):
        env[k] = ""
    # Match any path ending in memory.md so tests can use a temp file.
    env["MEMORY_CAP_PATH_RE"] = r"memory\.md$"
    for k, v in env_overrides.items():
        env[k] = v
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def decision(proc) -> str:
    try:
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "<no-decision: stdout=%r stderr=%r>" % (proc.stdout, proc.stderr)


class MemoryCapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.index = self.root / "memory.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: Path, content: str) -> dict:
        return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}}

    def test_gate_off_allows_over_cap(self):
        p = run_gate(self._write(self.index, OVER_CAP_LINE), gate_on=False)
        self.assertEqual(decision(p), "allow")

    def test_non_index_file_allowed(self):
        other = self.root / "notes.md"
        p = run_gate(self._write(other, OVER_CAP_LINE))
        self.assertEqual(decision(p), "allow")

    def test_index_short_lines_allowed(self):
        p = run_gate(self._write(self.index, f"# Heading\n{SHORT_LINE}\n{SHORT_LINE}\n"))
        self.assertEqual(decision(p), "allow")

    def test_index_over_cap_line_denied(self):
        p = run_gate(self._write(self.index, f"# Heading\n{OVER_CAP_LINE}\n"))
        self.assertEqual(decision(p), "deny")

    def test_long_heading_not_counted(self):
        # A long line that is NOT a `- ` entry must pass.
        p = run_gate(self._write(self.index, "# " + ("H" * 300) + "\n" + SHORT_LINE + "\n"))
        self.assertEqual(decision(p), "allow")

    def test_configurable_cap(self):
        sixty = "- " + ("y" * 60)  # 62 chars
        p = run_gate(self._write(self.index, sixty), MEMORY_CAP_CHARS="50")
        self.assertEqual(decision(p), "deny")

    def test_bypass_allows_over_cap(self):
        p = run_gate(self._write(self.index, OVER_CAP_LINE), MEMORY_CAP_BYPASS="1")
        self.assertEqual(decision(p), "allow")

    def test_edit_reconstruction_denied(self):
        self.index.write_text("# Heading\n- a\n", encoding="utf-8")
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.index),
                "old_string": "- a",
                "new_string": OVER_CAP_LINE,
                "replace_all": False,
            },
        }
        p = run_gate(payload)
        self.assertEqual(decision(p), "deny")

    def test_edit_nonmatching_old_string_defers(self):
        # Regression: a pre-existing over-cap line sits in the file, and the
        # Edit's old_string does NOT byte-exactly match anything (here, a stray
        # trailing space). str.replace would be a silent no-op, so evaluating the
        # UNCHANGED file would wrongly DENY over the pre-existing over-cap line the
        # edit never touches. The hook must DEFER (allow) and let the Edit tool
        # surface its own "string not found" error.
        self.index.write_text(
            f"# Heading\n{OVER_CAP_LINE}\n- keeper\n", encoding="utf-8"
        )
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.index),
                "old_string": "- keeper ",  # trailing space: absent from the file
                "new_string": "- still short",
                "replace_all": False,
            },
        }
        p = run_gate(payload)
        self.assertEqual(decision(p), "allow")

    def test_edit_nonunique_old_string_defers(self):
        # Regression: old_string occurs more than once and replace_all is False,
        # so the Edit tool will reject "string not unique". The hook cannot know
        # which occurrence is meant — it must DEFER (allow) rather than apply a
        # single replacement and then DENY over the pre-existing over-cap line.
        self.index.write_text(
            f"# Heading\n{OVER_CAP_LINE}\n- dup\n- dup\n", encoding="utf-8"
        )
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.index),
                "old_string": "- dup",
                "new_string": "- changed",
                "replace_all": False,
            },
        }
        p = run_gate(payload)
        self.assertEqual(decision(p), "allow")

    def test_edit_faithful_compression_allowed(self):
        # A faithful, unique edit that REPLACES a pre-existing over-cap line with
        # a short one must be allowed: the hook checks the RESULT, not the stale
        # file content.
        self.index.write_text(f"# Heading\n{OVER_CAP_LINE}\n", encoding="utf-8")
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.index),
                "old_string": OVER_CAP_LINE,
                "new_string": SHORT_LINE,
                "replace_all": False,
            },
        }
        p = run_gate(payload)
        self.assertEqual(decision(p), "allow")

    # --- changed-region scan: the self-wedge fix -------------------------------

    def test_write_retaining_preexisting_over_cap_allowed(self):
        # (a) WEDGE-REMOVED. The index ALREADY contains an over-cap `- ` line on
        # disk. A full-file Write that RETAINS that exact line verbatim and adds
        # only a short, compliant new entry must be ALLOWED — the retained line is
        # pre-existing and untouched, so it is never re-flagged. This is precisely
        # the case the whole-file scan used to DENY (the wedge): proof it's gone.
        self.index.write_text(
            f"# Heading\n{OVER_CAP_LINE}\n- keeper\n", encoding="utf-8"
        )
        retained = f"# Heading\n{OVER_CAP_LINE}\n- keeper\n{SHORT_LINE}\n"
        p = run_gate(self._write(self.index, retained))
        self.assertEqual(
            decision(p), "allow",
            msg="retaining a pre-existing over-cap line must not wedge the index",
        )

    def test_write_introducing_new_over_cap_denied(self):
        # (b) GOOD-PROPERTY-KEPT (Write). A pre-existing over-cap line is retained
        # AND a NEW, distinct over-cap line is introduced by the same Write. The
        # new one must still be caught — the fix narrows the scan, it does not
        # loosen the cap.
        self.index.write_text(
            f"# Heading\n{OVER_CAP_LINE}\n", encoding="utf-8"
        )
        new_over_cap = "- " + ("z" * 300)  # distinct from OVER_CAP_LINE, over cap
        result = f"# Heading\n{OVER_CAP_LINE}\n{new_over_cap}\n"
        p = run_gate(self._write(self.index, result))
        self.assertEqual(decision(p), "deny")

    def test_edit_introducing_new_over_cap_denied(self):
        # (b) GOOD-PROPERTY-KEPT (Edit). A pre-existing over-cap line sits in the
        # file untouched; a faithful, unique Edit turns a short line into a NEW
        # over-cap line. The introduced over-cap line must be DENIED even though a
        # different pre-existing over-cap line is (correctly) ignored.
        self.index.write_text(
            f"# Heading\n{OVER_CAP_LINE}\n- short\n", encoding="utf-8"
        )
        new_over_cap = "- " + ("z" * 300)
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.index),
                "old_string": "- short",
                "new_string": new_over_cap,
                "replace_all": False,
            },
        }
        p = run_gate(payload)
        self.assertEqual(decision(p), "deny")

    def test_write_retaining_crlf_preexisting_over_cap_allowed(self):
        # (c) CRLF / OS-agnostic. The on-disk index was authored with Windows
        # (CRLF) line endings and already holds an over-cap `- ` line. An LF-form
        # Write that retains that same logical line must be ALLOWED: the multiset
        # is compared via splitlines()/rstrip, so CRLF vs LF does not make every
        # line read as "introduced" (which would reintroduce the wedge on Windows).
        crlf_existing = f"# Heading\r\n{OVER_CAP_LINE}\r\n- keeper\r\n"
        # Write bytes verbatim so the CRLFs survive (text mode would translate).
        self.index.write_bytes(crlf_existing.encode("utf-8"))
        lf_result = f"# Heading\n{OVER_CAP_LINE}\n- keeper\n{SHORT_LINE}\n"
        p = run_gate(self._write(self.index, lf_result))
        self.assertEqual(
            decision(p), "allow",
            msg="CRLF-authored pre-existing line must normalize equal to its LF form",
        )

    # --- C2: NotebookEdit baselines against on-disk content --------------------

    def _nb(self, source: str) -> dict:
        return {"tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": str(self.index), "new_source": source}}

    def test_notebookedit_retaining_preexisting_over_cap_allowed(self):
        self.index.write_text(f"# H\n{OVER_CAP_LINE}\n- keeper\n", encoding="utf-8")
        p = run_gate(self._nb(f"{OVER_CAP_LINE}\n{SHORT_LINE}\n"))
        self.assertEqual(decision(p), "allow", msg=p.stdout)

    def test_notebookedit_introducing_over_cap_denied(self):
        self.index.write_text("# H\n- keeper\n", encoding="utf-8")
        p = run_gate(self._nb(OVER_CAP_LINE))
        self.assertEqual(decision(p), "deny")

    # --- C3: opt-in total caps --------------------------------------------------

    def _edit(self, old: str, new: str) -> dict:
        return {"tool_name": "Edit", "tool_input": {
            "file_path": str(self.index), "old_string": old, "new_string": new}}

    def test_total_caps_off_by_default(self):
        many = "".join(f"- e{i}\n" for i in range(500))
        p = run_gate(self._write(self.index, many))
        self.assertEqual(decision(p), "allow")

    def test_max_entries_denies_new_write_over_cap(self):
        content = "".join(f"- e{i}\n" for i in range(6))
        p = run_gate(self._write(self.index, content), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(p), "deny")
        self.assertIn("entries", p.stdout)

    def test_invalid_total_cap_value_announced_on_stderr_and_stays_off(self):
        content = "".join(f"- e{i}\n" for i in range(6))
        for bad in ("1e3", "50k", "0", "-4"):
            p = run_gate(self._write(self.index, content), MEMORY_CAP_MAX_ENTRIES=bad)
            self.assertEqual(decision(p), "allow")
            self.assertIn("MEMORY_CAP_MAX_ENTRIES", p.stderr)
            self.assertIn("OFF", p.stderr)

    def test_max_entries_at_cap_allowed(self):
        content = "".join(f"- e{i}\n" for i in range(5))
        p = run_gate(self._write(self.index, content), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(p), "allow")

    def test_max_entries_edit_growing_past_cap_denied(self):
        self.index.write_text("".join(f"- e{i}\n" for i in range(5)), encoding="utf-8")
        p = run_gate(self._edit("- e4\n", "- e4\n- extra\n"), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(p), "deny")

    def test_max_entries_already_over_not_wedged(self):
        # Index already holds 8 entries (> cap 5): a level or shrinking edit passes,
        # growing it further is refused.
        self.index.write_text("".join(f"- e{i}\n" for i in range(8)), encoding="utf-8")
        level = run_gate(self._edit("- e0\n", "- renamed\n"), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(level), "allow", msg=level.stdout)
        shrink = run_gate(self._edit("- e0\n", ""), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(shrink), "allow", msg=shrink.stdout)
        grow = run_gate(self._edit("- e0\n", "- e0\n- more\n"), MEMORY_CAP_MAX_ENTRIES="5")
        self.assertEqual(decision(grow), "deny")

    def test_total_chars_denies_growth_past_cap(self):
        self.index.write_text("- aaaa\n- bbbb\n", encoding="utf-8")  # 12 chars
        p = run_gate(self._edit("- bbbb", "- bbbb\n- cccccccc"), MEMORY_CAP_TOTAL_CHARS="20")
        self.assertEqual(decision(p), "deny")
        self.assertIn("total", p.stdout)

    def test_total_chars_already_over_shrinking_allowed(self):
        self.index.write_text("- " + "a" * 100 + "\n- " + "b" * 100 + "\n", encoding="utf-8")
        p = run_gate(self._edit("- " + "b" * 100, "- b"), MEMORY_CAP_TOTAL_CHARS="50")
        self.assertEqual(decision(p), "allow", msg=p.stdout)

    def test_total_chars_invalid_value_ignored(self):
        p = run_gate(self._write(self.index, "- x\n" * 50), MEMORY_CAP_TOTAL_CHARS="abc")
        self.assertEqual(decision(p), "allow")


class DecisionLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.index = self.root / "memory.md"
        self.logdir = self.root / "logs"
        self.log = self.logdir / "memory-cap-decisions.log"

    def tearDown(self):
        self._tmp.cleanup()

    def _w(self, content: str) -> dict:
        return {"tool_name": "Write",
                "tool_input": {"file_path": str(self.index), "content": content}}

    def _run(self, payload, **env):
        base = {"MEMORY_CAP_DECISION_LOG": "1", "MEMORY_CAP_LOG_DIR": str(self.logdir)}
        base.update(env)
        return run_gate(payload, **base)

    def _rows(self):
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()]

    def _verify(self, path=None):
        argv = [sys.executable, str(GATE), "verify-log"]
        if path is not None:
            argv.append(str(path))
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=dict(os.environ, MEMORY_CAP_LOG_DIR=str(self.logdir)))

    def test_allow_and_deny_rows_chain_verifies(self):
        self.assertEqual(decision(self._run(self._w(SHORT_LINE))), "allow")
        self.assertEqual(decision(self._run(self._w(OVER_CAP_LINE))), "deny")
        rows = self._rows()
        self.assertEqual([r["decision"] for r in rows], ["allow", "deny"])
        self.assertEqual(rows[0]["prev"], "0" * 64)
        self.assertEqual(rows[1]["prev"], rows[0]["h"])
        for r in rows:
            self.assertEqual(r["tool"], "Write")
            self.assertEqual(r["target"], str(self.index))
            self.assertTrue(r["reason"])
        self.assertEqual(rows[1]["class"], "over_cap")
        # privacy: no payload contents anywhere in the log
        self.assertNotIn("xxxxxxxx", self.log.read_text(encoding="utf-8"))
        v = self._verify()
        self.assertEqual(v.returncode, 0, msg=v.stdout + v.stderr)
        self.assertIn("chain intact", v.stdout)

    def test_flag_off_no_log(self):
        p = run_gate(self._w(SHORT_LINE), MEMORY_CAP_LOG_DIR=str(self.logdir))
        self.assertEqual(decision(p), "allow")
        self.assertFalse(self.logdir.exists())

    def test_non_index_and_inert_gate_not_logged(self):
        other = {"tool_name": "Write", "tool_input": {
            "file_path": str(self.root / "notes.md"), "content": "x"}}
        self._run(other)
        run_gate(self._w(SHORT_LINE), gate_on=False,
                 MEMORY_CAP_DECISION_LOG="1", MEMORY_CAP_LOG_DIR=str(self.logdir))
        self.assertFalse(self.log.exists())

    def test_bypass_is_logged(self):
        p = self._run(self._w(OVER_CAP_LINE), MEMORY_CAP_BYPASS="1")
        self.assertEqual(decision(p), "allow")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["class"], "emergency_bypass")
        self.assertEqual(rows[0]["decision"], "allow")
        self.assertEqual(rows[0]["tool"], "Write")

    def test_logging_failure_never_blocks(self):
        blocker = self.root / "iamafile"
        blocker.write_text("x", encoding="utf-8")
        bad = {"MEMORY_CAP_LOG_DIR": str(blocker / "sub")}  # cannot mkdir under a file
        self.assertEqual(decision(self._run(self._w(SHORT_LINE), **bad)), "allow")
        self.assertEqual(decision(self._run(self._w(OVER_CAP_LINE), **bad)), "deny")

    def test_tamper_detected(self):
        for _ in range(3):
            self._run(self._w(SHORT_LINE))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["decision"] = "deny"
        lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = self._verify(self.log)
        self.assertEqual(v.returncode, 2)
        self.assertIn("line 2", v.stdout)

    def test_deleted_row_detected(self):
        for _ in range(3):
            self._run(self._w(SHORT_LINE))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        del lines[1]
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(self._verify(self.log).returncode, 2)

    def test_verify_missing_log_exit_2(self):
        self.assertEqual(self._verify(self.root / "nope.log").returncode, 2)

    def test_total_cap_deny_is_logged(self):
        p = self._run(self._w("- a\n- b\n- c\n"), MEMORY_CAP_MAX_ENTRIES="2")
        self.assertEqual(decision(p), "deny")
        self.assertEqual(self._rows()[0]["class"], "total_cap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
