#!/usr/bin/env python3
"""Standalone test suite for externalized-memory/atomic-write.py.

Stdlib `unittest`, no third-party dependencies. Each test drives the real
atomic-write.py as a subprocess (new content on stdin, target path as argv) — the
same boundary the script is used at in practice. UTF-8 is forced on the pipe so the
suite is deterministic on Windows and POSIX alike.

Run:  python externalized-memory/tests/test_atomic_write.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "atomic-write.py"


def run(target, stdin_text, *extra_args):
    args = [sys.executable, str(SCRIPT)]
    if target is not None:
        args.append(str(target))
    args.extend(extra_args)
    return subprocess.run(
        args,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",  # deterministic across platforms
    )


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.target = self.root / "STATE.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _siblings(self):
        """Everything in the dir that is NOT the target — i.e. leftover temps."""
        return sorted(p.name for p in self.root.iterdir() if p.name != self.target.name)

    def test_writes_new_file(self):
        body = "# STATE\n\n- one line\n"
        p = run(self.target, body)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.target.read_text(encoding="utf-8"), body)

    def test_overwrites_existing_atomically(self):
        self.target.write_text(
            "OLD CONTENT that is much longer than the new\n", encoding="utf-8"
        )
        new = "new\n"
        p = run(self.target, new)
        self.assertEqual(p.returncode, 0, p.stderr)
        # Fully replaced — not appended to or merged with the old content.
        self.assertEqual(self.target.read_text(encoding="utf-8"), new)

    def test_no_leftover_temp_after_success(self):
        p = run(self.target, "x\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self._siblings(), [])

    def test_preserves_exact_content(self):
        # Unicode, embedded newlines, and NO trailing newline must round-trip byte-exact.
        body = "meta: ✓\nthread — α\nno-trailing-newline"
        p = run(self.target, body)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.target.read_text(encoding="utf-8"), body)

    def test_missing_parent_dir_fails_loudly(self):
        target = self.root / "does-not-exist" / "STATE.md"
        p = run(target, "data\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("REFUSED", p.stderr)
        self.assertFalse(target.exists())
        # And nothing was littered into the existing root either.
        self.assertEqual(self._siblings(), [])

    def test_missing_path_arg_is_usage_error(self):
        p = run(None, "data\n")
        self.assertEqual(p.returncode, 2)

    def test_empty_content_writes_empty_file(self):
        p = run(self.target, "")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.target.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
