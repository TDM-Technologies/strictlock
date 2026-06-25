#!/usr/bin/env python3
r"""Standalone acceptance suite for sink-resolver.

Stdlib `unittest`, no third-party dependencies. Every test builds a REAL git repo in a
tempdir, sets up a REAL `merge=binary` conflicted merge with a REAL deterministic generator,
and drives `resolve` / `check` end-to-end — never a mock, never a smoke check. The generator
is a tiny pure renderer written into each repo: `dist/index.txt` is a byte-deterministic render
of the `src/*.txt` sources, so the suite exercises exactly the precondition the module requires.

Acceptance cases:
  (a) sink-only conflict        -> regenerate from the merged sources, byte-oracle, finalize
  (b) non-sink conflict present -> ESCALATE (exit 4), write nothing
  (c) non-sink conflict alone   -> ESCALATE (exit 4)
  (d) nothing unmerged          -> "clean", exit 0
  (e) --dry-run                 -> classify, write nothing (sink stays unmerged)
  (f) --no-commit               -> staged, merge NOT finalized (MERGE_HEAD still present)
  (g) byte-oracle fails (CHECK_CMD drift / non-deterministic generator) -> fail-closed, no finalize
  (h) the strong (CHECK_CMD) oracle vs the weak double-regenerate oracle, both paths
  (i) check: fresh -> exit 0; stale -> exit 1 (with and without CHECK_CMD; check is net non-mutating)
  (j) SINK_RESOLVER off -> no-op exit 0
  (k) misconfig (no SINKS / no GENERATOR_CMD) -> fail-closed
  (l) normalization: absolute / ./ -prefixed sink still matches the repo-relative unmerged path
  (m) a sink outside the repo -> fail-closed
  (n) generator non-zero exit -> fail-closed
  (o) escalation is inert: index + working tree unchanged after an escalate

Run:  python3 sink-resolver/tests/test_sink_resolver.py
"""
from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "sink-resolver.py"

# Import the hyphen-named script as a module (can't `import sink-resolver`).
_spec = importlib.util.spec_from_file_location("sink_resolver", SCRIPT)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

PY = sys.executable  # don't depend on a `python3` on PATH

# A pure, deterministic generator: dist/index.txt = "<name>=<content>" per sorted src/*.txt.
GEN = r'''#!/usr/bin/env python3
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent
src = root / "src"
sink = root / "dist" / "index.txt"
lines = []
for f in sorted(src.glob("*.txt")):
    lines.append(f.name + "=" + f.read_text(encoding="utf-8").strip())
content = "\n".join(lines) + "\n"
if "--check" in sys.argv:
    cur = sink.read_text(encoding="utf-8") if sink.exists() else ""
    sys.exit(0 if cur == content else 1)
sink.parent.mkdir(parents=True, exist_ok=True)
sink.write_text(content, encoding="utf-8")
'''

# A NON-deterministic generator: appends an ever-incrementing nonce from a state file, so two
# regenerates never match (the weak double-regenerate oracle must catch this).
GEN_NONDET = r'''#!/usr/bin/env python3
import sys, pathlib
root = pathlib.Path(__file__).resolve().parent
src = root / "src"
sink = root / "dist" / "index.txt"
state = root / ".nonce"
n = int(state.read_text()) + 1 if state.exists() else 1
state.write_text(str(n))
lines = []
for f in sorted(src.glob("*.txt")):
    lines.append(f.name + "=" + f.read_text(encoding="utf-8").strip())
content = "\n".join(lines) + ("\nnonce=%d\n" % n)
if "--check" in sys.argv:
    sys.exit(1)  # never "fresh" — irrelevant; the non-det path uses no CHECK_CMD
sink.parent.mkdir(parents=True, exist_ok=True)
sink.write_text(content, encoding="utf-8")
'''


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc


def _render(a: str, b: str) -> str:
    """The expected dist/index.txt for src/a.txt=a, src/b.txt=b (matches GEN)."""
    return f"a.txt={a}\nb.txt={b}\n"


class SinkResolverBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self._env_backup = dict(os.environ)
        # default config the tests share; individual tests override.
        os.environ["SINK_RESOLVER"] = "on"
        os.environ["SINK_RESOLVER_GENERATOR_CMD"] = f"{shlex.quote(PY)} gen.py"
        os.environ["SINK_RESOLVER_CHECK_CMD"] = f"{shlex.quote(PY)} gen.py --check"
        os.environ["SINK_RESOLVER_SINKS"] = "dist/index.txt"
        for k in ("SINK_RESOLVER_GENERATOR_CWD", "SINK_RESOLVER_TIMEOUT",
                  "SINK_RESOLVER_LOG_DIR"):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)
        self._tmp.cleanup()

    # ── repo / merge fixtures ────────────────────────────────────────────────────────
    def _init_repo(self, gen: str = GEN) -> None:
        r = self.root
        _git(r, "init", "-q")
        _git(r, "config", "user.email", "t@example.com")
        _git(r, "config", "user.name", "Tester")
        _git(r, "checkout", "-q", "-b", "main")
        (r / "gen.py").write_text(gen, encoding="utf-8")
        (r / ".gitattributes").write_text("dist/index.txt merge=binary\n", encoding="utf-8")
        (r / "src").mkdir()
        (r / "src" / "a.txt").write_text("A1\n", encoding="utf-8")
        (r / "src" / "b.txt").write_text("B1\n", encoding="utf-8")
        self._regen()
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "base")

    def _regen(self) -> None:
        proc = subprocess.run([PY, "gen.py"], cwd=str(self.root),
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"gen.py failed: {proc.stderr or proc.stdout}")

    def _make_sink_only_conflict(self) -> None:
        """Two branches change DIFFERENT sources (merge cleanly) + each regenerates the sink
        (merge=binary conflict). Leaves an in-progress merge with the sink unmerged."""
        r = self.root
        self._init_repo()
        _git(r, "checkout", "-q", "-b", "feat")
        (r / "src" / "b.txt").write_text("B2\n", encoding="utf-8")
        self._regen()
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "feat: b->B2")
        _git(r, "checkout", "-q", "main")
        (r / "src" / "a.txt").write_text("A2\n", encoding="utf-8")
        self._regen()
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "main: a->A2")
        merge = _git(r, "merge", "--no-edit", "feat", check=False)
        self.assertNotEqual(merge.returncode, 0, "expected a merge=binary conflict on the sink")

    def _make_nonsink_conflict(self, also_sink: bool = True) -> None:
        """Both branches change the SAME line of src/a.txt -> src/a.txt (a NON-sink) conflicts.
        also_sink: each side also regenerates, so the sink conflicts too."""
        r = self.root
        self._init_repo()
        _git(r, "checkout", "-q", "-b", "feat")
        (r / "src" / "a.txt").write_text("A_feat\n", encoding="utf-8")
        if also_sink:
            self._regen()
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "feat: a->A_feat")
        _git(r, "checkout", "-q", "main")
        (r / "src" / "a.txt").write_text("A_main\n", encoding="utf-8")
        if also_sink:
            self._regen()
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "main: a->A_main")
        merge = _git(r, "merge", "--no-edit", "feat", check=False)
        self.assertNotEqual(merge.returncode, 0)

    def _unmerged(self) -> list[str]:
        out = _git(self.root, "diff", "--name-only", "--diff-filter=U").stdout
        return sorted(ln.strip() for ln in out.splitlines() if ln.strip())

    def _run(self, *argv: str) -> int:
        return sr.main(list(argv) + ["--root", str(self.root)])


# ── (a) the happy path: sink-only conflict -> resolved + finalized ───────────────────
class TestResolveSinkOnly(SinkResolverBase):
    def test_resolves_and_finalizes(self) -> None:
        self._make_sink_only_conflict()
        self.assertEqual(self._unmerged(), ["dist/index.txt"])
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_OK)
        # the sink is now the render of the MERGED sources (a=A2, b=B2)
        self.assertEqual((self.root / "dist" / "index.txt").read_text(encoding="utf-8"),
                         _render("A2", "B2"))
        # merge finalized: no MERGE_HEAD, clean tree, nothing unmerged
        self.assertFalse(sr._merge_in_progress(self.root))
        self.assertEqual(self._unmerged(), [])
        status = _git(self.root, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "", f"expected a clean tree, got: {status!r}")

    def test_resolve_function_returns_finalized(self) -> None:
        self._make_sink_only_conflict()
        status, sinks = sr.resolve_merge(root=self.root, sinks=["dist/index.txt"])
        self.assertEqual(status, "resolved-finalized")
        self.assertEqual(sinks, ["dist/index.txt"])


# ── (b)(c)(o) escalation: a non-sink conflict -> exit 4, write nothing ────────────────
class TestEscalation(SinkResolverBase):
    def test_nonsink_alongside_sink_escalates(self) -> None:
        self._make_nonsink_conflict(also_sink=True)
        self.assertIn("src/a.txt", self._unmerged())
        before_unmerged = self._unmerged()
        before_sink = (self.root / "dist" / "index.txt").read_bytes()
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_ESCALATE)
        # inert: unmerged set + sink bytes unchanged, merge still in progress
        self.assertEqual(self._unmerged(), before_unmerged)
        self.assertEqual((self.root / "dist" / "index.txt").read_bytes(), before_sink)
        self.assertTrue(sr._merge_in_progress(self.root))

    def test_nonsink_alone_escalates(self) -> None:
        # a non-sink conflict with the sink NOT regenerated either side: src/a.txt only.
        self._make_nonsink_conflict(also_sink=False)
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_ESCALATE)

    def test_escalation_raises_with_offenders_named(self) -> None:
        self._make_nonsink_conflict(also_sink=True)
        with self.assertRaises(sr.Escalation) as ctx:
            sr.resolve_merge(root=self.root, sinks=["dist/index.txt"])
        self.assertIn("src/a.txt", str(ctx.exception))


