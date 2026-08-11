"""Fetch the LEF abstracts for the cells a design uses

uv run python tools/fetch_lef.py samples/sample.gds -o samples/sample_cells.lef

The result is a single multi-MACRO file, which is the shape a PDK ships.
"""

from __future__ import annotations

import argparse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import klayout.db as db

REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/cells"
LIBRARY = "sky130_fd_sc_hd"


def cells_used(gds: Path) -> list[str]:
    layout = db.Layout()
    layout.read(str(gds))
    names = set()
    for cell in layout.each_cell():
        if cell.name.startswith(LIBRARY + "__"):
            names.add(cell.name)
    return sorted(names)


def fetch(cell: str) -> str:
    family = cell.split("__", 1)[1].rsplit("_", 1)[0]
    url = f"{RAW}/{family}/{cell}.lef"
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()


def macro_of(text: str) -> str:
    """Just the MACRO block; the file header is repeated in every file."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("MACRO "))
    end = next(i for i, line in enumerate(lines) if line.startswith("END sky130"))
    return "\n".join(lines[start : end + 1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args()

    cells = cells_used(args.gds)
    print(f"{len(cells)} library cells in {args.gds.name}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        macros = list(pool.map(lambda c: macro_of(fetch(c)), cells))

    header = [
        f"# LEF abstracts for the cells placed in {args.gds.name}",
        f"# fetched from {REPO}",
        "",
        "VERSION 5.5 ;",
        'BUSBITCHARS "[]" ;',
        'DIVIDERCHAR "/" ;',
        "",
    ]
    args.out.write_text("\n".join(header + macros) + "\nEND LIBRARY\n")
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
