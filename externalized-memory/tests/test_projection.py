#!/usr/bin/env python3
"""Standalone test suite for externalized-memory/projection.py.

Stdlib `unittest`, no third-party dependencies, no pytest. Run directly:

    python3 externalized-memory/tests/test_projection.py

The suite imports projection.py as a module (for the pure functions) AND drives it as a
subprocess (for the CLI exit-code contracts), so both the library surface and the tool
boundary are proven. Every assertion is on REAL behavior — byte-identity of two renders,
exact out-of-region bytes after a splice, the specific reject path each guard takes — never a
smoke assertion. The fail-closed paths (non-canonical UTC, missing body, missing field,
missing fence marker) are covered EXPLICITLY.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "projection.py"

# Import projection.py as a module despite its non-identifier filename / hyphenless name.
_spec = importlib.util.spec_from_file_location("projection", SCRIPT)
projection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projection)


def make_record(rec_id="r1", updated="2026-06-25T12:00:00Z", body="payload", **kw):
    """Build a valid record via the sanctioned writer (so the fixture itself proves
    build_record accepts a good record)."""
    return projection.build_record(id=rec_id, updated=updated, body=body, **kw)


def run_cli(*args, stdin_text=None):
    """Drive projection.py as a subprocess — the exact boundary CI uses."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


