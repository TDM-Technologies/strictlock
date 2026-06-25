#!/usr/bin/env python3
r"""projection.py — a deterministic, auditable status projection over a records directory.

WHY THIS EXISTS (the blackboard, made reproducible)
  externalized-memory's shared blackboard is one state file per project — readable at a
  glance, hand-written. That is the right shape for a human handoff. But the moment you
  want a *machine-derived* status view over many small records (one file per work unit,
  per decision, per thread), a hand-merged summary file becomes a last-writer-wins race:
  two sessions appending to it can silently drop a line. The fix is the static-site-generator
  / Maildir pattern — make the summary a GENERATED VIEW over the per-record files, never a
  hand-merged file. Each session writes only its OWN record; the projection is derived, so
  "whoever renders last reads every record and emits the complete view" is the correct
  semantics and transient staleness self-heals on the next render.

  This turns the blackboard from "a place state lives" into "a place state lives PLUS an
  auditable, reproducible projection of it" — the same input records always yield the same
  bytes, so the projection is a pure function of the record set, diffable and replayable.

  Part of StrictLock's externalized-memory module. Standalone — plain script, no daemon,
  no dependencies, standard library only.

THE FIVE PARTS (all required, all proven by the tests)
  1. RECORD SCHEMA. Each record is `<RECORDS_DIR>/<id>.md`: a fenced YAML-ish frontmatter
     block (a fixed, ordered field set) followed by a free-text body. A line-based reader
     parses our own controlled writes — not a general YAML parser. See FIELD_ORDER /
     parse_record / build_record.

  2. DETERMINISTIC RENDER — GIT-FREE and CLOCK-FREE. render_projection() never reads the
     system clock and never shells out to git. Inputs are sorted EXPLICITLY by id
     (filesystem listing order is unstable — the #1 cause of non-determinism), output is
     LF-only bytes, table cells are escaped. Same records => byte-identical output, run
     after run, machine after machine.

  3. FENCED-REGION SPLICING. The projection is written only BETWEEN two marker lines in a
     target file; every byte outside the region is preserved exactly. This lets the
     generated view live inside a hand-maintained document (a README, a STATE file) without
     the generator ever clobbering the prose around it.

  4. CANONICAL-UTC TIMESTAMP GUARD. Human-entered timestamps are the enemy of a reproducible
     render: `2026-06-25T12:00:00+02:00` and `2026-06-25T10:00:00Z` are the same instant but
     different bytes, and a render that normalized them at render time would be reading wall
     context. So the guard runs at WRITE time, not render time: a record's timestamp must
     already be canonical UTC (`YYYY-MM-DDTHH:MM:SSZ`). Non-canonical input is REJECTED — the
     record is never written — so by the time the render runs, every timestamp is already the
     one canonical spelling and the render stays a pure function of bytes on disk.

  5. REQUIRED-BODY VALIDATION. A record with an empty body is rejected at write time. A status
     projection over bodiless records is a table of ids with nothing to say; the body is the
     payload, so an empty one is a malformed record, not an edge case to paper over.

FAIL-CLOSED / NO SILENT FAILURE (the StrictLock posture)
  Every validation path REJECTS loudly with a non-zero exit and a reason on stderr — never a
  silent pass, never a silently-coerced value:
    * a non-canonical timestamp is rejected (RecordValidationError) — not silently normalized,
    * a missing/blank body is rejected (RecordValidationError),
    * a missing required field is rejected,
    * splicing into a file that lacks the configured fence markers is rejected — the generator
      refuses to guess where the region should go and never appends a second copy,
    * the projection write is ATOMIC (temp file in the same dir -> os.replace), so a crash
      mid-write can never leave a torn projection that still looks authoritative.

CONFIGURATION (environment — consistently EM_PROJECTION_-prefixed; see README CONFIG)
  EM_PROJECTION_RECORDS_DIR   Directory holding the per-record `<id>.md` files. REQUIRED for
                              render/check/splice unless --records-dir is passed.
  EM_PROJECTION_TARGET        Projection target file the render is spliced into. REQUIRED for
                              splice/check unless --target is passed.
  EM_PROJECTION_BEGIN         The begin fence marker line. Default:
                              "<!-- BEGIN externalized-memory projection -->".
  EM_PROJECTION_END           The end fence marker line. Default:
                              "<!-- END externalized-memory projection -->".
  EM_PROJECTION_TITLE         Heading rendered at the top of the projection region.
                              Default: "Status projection".
  No machine paths, no project names — every binding comes from the environment or a flag.

SUBCOMMANDS
  validate  Validate one record's text (stdin or a file) against the schema + the canonical-UTC
            and required-body guards. Exit 0 valid, non-zero with a reason otherwise.
  render    Render the projection block for the records dir and print it to stdout. Pure /
            deterministic — no clock, no git.
  splice    Render the block and splice it between the fence markers in the target file,
            preserving every byte outside the region. Atomic write.
  check     The oracle (gofmt -l / prettier --check shape): re-render + re-splice in memory and
            diff against the on-disk target's current region, exit non-zero on drift.

Usage:
  python3 projection.py validate [--file REC.md]            # or pipe the record on stdin
  python3 projection.py render   [--records-dir DIR]
  python3 projection.py splice   [--records-dir DIR] [--target FILE]
  python3 projection.py check    [--records-dir DIR] [--target FILE]

Exit codes: 0 ok / valid / clean; 1 error OR check-drift; 2 usage error;
            3 record validation rejected (non-canonical UTC, missing body, missing field).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

# ── configuration keys (environment) ─────────────────────────────────────────────────
ENV_RECORDS_DIR = "EM_PROJECTION_RECORDS_DIR"
ENV_TARGET = "EM_PROJECTION_TARGET"
ENV_BEGIN = "EM_PROJECTION_BEGIN"
ENV_END = "EM_PROJECTION_END"
ENV_TITLE = "EM_PROJECTION_TITLE"

DEFAULT_BEGIN = "<!-- BEGIN externalized-memory projection -->"
DEFAULT_END = "<!-- END externalized-memory projection -->"
DEFAULT_TITLE = "Status projection"

# ── record schema (part 1) ────────────────────────────────────────────────────────────
# Fixed field order — the writer and the parser both walk this, so the on-disk schema is a
# single source of truth and the render is stable. `id` and `updated` are required and the
# body is required; `status` and `summary` are optional projection columns.
FIELD_ORDER = ("id", "status", "summary", "updated")
REQUIRED_FIELDS = ("id", "updated")
# Fields whose value must be a canonical-UTC timestamp (part 4). Kept as a set so adding a
# second timestamp field later guards it for free.
TIMESTAMP_FIELDS = ("updated",)

# Canonical UTC, and ONLY canonical UTC: `YYYY-MM-DDTHH:MM:SSZ`. No offset, no fractional
# seconds, no lowercase 't'/'z', no space separator. This is the one spelling the render is
# allowed to see, so the render never has to normalize (and so never reads wall context).
_CANONICAL_UTC_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$"
)
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class RecordValidationError(Exception):
    """A record violated the schema / a guard. Carries a human reason; the CLI surfaces it
    and exits 3 (a guard tripped — write nothing), never a silent coercion."""


class ProjectionError(Exception):
    """A render/splice operation could not complete (missing dir, missing fence markers,
    unresolved config). Surfaced loudly; exit 1."""


def _scalar(value: str) -> str:
    """One-line scalar: any newline/tab collapsed to a single space, trimmed. The parser
    splits on the first ': ', so an embedded ':' in a value is safe and needs no quoting —
    we only guarantee the value is single-line."""
    return " ".join((value or "").split())


def validate_canonical_utc(field: str, value: str) -> str:
    """Return `value` unchanged iff it is canonical UTC `YYYY-MM-DDTHH:MM:SSZ`, else raise.

    This is the guard that keeps the render reproducible despite human-entered timestamps
    (part 4). It REJECTS rather than normalizes on purpose: normalizing at write time would
    silently rewrite the author's bytes, and normalizing at render time would make the render
    impure (it would have to know about offsets / DST). Canonical-or-reject means every
    timestamp on disk is already the one true spelling before any render runs.

    The check is purely lexical + a calendar range check — it does NOT read the system clock,
    so two machines in different timezones validate identically."""
    s = (value or "").strip()
    m = _CANONICAL_UTC_RE.match(s)
    if not m:
        raise RecordValidationError(
            f"field {field!r} must be a canonical-UTC timestamp "
            f"'YYYY-MM-DDTHH:MM:SSZ' (UTC, trailing 'Z', no offset, no fractional seconds), "
            f"got {value!r}. Non-canonical timestamps are rejected, not normalized, so the "
            f"projection render stays byte-reproducible."
        )
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    if not (1 <= month <= 12):
        raise RecordValidationError(f"field {field!r}: month out of range in {value!r}")
    max_day = _DAYS_IN_MONTH[month - 1]
    # Reject Feb 29 outside a leap year so a bogus date can't masquerade as canonical.
    if month == 2 and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        max_day = 28
    if not (1 <= day <= max_day):
        raise RecordValidationError(f"field {field!r}: day out of range in {value!r}")
    if hour > 23 or minute > 59 or second > 59:
        raise RecordValidationError(f"field {field!r}: time out of range in {value!r}")
    return s


def build_record(
    *,
    id: str,
    updated: str,
    body: str,
    status: str = "",
    summary: str = "",
) -> str:
    """Render the canonical record text: frontmatter (FIELD_ORDER) + a required free body.
    Pure/deterministic for a given input — no clock, no env, no git. Runs the full validation
    chain so a record can NEVER be built in an invalid state (canonical-UTC timestamps,
    required fields, required body). Raises RecordValidationError on any violation."""
    fields = {
        "id": _scalar(id),
        "status": _scalar(status),
        "summary": _scalar(summary),
        "updated": _scalar(updated),
    }
    validate_fields(fields, body)  # the single gate — both build and `validate` go through it
    lines = ["---"]
    for key in FIELD_ORDER:
        val = fields.get(key, "")
        lines.append(f"{key}:{(' ' + val) if val else ''}")
    lines.append("---")
    frontmatter = "\n".join(lines) + "\n"
    return frontmatter + "\n" + body.strip() + "\n"


def validate_fields(fields: dict, body: str) -> None:
    """The single validation gate, shared by build_record and the `validate` subcommand so the
    contract can't drift between writers. Enforces, in order, all three reject-loudly guards:
      • every REQUIRED_FIELDS key is present and non-empty,
      • every TIMESTAMP_FIELDS value is canonical UTC (part 4),
      • the body is non-empty after stripping (part 5).
    Raises RecordValidationError (exit 3) on the first violation; returns None when valid."""
    for key in REQUIRED_FIELDS:
        if not _scalar(fields.get(key, "")):
            raise RecordValidationError(
                f"missing required field {key!r} — a record must carry every one of "
                f"{', '.join(REQUIRED_FIELDS)}"
            )
    for key in TIMESTAMP_FIELDS:
        if key in fields and _scalar(fields.get(key, "")):
            validate_canonical_utc(key, fields[key])
    if not (body or "").strip():
        raise RecordValidationError(
            "record body is empty — a record MUST carry body content (the projection's "
            "payload is the body; a bodiless record is malformed, not an edge case)"
        )


def parse_record(text: str) -> tuple[dict, str]:
    """Parse a record's frontmatter + body. Returns (fields, body). A minimal line-based reader
    for our own controlled writes — not a general YAML parser. Raises RecordValidationError when
    the fences are missing so a malformed file fails loudly instead of rendering as a blank row."""
    if not text.startswith("---\n"):
        raise RecordValidationError("record missing opening frontmatter fence ('---')")
    end = text.find("\n---", 4)
    if end == -1:
        raise RecordValidationError("record missing closing frontmatter fence ('---')")
    fm_block = text[4:end]
    body = text[end + 4:].lstrip("\n").rstrip("\n")
    fields: dict = {}
    for line in fm_block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(": ")
        if not sep:
            key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


def validate_record_text(text: str) -> dict:
    """Parse + fully validate one record's text. Returns the parsed fields on success; raises
    RecordValidationError on any schema / canonical-UTC / required-body violation. This is the
    exact gate a writer should run BEFORE persisting a record file."""
    fields, body = parse_record(text)
    validate_fields(fields, body)
    return fields


# ── the deterministic render (part 2) — GIT-FREE and CLOCK-FREE ───────────────────────
def list_record_files(records_dir: Path) -> list[Path]:
    """Every top-level `*.md` in the records dir EXCEPT a `_`-prefixed infra file, sorted by
    filename. The explicit sort is load-bearing: filesystem listing order is not stable, and
    that is exactly where determinism would otherwise break. Non-recursive on purpose."""
    if not records_dir.is_dir():
        raise ProjectionError(f"records dir does not exist: {records_dir}")
    return sorted(
        (p for p in records_dir.glob("*.md") if not p.name.startswith("_")),
        key=lambda p: p.name,
    )


def _cell(value: str) -> str:
    """Markdown-table-safe cell: single line, pipes escaped so a value can't forge a column."""
    return _scalar(value).replace("|", r"\|")


