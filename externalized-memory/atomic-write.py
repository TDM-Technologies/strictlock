#!/usr/bin/env python3
"""atomic-write: write-temp-then-rename, the reference write for an externalized state file.

WHY THIS EXISTS
  An externalized-memory state file (the shared blackboard) is only as trustworthy
  as its worst write. A crash halfway through rewriting it *in place* leaves a
  half-written file — which is worse than a stale one, because it still looks
  authoritative to the next stateless session that reads it. The cure is to never
  touch the destination in place: stage the entire new content in a temp file in
  the SAME directory, flush it to disk, then atomically rename it over the target.
  The rename either happens or it does not; there is no half. A reader sees either
  the complete old file or the complete new one — never a torso.

  Part of StrictLock's externalized-memory module
  (https://github.com/TDM-Technologies/strictlock). Standalone — plain script, no
  daemon, no dependencies, standard library only.

WHAT IT DOES
  Reads the new file content (raw bytes) from STDIN and writes those exact bytes to
  the target path given as the sole argument, atomically:
    1. create a temp file in the SAME directory as the target — same filesystem, so
       the rename is a true atomic rename and not a cross-device copy (which is
       itself non-atomic and interruptible),
    2. write the bytes, flush, and fsync the file descriptor,
    3. os.replace(temp, target) — atomic on POSIX and Windows; overwrites any
       existing target,
    4. best-effort fsync the containing directory (POSIX) so the rename itself is
       durable across a power loss.

FAIL-CLOSED / NO SILENT FAILURE
  The whole point of the pattern is that a write either lands completely or fails
  loudly — a blackboard is not allowed to be quietly wrong. So:
    * the target's parent directory must already exist — a missing parent is an
      error (non-zero exit), never a silently-created path,
    * any IO error aborts with a non-zero exit and a reason on stderr,
    * the temp file is removed on any failure, so a failed write never litters a
      half-written sibling that a later glob might mistake for the real file.
  A write that cannot complete announces itself; it does not no-op.

USAGE
    printf '%s' "$new_content" | python atomic-write.py path/to/STATE.md
    your_state_generator      | python atomic-write.py "$HOME/project/STATE.md"

  Exit status is 0 on a completed write, non-zero (with a stderr reason) otherwise.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def atomic_write(target: Path, data: bytes) -> None:
    """Write `data` to `target` via write-temp-then-rename in the target's directory.

    Raises on any failure (missing parent directory, IO error). The temp file is
    removed if the rename does not complete, so a failed write never leaves a
    partial sibling behind.
    """
    directory = target.parent
    if not directory.is_dir():
        # Fail loudly rather than silently materialize a path the caller did not
        # set up — a surprising mkdir is exactly the kind of quiet behavior the
        # externalized-memory design forbids.
        raise FileNotFoundError(
            f"target directory does not exist: {directory} "
            f"(refusing to silently create it)"
        )

    # Stage in the SAME directory so os.replace is an atomic rename, not a
    # cross-filesystem copy. The leading dot + target name keeps the temp grouped
    # with its target and out of the way of casual listings.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # Atomic on POSIX and Windows; replaces an existing target in one step.
        os.replace(tmp_path, target)
    except BaseException:
        # No silent failure and no litter: drop the temp, then re-raise to fail loud.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    # Best-effort: make the rename itself durable. fsync on a directory handle is
    # POSIX-only; on Windows the open/ fsync simply fails and is safely ignored.
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def main(argv: list) -> int:
    if len(argv) != 2:
        print(
            "usage: python atomic-write.py <target-path>   (new content on stdin)",
            file=sys.stderr,
        )
        return 2
    target = Path(argv[1])
    data = sys.stdin.buffer.read()  # raw bytes — no locale-dependent decoding
    try:
        atomic_write(target, data)
    except Exception as e:  # fail loudly — the caller MUST learn the write didn't land
        # ASCII-only diagnostics: stderr's encoding is the console/locale codec,
        # not necessarily UTF-8, so keep tool output portable.
        print(f"atomic-write: REFUSED: {e}", file=sys.stderr)
        return 1
    print(
        f"atomic-write: wrote {len(data)} byte(s) atomically to {target}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
