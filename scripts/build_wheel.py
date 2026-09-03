"""Build the browser wheel, with the repo's `config/` data files inside it.

    uv run python scripts/build_wheel.py

`uv build` alone produces a wheel that imports but cannot analyse anything:
`config/sky130.yaml` and `config/sky130_fd_sc_hd.cells.json` live at the repo
root, above the package, so they are not packaged and `gdsx.datafiles` finds
nothing at `site-packages/gdsx/data/`. This copies them into the package
first (the copy is gitignored) and removes it afterwards, so the checkout is
left exactly as it was and the repo keeps one copy of each data file.
"""

import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "config"
DST = ROOT / "src" / "gdsx" / "data"
WANTED = ("sky130.yaml", "sky130.json", "sky130_fd_sc_hd.cells.json")


def main() -> int:
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    for name in WANTED:
        shutil.copy2(SRC / name, DST / name)
    try:
        proc = subprocess.run(["uv", "build", *sys.argv[1:]], cwd=ROOT)
    finally:
        shutil.rmtree(DST)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
