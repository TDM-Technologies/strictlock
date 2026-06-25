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


if __name__ == "__main__":
    unittest.main(verbosity=2)