class DeterminismTests(unittest.TestCase):
    """Part 2: same records => byte-identical projection, with NO clock and NO git read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.records = Path(self._tmp.name) / "records"
        self.records.mkdir()
        # Write records OUT OF id order on disk to prove the render sorts, not the FS.
        (self.records / "02-second.md").write_text(
            make_record("02-second", "2026-01-02T00:00:00Z", "second body"),
            encoding="utf-8",
        )
        (self.records / "01-first.md").write_text(
            make_record("01-first", "2026-01-01T00:00:00Z", "first body"),
            encoding="utf-8",
        )
        (self.records / "03-third.md").write_text(
            make_record("03-third", "2026-01-03T00:00:00Z", "third body"),
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_two_renders_are_byte_identical(self):
        first = projection.render_projection(self.records)
        second = projection.render_projection(self.records)
        self.assertEqual(first, second)
        # Non-trivial: it actually rendered all three rows in a table.
        self.assertEqual(first.count("\n01-"), 0)  # ids are in cells, not line starts
        self.assertIn("| 01-first |", first)
        self.assertIn("| 02-second |", first)
        self.assertIn("| 03-third |", first)

    def test_render_is_sorted_by_id_not_filesystem_order(self):
        out = projection.render_projection(self.records)
        i1 = out.index("01-first")
        i2 = out.index("02-second")
        i3 = out.index("03-third")
        self.assertLess(i1, i2)
        self.assertLess(i2, i3)

    def test_render_does_not_read_clock_or_git(self):
        """Prove git-free/clock-free structurally: monkeypatch subprocess.run and
        time.time to raise — a render that touched either would explode. It must not."""
        import subprocess as _sp
        import time as _time
        orig_run, orig_time, orig_time_ns = _sp.run, _time.time, _time.time_ns

        def boom_run(*a, **k):
            raise AssertionError("render shelled out to a subprocess (git?) — not allowed")

        def boom_time(*a, **k):
            raise AssertionError("render read the wall clock — not allowed")

        _sp.run, _time.time, _time.time_ns = boom_run, boom_time, boom_time
        try:
            out = projection.render_projection(self.records)
        finally:
            _sp.run, _time.time, _time.time_ns = orig_run, orig_time, orig_time_ns
        self.assertIn("| 01-first |", out)

    def test_render_output_is_lf_only(self):
        out = projection.render_projection(self.records)
        self.assertNotIn("\r", out)

    def test_empty_records_dir_renders_stable_placeholder(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        a = projection.render_projection(empty)
        b = projection.render_projection(empty)
        self.assertEqual(a, b)
        self.assertIn("_No records yet._", a)


class SplicePreservationTests(unittest.TestCase):
    """Part 3: splicing rewrites ONLY the fenced region; out-of-region bytes are exact."""

    BEGIN = projection.DEFAULT_BEGIN
    END = projection.DEFAULT_END

    def _doc(self, region_inner=""):
        return (
            "# Title\n\nIntro prose with a trailing space here.  \n\n"
            f"{self.BEGIN}\n{region_inner}{self.END}\n\nFooter prose.\nLast line no newline"
        )

    def test_out_of_region_bytes_are_preserved_exactly(self):
        before = self._doc(region_inner="OLD STALE TABLE\n")
        spliced = projection.splice_region(
            before, "NEW BLOCK CONTENT", begin=self.BEGIN, end=self.END
        )
        # Everything before BEGIN and after END is byte-identical to the original.
        head = before.split(self.BEGIN)[0]
        tail = before.split(self.END)[1]
        self.assertTrue(spliced.startswith(head + self.BEGIN))
        self.assertTrue(spliced.endswith(self.END + tail))
        # The new content is in the region; the old content is gone.
        self.assertIn("NEW BLOCK CONTENT", spliced)
        self.assertNotIn("OLD STALE TABLE", spliced)

    def test_resplice_is_idempotent(self):
        doc = self._doc()
        once = projection.splice_region(doc, "BLOCK", begin=self.BEGIN, end=self.END)
        twice = projection.splice_region(once, "BLOCK", begin=self.BEGIN, end=self.END)
        self.assertEqual(once, twice)

    def test_markers_themselves_are_preserved(self):
        doc = self._doc()
        spliced = projection.splice_region(doc, "X", begin=self.BEGIN, end=self.END)
        self.assertEqual(spliced.count(self.BEGIN), 1)
        self.assertEqual(spliced.count(self.END), 1)

    def test_missing_begin_marker_is_rejected(self):
        doc = "no markers here\nat all\n"
        with self.assertRaises(projection.ProjectionError):
            projection.splice_region(doc, "X", begin=self.BEGIN, end=self.END)

    def test_missing_end_marker_is_rejected(self):
        doc = f"prose\n{self.BEGIN}\nbut no end\n"
        with self.assertRaises(projection.ProjectionError):
            projection.splice_region(doc, "X", begin=self.BEGIN, end=self.END)

    def test_splice_never_appends_a_second_copy(self):
        """A missing marker must be a hard refusal — proving the generator never silently
        appends a duplicate block to the end of the file."""
        doc = "content without markers\n"
        try:
            projection.splice_region(doc, "BLOCK", begin=self.BEGIN, end=self.END)
            self.fail("expected ProjectionError on missing markers")
        except projection.ProjectionError:
            pass  # correct: refused, nothing appended


class CanonicalUtcGuardTests(unittest.TestCase):
    """Part 4: timestamps are validated/normalized to canonical UTC; non-canonical REJECTED."""

    def test_canonical_utc_accepted(self):
        ok = projection.validate_canonical_utc("updated", "2026-06-25T14:30:00Z")
        self.assertEqual(ok, "2026-06-25T14:30:00Z")

    def test_offset_timestamp_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25T12:00:00+02:00")

    def test_fractional_seconds_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25T12:00:00.123Z")

    def test_lowercase_t_or_z_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25t12:00:00z")

    def test_space_separator_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25 12:00:00Z")

    def test_naive_timestamp_without_z_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25T12:00:00")

    def test_out_of_range_calendar_rejected(self):
        # A syntactically-canonical but impossible date must still be rejected.
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-13-01T00:00:00Z")
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-02-29T00:00:00Z")  # not a leap yr
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_canonical_utc("updated", "2026-06-25T24:00:00Z")

    def test_build_record_rejects_non_canonical_timestamp(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.build_record(
                id="r1", updated="2026-06-25T12:00:00+02:00", body="x"
            )

    def test_render_rejects_a_record_with_a_bad_timestamp(self):
        """A record hand-edited to a bad timestamp must fail the render loudly, not render a
        silently-blank row — proving the guard is enforced on the read/render path too."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        records = Path(tmp.name)
        # Bypass build_record to plant a non-canonical timestamp directly on disk.
        (records / "bad.md").write_text(
            "---\nid: bad\nstatus: \nsummary: \nupdated: 2026-06-25T12:00:00+02:00\n---\n\nbody\n",
            encoding="utf-8",
        )
        with self.assertRaises(projection.RecordValidationError):
            projection.render_projection(records)