# ── (d) nothing unmerged -> clean ────────────────────────────────────────────────────
class TestClean(SinkResolverBase):
    def test_clean_when_no_conflict(self) -> None:
        self._init_repo()
        status, sinks = sr.resolve_merge(root=self.root, sinks=["dist/index.txt"])
        self.assertEqual(status, "clean")
        self.assertEqual(sinks, [])
        self.assertEqual(self._run("resolve"), sr.EXIT_OK)


# ── (e) --dry-run -> classify, write nothing ─────────────────────────────────────────
class TestDryRun(SinkResolverBase):
    def test_dry_run_writes_nothing(self) -> None:
        self._make_sink_only_conflict()
        before = (self.root / "dist" / "index.txt").read_bytes()
        rc = self._run("resolve", "--dry-run")
        self.assertEqual(rc, sr.EXIT_OK)
        self.assertEqual(self._unmerged(), ["dist/index.txt"])  # still unmerged
        self.assertEqual((self.root / "dist" / "index.txt").read_bytes(), before)
        self.assertTrue(sr._merge_in_progress(self.root))


# ── (f) --no-commit -> staged, not finalized ─────────────────────────────────────────
class TestNoCommit(SinkResolverBase):
    def test_no_commit_stages_but_does_not_finalize(self) -> None:
        self._make_sink_only_conflict()
        status, _ = sr.resolve_merge(root=self.root, sinks=["dist/index.txt"], finalize=False)
        self.assertEqual(status, "resolved-staged")
        self.assertTrue(sr._merge_in_progress(self.root))  # merge NOT finalized
        self.assertEqual(self._unmerged(), [])             # but sink is staged (cleared)
        # the staged sink is the merged render
        self.assertEqual((self.root / "dist" / "index.txt").read_text(encoding="utf-8"),
                         _render("A2", "B2"))


# ── (g) byte-oracle fails -> fail-closed, no finalize ────────────────────────────────
class TestByteOracleFailClosed(SinkResolverBase):
    def test_check_cmd_drift_fails_closed(self) -> None:
        self._make_sink_only_conflict()
        # a deliberately-failing oracle (always reports drift) -> resolve must fail closed
        os.environ["SINK_RESOLVER_CHECK_CMD"] = f"{shlex.quote(PY)} -c \"import sys; sys.exit(1)\""
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_ERROR)
        self.assertTrue(sr._merge_in_progress(self.root), "merge must NOT be finalized on a "
                        "failed oracle")

    def test_nondeterministic_generator_caught_by_double_regenerate(self) -> None:
        # no CHECK_CMD -> the weak double-regenerate oracle; a non-det generator must fail closed
        self.root = self.tmp / "repo"  # fresh
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        # rebuild the repo with the non-deterministic generator
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir()
        self._init_repo(gen=GEN_NONDET)
        # make a sink-only conflict by hand using the non-det generator
        r = self.root
        _git(r, "checkout", "-q", "-b", "feat")
        (r / "src" / "b.txt").write_text("B2\n", encoding="utf-8")
        subprocess.run([PY, "gen.py"], cwd=str(r), check=True)
        _git(r, "add", "-A"); _git(r, "commit", "-q", "-m", "feat")
        _git(r, "checkout", "-q", "main")
        (r / "src" / "a.txt").write_text("A2\n", encoding="utf-8")
        subprocess.run([PY, "gen.py"], cwd=str(r), check=True)
        _git(r, "add", "-A"); _git(r, "commit", "-q", "-m", "main")
        _git(r, "merge", "--no-edit", "feat", check=False)
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_ERROR, "a non-deterministic generator must be caught")
        self.assertTrue(sr._merge_in_progress(self.root))


# ── (h) the two oracle modes both succeed on a deterministic generator ───────────────
class TestOracleModes(SinkResolverBase):
    def test_strong_oracle_mode(self) -> None:
        self._make_sink_only_conflict()
        mode = sr.byte_oracle  # smoke the symbol exists
        status, _ = sr.resolve_merge(root=self.root, sinks=["dist/index.txt"])
        self.assertEqual(status, "resolved-finalized")
        self.assertTrue(callable(mode))

    def test_weak_oracle_mode_without_check_cmd(self) -> None:
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        self._make_sink_only_conflict()
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_OK)
        self.assertEqual((self.root / "dist" / "index.txt").read_text(encoding="utf-8"),
                         _render("A2", "B2"))