def render_projection(records_dir: Path, title: str = DEFAULT_TITLE) -> str:
    """Pure, deterministic render of the projection block from the record files.

    GUARANTEES (proven by the tests): no system-clock read, no git invocation, inputs sorted
    explicitly by id, output LF-only. Calling it twice over an unchanged records dir yields
    byte-identical output. The render VALIDATES every record it reads (validate_record_text),
    so a malformed record fails the render loudly rather than emitting a silently-blank row."""
    rows = []
    for path in list_record_files(records_dir):
        fields = validate_record_text(path.read_text(encoding="utf-8"))
        rows.append(fields)
    # Belt-and-suspenders over the filename sort: order by id explicitly.
    rows.sort(key=lambda f: f.get("id", ""))

    out = [f"## {title}", ""]
    if not rows:
        out.append("_No records yet._")
        return "\n".join(out) + "\n"
    out.append("| id | status | summary | updated |")
    out.append("|----|--------|---------|---------|")
    for f in rows:
        out.append(
            f"| {_cell(f.get('id', ''))} | {_cell(f.get('status', ''))} "
            f"| {_cell(f.get('summary', ''))} | {_cell(f.get('updated', ''))} |"
        )
    return "\n".join(out) + "\n"


# ── fenced-region splicing (part 3) ───────────────────────────────────────────────────
def splice_region(
    document: str, block: str, *, begin: str, end: str
) -> str:
    """Replace the content BETWEEN the `begin` and `end` marker lines in `document` with
    `block`, preserving every byte OUTSIDE the region exactly (including the markers, the
    surrounding prose, and the document's leading/trailing bytes).

    Fail-closed: if either marker is absent, or `end` precedes `begin`, raise ProjectionError —
    the generator REFUSES to guess where the region belongs and never appends a second copy
    (a silent append is how a generated block ends up duplicated forever).

    The markers are matched as whole lines (a line whose stripped text equals the marker), so
    indentation or trailing whitespace around a marker line never hides it. The region is
    rewritten as: <begin-line>\\n<block>\\n<end-line>, with `block`'s own trailing newlines
    normalized to exactly one, so re-splicing an unchanged block is idempotent (byte-stable)."""
    lines = document.splitlines(keepends=True)
    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if begin_idx is None and stripped == begin.strip():
            begin_idx = i
        elif begin_idx is not None and stripped == end.strip():
            end_idx = i
            break
    if begin_idx is None:
        raise ProjectionError(
            f"begin marker not found in target — expected a line equal to {begin!r}. "
            f"Refusing to guess where the projection region goes (a silent append would "
            f"duplicate the block)."
        )
    if end_idx is None:
        raise ProjectionError(
            f"end marker not found after the begin marker — expected a line equal to {end!r}."
        )
    # Preserve the begin/end marker lines verbatim; replace only what is strictly between them.
    before = lines[: begin_idx + 1]
    after = lines[end_idx:]
    # The begin marker line keeps its own newline; if it somehow lacked one (last line of file),
    # add one so the block starts on its own line.
    if before and not before[-1].endswith("\n"):
        before[-1] = before[-1] + "\n"
    body = block.rstrip("\n") + "\n"
    return "".join(before) + body + "".join(after)