class RequiredBodyTests(unittest.TestCase):
    """Part 5: a record missing required body content is REJECTED."""

    def test_empty_body_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.build_record(id="r1", updated="2026-06-25T12:00:00Z", body="")

    def test_whitespace_only_body_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.build_record(id="r1", updated="2026-06-25T12:00:00Z", body="   \n\t  ")

    def test_validate_record_text_rejects_bodiless_record(self):
        text = "---\nid: r1\nstatus: \nsummary: \nupdated: 2026-06-25T12:00:00Z\n---\n\n"
        with self.assertRaises(projection.RecordValidationError):
            projection.validate_record_text(text)

    def test_non_empty_body_accepted(self):
        rec = projection.build_record(id="r1", updated="2026-06-25T12:00:00Z", body="real")
        fields = projection.validate_record_text(rec)
        self.assertEqual(fields["id"], "r1")


class RequiredFieldTests(unittest.TestCase):
    """Schema: required fields must be present and non-empty."""

    def test_missing_updated_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.build_record(id="r1", updated="", body="x")

    def test_missing_id_rejected(self):
        with self.assertRaises(projection.RecordValidationError):
            projection.build_record(id="", updated="2026-06-25T12:00:00Z", body="x")

    def test_round_trip_parse(self):
        rec = projection.build_record(
            id="r1", updated="2026-06-25T12:00:00Z", body="b", status="active", summary="s: x"
        )
        fields, body = projection.parse_record(rec)
        self.assertEqual(fields["id"], "r1")
        self.assertEqual(fields["status"], "active")
        self.assertEqual(fields["summary"], "s: x")  # embedded ':' survives the line parser
        self.assertEqual(fields["updated"], "2026-06-25T12:00:00Z")
        self.assertEqual(body, "b")


