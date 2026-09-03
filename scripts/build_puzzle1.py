"""Build puzzle 1 (Warm Start)'s design.gds from RTL.

Usage: uv run python scripts/build_puzzle1.py

Equivalent to:

    uv run gdsx puzzle build puzzles/1-warm-start/warm_start.v \\
        --top warm_start --spec puzzles/1-warm-start/layout.json \\
        -o puzzles/1-warm-start/design.gds

This is an author-time wrapper, run by hand to regenerate the puzzle's
committed `design.gds` whenever its RTL or layout spec changes. REFERENCE
(`samples/puzzle.gds`) supplies the standard-cell geometry the generator
copies from -- it is the only self-contained cell library in the repository.
`build()` is run with `check=True`, so the GDS it just wrote is read back and
extracted, and the result compared against the netlist that went in; that is
the only thing that actually proves the generated geometry is right, and it
is worth the extra build time for a script run by hand rather than on
every commit.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from gdsx import synth
from gdsx.build.build import build
from gdsx.build.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "1-warm-start"
REFERENCE = ROOT / "samples" / "puzzle.gds"
SPEC = PUZZLE_DIR / "layout.json"
TOP = "warm_start"


def main() -> int:
    source = (PUZZLE_DIR / f"{TOP}.v").read_text()
    workdir = Path(tempfile.mkdtemp(prefix=f"{TOP}_synth_"))

    nl = synth.from_verilog(source, TOP, workdir)
    spec = load_spec(SPEC, nl)
    report = build(
        source, TOP, spec, PUZZLE_DIR / "design.gds", workdir, REFERENCE, check=True
    )
    for line in report.lines():
        print(line)
    print(f"wrote {report.out_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())