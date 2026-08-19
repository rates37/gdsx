"""Bake a compiled gate tape for a GDS, for the web waveform/sequence editor
to fetch -- the same self-contained bundle format `bake_render.py` produces
for the die view (uint32 header length, JSON header, binary op-stream blob).

Usage: uv run python scripts/bake_tape.py samples/puzzle.gds samples/puzzle.tape.bin
"""

import pathlib
import sys

from gdsx import config, loader, netlist
from gdsx.puzzle import _tape_bundle


def main() -> int:
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "samples/puzzle.gds")
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else src.with_suffix(".tape.bin"))

    nl = netlist.build(loader.load(src, config.load()))
    blob = _tape_bundle(nl)
    out.write_bytes(blob)
    print(f"{out}: {len(blob):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())