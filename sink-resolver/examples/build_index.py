#!/usr/bin/env python3
"""A worked example generator — a pure, deterministic render of a sink from its sources.

This is the kind of generator sink-resolver requires: byte-deterministic, with a `--check`
mode (the byte-oracle). It renders `dist/index.md` from the `records/*.md` source files — a
miniature of the externalized-memory projection bundle this module pairs with.

  python3 build_index.py            # regenerate dist/index.md in place
  python3 build_index.py --check    # exit 0 iff dist/index.md is already a fresh render

The render is deterministic: sources are sorted by filename, no wall-clock, no hash-map
iteration order, LF-only. That determinism is exactly what makes the merge-time regenerate
provably correct — the property the resolver's byte-oracle verifies.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "records"
SINK = ROOT / "dist" / "index.md"


def render() -> str:
    lines = ["# Index (GENERATED — do not edit, do not hand-merge)", ""]
    for f in sorted(SRC.glob("*.md")):           # explicit sort = determinism
        body = f.read_text(encoding="utf-8").strip()
        title = body.splitlines()[0] if body else "(empty)"
        lines.append(f"- `{f.name}` — {title}")
    return "\n".join(lines) + "\n"


def main(argv: list) -> int:
    content = render()
    if "--check" in argv:
        current = SINK.read_text(encoding="utf-8") if SINK.exists() else ""
        if current == content:
            return 0
        print(f"build_index: {SINK} is STALE — run `python3 build_index.py` to regenerate.",
              file=sys.stderr)
        return 1
    SINK.parent.mkdir(parents=True, exist_ok=True)
    SINK.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
