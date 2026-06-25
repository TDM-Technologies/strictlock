#!/usr/bin/env python3
"""A tiny, real generator to demonstrate generated-sink-prepush-gate.

It renders `SOURCE.txt` into a canonical sink form and writes it to `SINK.txt`.
In `--check` mode it exits non-zero iff the checked-in `SINK.txt` is NOT a
byte-exact match of a fresh render — the universal contract the gate relies on.

This stands in for whatever your real generator is (a manifest builder, an
OpenAPI emitter, an index renderer). The only thing the gate cares about is the
`--check` exit-code contract: 0 = fresh, non-zero = stale.

Usage:
  python3 sample-generator.py            # regenerate SINK.txt from SOURCE.txt
  python3 sample-generator.py --check    # exit 0 if fresh, 1 if stale
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def render(source_text: str) -> str:
    return "GENERATED FROM SOURCE (do not hand-edit):\n" + source_text.upper()


def main() -> int:
    source = (ROOT / "SOURCE.txt").read_text(encoding="utf-8")
    rendered = render(source)
    sink = ROOT / "SINK.txt"

    if "--check" in sys.argv:
        current = sink.read_text(encoding="utf-8") if sink.exists() else ""
        if current != rendered:
            sys.stderr.write("SINK.txt is stale — regenerate it.\n")
            return 1
        return 0

    sink.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