# ── atomic write (the externalized-memory write discipline) ───────────────────────────
def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file in the SAME dir + os.replace() — atomic on a
    normal POSIX/APFS filesystem, so a concurrent reader (or a racing splice) never sees a torn
    projection. LF-only bytes. The temp file is removed on any failure before the rename, so a
    failed write never litters a half-written sibling. Same discipline as ../atomic-write.py."""
    directory = path.parent
    if not directory.is_dir():
        raise ProjectionError(
            f"target directory does not exist: {directory} (refusing to silently create it)"
        )
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


# ── splice a rendered projection into the target file ─────────────────────────────────
def project_into_target(
    *,
    records_dir: Path,
    target: Path,
    begin: str,
    end: str,
    title: str,
    write: bool = True,
) -> str:
    """Render the projection for `records_dir` and splice it into `target` between the fence
    markers, preserving all out-of-region bytes. Returns the full spliced document. When
    `write` is True the document is written atomically; when False it is computed only (the
    `check` oracle uses write=False)."""
    if not target.exists():
        raise ProjectionError(
            f"target file does not exist: {target} — create it with the fence markers "
            f"{begin!r} / {end!r} first; the projection only splices, it never creates."
        )
    block = render_projection(records_dir, title=title)
    document = target.read_text(encoding="utf-8")
    spliced = splice_region(document, block, begin=begin, end=end)
    if write:
        atomic_write_text(target, spliced)
    return spliced


def check_target(
    *, records_dir: Path, target: Path, begin: str, end: str, title: str
) -> tuple[bool, str]:
    """The oracle. (ok, message). ok=False when the on-disk target differs from a fresh
    render+splice — i.e. the projection region is stale. Advisory: callers surface it."""
    if not target.exists():
        return False, f"target missing at {target} — run: projection.py splice"
    fresh = project_into_target(
        records_dir=records_dir, target=target, begin=begin, end=end, title=title, write=False
    ).encode("utf-8")
    current = target.read_text(encoding="utf-8").encode("utf-8")
    if current == fresh:
        return True, "ok — target projection region is byte-identical to a fresh render"
    return False, (
        f"DRIFT — {target} projection region is not a fresh render "
        f"(on disk {len(current)} bytes, fresh {len(fresh)} bytes). Run: projection.py splice"
    )


# ── config resolution ─────────────────────────────────────────────────────────────────
def _resolve(explicit: str | None, env_key: str) -> str | None:
    """--flag wins, else $ENV, else None."""
    if explicit:
        return explicit
    return os.environ.get(env_key)


def _require(explicit: str | None, env_key: str, what: str) -> str:
    val = _resolve(explicit, env_key)
    if not val:
        raise ProjectionError(
            f"{what} not set — pass the flag or export {env_key}"
        )
    return val


def _markers_and_title(args) -> tuple[str, str, str]:
    begin = _resolve(getattr(args, "begin", None), ENV_BEGIN) or DEFAULT_BEGIN
    end = _resolve(getattr(args, "end", None), ENV_END) or DEFAULT_END
    title = _resolve(getattr(args, "title", None), ENV_TITLE) or DEFAULT_TITLE
    return begin, end, title


# ── CLI ────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="projection.py",
        description="Deterministic, git-free/clock-free status projection over a records "
                    "directory, spliced into a fenced region of a target file.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="Validate one record's text (stdin or --file).")
    p_val.add_argument("--file", default=None, metavar="REC.md",
                       help="Record file to validate (default: read the record on stdin).")

    p_render = sub.add_parser("render", help="Render the projection block to stdout (pure).")
    p_render.add_argument("--records-dir", default=None, metavar="DIR")
    p_render.add_argument("--title", default=None, metavar="TITLE")

    p_splice = sub.add_parser("splice", help="Render + splice into the target's fenced region.")
    p_splice.add_argument("--records-dir", default=None, metavar="DIR")
    p_splice.add_argument("--target", default=None, metavar="FILE")
    p_splice.add_argument("--begin", default=None, metavar="LINE")
    p_splice.add_argument("--end", default=None, metavar="LINE")
    p_splice.add_argument("--title", default=None, metavar="TITLE")

    p_check = sub.add_parser("check", help="Oracle: fail if the target's region is stale.")
    p_check.add_argument("--records-dir", default=None, metavar="DIR")
    p_check.add_argument("--target", default=None, metavar="FILE")
    p_check.add_argument("--begin", default=None, metavar="LINE")
    p_check.add_argument("--end", default=None, metavar="LINE")
    p_check.add_argument("--title", default=None, metavar="TITLE")

    args = ap.parse_args(argv)

    try:
        if args.cmd == "validate":
            text = (Path(args.file).read_text(encoding="utf-8")
                    if args.file else sys.stdin.read())
            try:
                fields = validate_record_text(text)
            except RecordValidationError as exc:
                print(f"projection: REJECTED: {exc}", file=sys.stderr)
                return 3
            print(f"projection: OK — record {fields.get('id', '?')!r} is valid")
            return 0

        if args.cmd == "render":
            records_dir = Path(_require(args.records_dir, ENV_RECORDS_DIR, "records dir"))
            _begin, _end, title = _markers_and_title(args)
            sys.stdout.write(render_projection(records_dir, title=title))
            return 0

        if args.cmd == "splice":
            records_dir = Path(_require(args.records_dir, ENV_RECORDS_DIR, "records dir"))
            target = Path(_require(args.target, ENV_TARGET, "target file"))
            begin, end, title = _markers_and_title(args)
            project_into_target(records_dir=records_dir, target=target,
                                begin=begin, end=end, title=title, write=True)
            print(f"SPLICED: {target}")
            return 0

        if args.cmd == "check":
            records_dir = Path(_require(args.records_dir, ENV_RECORDS_DIR, "records dir"))
            target = Path(_require(args.target, ENV_TARGET, "target file"))
            begin, end, title = _markers_and_title(args)
            ok, msg = check_target(records_dir=records_dir, target=target,
                                   begin=begin, end=end, title=title)
            print(msg, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
    except RecordValidationError as exc:  # a guard tripped during render/splice
        print(f"projection: REJECTED: {exc}", file=sys.stderr)
        return 3
    except ProjectionError as exc:
        print(f"projection: ERROR: {exc}", file=sys.stderr)
        return 1

    ap.error(f"unknown command {args.cmd!r}")  # unreachable (subparser required)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RecordValidationError as exc:
        print(f"projection: REJECTED: {exc}", file=sys.stderr)
        sys.exit(3)
    except ProjectionError as exc:
        print(f"projection: ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:  # surface any failure non-silently
        print(f"projection: ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
