"""Build puzzle 2 (Polynomial)'s design.gds from RTL.

Usage: uv run python scripts/build_puzzle2.py

Equivalent to:

    uv run gdsx puzzle build puzzles/2-polynomial/polynomial.v \\
        --top polynomial --spec puzzles/2-polynomial/layout.json \\
        -o puzzles/2-polynomial/design.gds

See docs/game/layout-guide.md §12 step 4 and docs/game/puzzle-pack.md §2.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from gdsx import synth
from gdsx.build.build import build
from gdsx.build.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "2-polynomial"
REFERENCE = ROOT / "samples" / "puzzle.gds"
SPEC = PUZZLE_DIR / "layout.json"
TOP = "polynomial"


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