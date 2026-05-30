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


if __name__ == "__main__":
    unittest.main(verbosity=2)