# ── (i) check: fresh / stale, with and without CHECK_CMD, net non-mutating ───────────
class TestCheck(SinkResolverBase):
    def test_check_fresh(self) -> None:
        self._init_repo()
        fresh, _ = sr.check_fresh(root=self.root, sinks=["dist/index.txt"])
        self.assertTrue(fresh)
        self.assertEqual(self._run("check"), sr.EXIT_OK)

    def test_check_stale(self) -> None:
        self._init_repo()
        # desync: change a source WITHOUT regenerating, then commit (so the sink is stale)
        (self.root / "src" / "a.txt").write_text("A_DRIFT\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "drift the sink")
        fresh, _ = sr.check_fresh(root=self.root, sinks=["dist/index.txt"])
        self.assertFalse(fresh)
        self.assertEqual(self._run("check"), sr.EXIT_ERROR)

    def test_check_without_check_cmd_is_net_non_mutating(self) -> None:
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        self._init_repo()
        before = (self.root / "dist" / "index.txt").read_bytes()
        rc = self._run("check")
        self.assertEqual(rc, sr.EXIT_OK)
        # check regenerated then restored -> working tree unchanged, tree still clean
        self.assertEqual((self.root / "dist" / "index.txt").read_bytes(), before)
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_check_stale_without_check_cmd(self) -> None:
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        self._init_repo()
        (self.root / "src" / "a.txt").write_text("A_DRIFT\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "drift")
        self.assertEqual(self._run("check"), sr.EXIT_ERROR)
        # even on the stale path, the working tree is restored
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout.strip(), "")


# ── (j) disabled -> no-op ────────────────────────────────────────────────────────────
class TestDisabled(SinkResolverBase):
    def test_off_is_noop(self) -> None:
        self._make_sink_only_conflict()
        os.environ["SINK_RESOLVER"] = "off"
        rc = self._run("resolve")
        self.assertEqual(rc, sr.EXIT_OK)
        self.assertEqual(self._unmerged(), ["dist/index.txt"])  # untouched


# ── (k)(m)(n) misconfiguration -> fail-closed ────────────────────────────────────────
class TestMisconfig(SinkResolverBase):
    def test_no_sinks_fails_closed(self) -> None:
        self._make_sink_only_conflict()
        os.environ.pop("SINK_RESOLVER_SINKS", None)
        self.assertEqual(self._run("resolve"), sr.EXIT_ERROR)

    def test_no_generator_fails_closed(self) -> None:
        self._make_sink_only_conflict()
        os.environ.pop("SINK_RESOLVER_GENERATOR_CMD", None)
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        self.assertEqual(self._run("resolve"), sr.EXIT_ERROR)

    def test_sink_outside_repo_fails_closed(self) -> None:
        self._make_sink_only_conflict()
        os.environ["SINK_RESOLVER_SINKS"] = "/etc/hosts"
        with self.assertRaises(sr.ResolveError):
            sr.resolve_merge(root=self.root, sinks=["/etc/hosts"])

    def test_generator_nonzero_fails_closed(self) -> None:
        self._make_sink_only_conflict()
        os.environ["SINK_RESOLVER_GENERATOR_CMD"] = f"{shlex.quote(PY)} -c \"import sys; sys.exit(3)\""
        os.environ.pop("SINK_RESOLVER_CHECK_CMD", None)
        self.assertEqual(self._run("resolve"), sr.EXIT_ERROR)
        self.assertTrue(sr._merge_in_progress(self.root))


# ── (l) normalization: absolute / ./ -prefixed sink matches the repo-relative path ───
class TestNormalization(SinkResolverBase):
    def test_absolute_sink_matches(self) -> None:
        self._make_sink_only_conflict()
        abs_sink = str((self.root / "dist" / "index.txt").resolve())
        keys = sr.normalize_sinks(self.root, [abs_sink])
        self.assertEqual(keys, ["dist/index.txt"])
        # and resolve works when SINKS is the absolute spelling
        os.environ["SINK_RESOLVER_SINKS"] = abs_sink
        self.assertEqual(self._run("resolve"), sr.EXIT_OK)

    def test_dot_slash_and_trailing_variants_fold(self) -> None:
        self._init_repo()
        keys = sr.normalize_sinks(self.root, ["./dist/index.txt", "dist/index.txt"])
        self.assertEqual(keys, ["dist/index.txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