class EndToEndSpliceTests(unittest.TestCase):
    """Library-level splice into a real target: two splices are byte-identical AND the
    out-of-region bytes are exact (determinism + preservation, end to end)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.records = root / "records"
        self.records.mkdir()
        (self.records / "a.md").write_text(
            make_record("a", "2026-01-01T00:00:00Z", "abody"), encoding="utf-8"
        )
        (self.records / "b.md").write_text(
            make_record("b", "2026-01-02T00:00:00Z", "bbody"), encoding="utf-8"
        )
        self.target = root / "STATUS.md"
        # The END marker line owns its own trailing newline; `tail` is everything that
        # follows it, byte-for-byte — so the preservation assertion below is unambiguous.
        self.preamble = "# Status\n\nHand prose.\n\n"
        self.tail = "\nHand footer.\nLast line, no trailing newline"
        self.original = (
            self.preamble
            + projection.DEFAULT_BEGIN + "\n" + projection.DEFAULT_END + "\n"
            + self.tail
        )
        self.target.write_text(self.original, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_splice_then_resplice_is_byte_identical(self):
        first = projection.project_into_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END,
            title=projection.DEFAULT_TITLE, write=True,
        )
        on_disk_1 = self.target.read_text(encoding="utf-8")
        second = projection.project_into_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END,
            title=projection.DEFAULT_TITLE, write=True,
        )
        on_disk_2 = self.target.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(on_disk_1, on_disk_2)

    def test_out_of_region_prose_survives_splice(self):
        projection.project_into_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END,
            title=projection.DEFAULT_TITLE, write=True,
        )
        result = self.target.read_text(encoding="utf-8")
        # Bytes BEFORE the begin marker, and AFTER the end marker, are preserved exactly.
        self.assertTrue(result.startswith(self.preamble + projection.DEFAULT_BEGIN + "\n"))
        self.assertTrue(result.endswith(projection.DEFAULT_END + "\n" + self.tail))
        self.assertIn("| a |", result)
        self.assertIn("| b |", result)

    def test_check_detects_drift_then_clean(self):
        # Before splicing, the region is empty -> drift.
        ok, _ = projection.check_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END, title=projection.DEFAULT_TITLE,
        )
        self.assertFalse(ok)
        projection.project_into_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END,
            title=projection.DEFAULT_TITLE, write=True,
        )
        ok2, _ = projection.check_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END, title=projection.DEFAULT_TITLE,
        )
        self.assertTrue(ok2)

    def test_atomic_write_leaves_no_temp_sibling(self):
        projection.project_into_target(
            records_dir=self.records, target=self.target,
            begin=projection.DEFAULT_BEGIN, end=projection.DEFAULT_END,
            title=projection.DEFAULT_TITLE, write=True,
        )
        siblings = sorted(p.name for p in self.target.parent.iterdir())
        self.assertEqual(siblings, ["STATUS.md", "records"])


class CliContractTests(unittest.TestCase):
    """The tool boundary: exit codes are the fail-closed contract CI relies on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.records = self.root / "records"
        self.records.mkdir()
        (self.records / "a.md").write_text(
            make_record("a", "2026-01-01T00:00:00Z", "abody"), encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_validate_ok_exit_zero(self):
        good = make_record("a", "2026-01-01T00:00:00Z", "abody")
        p = run_cli("validate", stdin_text=good)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("OK", p.stdout)

    def test_validate_bad_timestamp_exit_three(self):
        bad = "---\nid: a\nstatus: \nsummary: \nupdated: 2026-01-01T00:00:00+05:00\n---\n\nbody\n"
        p = run_cli("validate", stdin_text=bad)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("REJECTED", p.stderr)

    def test_validate_missing_body_exit_three(self):
        bad = "---\nid: a\nstatus: \nsummary: \nupdated: 2026-01-01T00:00:00Z\n---\n\n"
        p = run_cli("validate", stdin_text=bad)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("REJECTED", p.stderr)

    def test_render_cli_is_deterministic(self):
        p1 = run_cli("render", "--records-dir", str(self.records))
        p2 = run_cli("render", "--records-dir", str(self.records))
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p1.stdout, p2.stdout)
        self.assertIn("| a |", p1.stdout)

    def test_render_missing_config_exit_one(self):
        # No --records-dir and no env var -> loud config error, exit 1.
        p = run_cli("render")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ERROR", p.stderr)

    def test_splice_missing_marker_exit_one(self):
        target = self.root / "no_markers.md"
        target.write_text("just prose, no fence\n", encoding="utf-8")
        p = run_cli(
            "splice", "--records-dir", str(self.records), "--target", str(target)
        )
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ERROR", p.stderr)
        # Fail-closed: the target was NOT mutated.
        self.assertEqual(target.read_text(encoding="utf-8"), "just prose, no fence\n")

    def test_check_drift_exit_one_then_clean_exit_zero(self):
        target = self.root / "STATUS.md"
        target.write_text(
            projection.DEFAULT_BEGIN + "\n" + projection.DEFAULT_END + "\n", encoding="utf-8"
        )
        drift = run_cli("check", "--records-dir", str(self.records), "--target", str(target))
        self.assertEqual(drift.returncode, 1, drift.stdout + drift.stderr)
        run_cli("splice", "--records-dir", str(self.records), "--target", str(target))
        clean = run_cli("check", "--records-dir", str(self.records), "--target", str(target))
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
