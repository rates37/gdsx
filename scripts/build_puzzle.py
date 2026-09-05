"""Seed `puzzles/original-puzzle/` from the artifacts baked under `samples/`.

Usage: uv run python scripts/build_puzzle.py [original-puzzle]

The original puzzle is the one level with no RTL: its `design.gds` is the file
the challenge shipped, and `samples/` is where it and its two baked artifacts
live. This script is the one step that cannot be expressed as "author a level
and bake it" -- it copies those three files into the puzzle directory. Nothing
else. Then, as for every other level:

    uv run gdsx puzzle sync                    # manifest.json, solution.json
    uv run gdsx puzzle bake puzzles/original-puzzle   # the rest, and the zip

**It used to do much more than this, and that was the problem.** It generated
`manifest.json` and `solution.json` from Python literals, which made it a
second copy of prose and of an answer key that six other levels kept in JSON;
it wrote the tape and the zip itself, duplicating `gdsx puzzle bake`; and it
`rmtree`d the puzzle directory first, which by the time hints existed would
have deleted a `hints.json` it could not regenerate, leaving the level
unloadable. Every one of those jobs now belongs to something that does it for
all seven levels the same way. See `src/gdsx/catalog.py`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUZZLE_ID = "original-puzzle"

#: Source under `samples/` -> name inside the puzzle directory. `netlist.json`
#: and `render.bin` are seeded rather than left to `bake` because they are the
#: baked artifacts the rest of the repo already measures against; `bake` will
#: rewrite both from `design.gds` and must produce the same bytes.
SEED = {
    "puzzle.gds": "design.gds",
    "puzzle.netlist.json": "netlist.json",
    "puzzle.render.bin": "render.bin",
}


def seed(puzzle_id: str, out_dir: Path) -> Path:
    if puzzle_id != PUZZLE_ID:
        raise SystemExit(
            f"no seed recipe for {puzzle_id!r} -- every other level is built from "
            f"its RTL with `gdsx puzzle build`"
        )

    stage = out_dir / puzzle_id
    stage.mkdir(parents=True, exist_ok=True)
    for source_name, dest_name in SEED.items():
        source = ROOT / "samples" / source_name
        if not source.exists():
            raise SystemExit(f"missing prerequisite artifact: {source}")
        shutil.copyfile(source, stage / dest_name)
        print(f"  {source.relative_to(ROOT)} -> {(stage / dest_name).relative_to(ROOT)}")
    return stage


def main() -> int:
    puzzle_id = sys.argv[1] if len(sys.argv) > 1 else PUZZLE_ID
    stage = seed(puzzle_id, ROOT / "puzzles")
    print(f"seeded {stage.relative_to(ROOT)}")
    print("next: uv run gdsx puzzle sync")
    print(f"      uv run gdsx puzzle bake {stage.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())