"""Fetch the sky130 cell library's Liberty data and distil it to a form
that the library can use

$ uv run python tools/fetch_liberty.py

Thank you AI for making this into a script :)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"
TREE = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
CORNER = "__tt_025C_1v80.lib.json"  # functions are corner-independent so any will do

OUT = Path(__file__).resolve().parents[1] / "config" / "sky130_fd_sc_hd.cells.json"

KEEP_PIN = ("direction", "function", "three_state")


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.loads(response.read())


def pick_one_per_cell(paths: list[str]) -> dict[str, str]:
    """Lowest drive strength per base cell(they all share a function)."""
    by_cell: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        parts = path.split("/")
        if len(parts) != 3 or parts[0] != "cells":
            continue  # the assembled library file at the top level, not a cell
        by_cell[parts[1]].append(path)
    return {cell: sorted(candidates)[0] for cell, candidates in by_cell.items()}


def distil(raw: dict) -> dict:
    """Keep pin directions/functions and the sequential group"""
    cell: dict = {"pins": {}, "power": []}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        head, _, rest = key.partition(",")
        if head == "pin":
            kept = {k: v for k, v in value.items() if k in KEEP_PIN}
            if kept:
                cell["pins"][rest] = kept
        elif head == "pg_pin":
            cell["power"].append(rest)
        elif head in ("ff", "latch"):
            kept = {k: v for k, v in value.items() if isinstance(v, str)}
            # "ff,IQ,IQ_N" names the internal state variable and its complement
            kept["state_vars"] = rest.split(",")
            cell[head] = kept
    cell["power"].sort()
    return cell


def main() -> int:
    print(f"listing {REPO} ...")
    tree = get(TREE)
    if tree.get("truncated"):
        print("tree listing was truncated, cannot pick cells reliably!", file=sys.stderr)
        return 1

    paths = [e["path"] for e in tree["tree"] if e["path"].endswith(CORNER)]
    chosen = pick_one_per_cell(paths)
    print(f"{len(paths)} liberty files -> {len(chosen)} base cells")

    def fetch(item):
        cell, path = item
        name = Path(path).name.split("__tt_")[0]  # sky130_fd_sc_hd__nand2_1
        return cell, name, distil(get(RAW + path))

    cells = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, (cell, name, data) in enumerate(
            pool.map(fetch, sorted(chosen.items())), 1
        ):
            data["sampled_from"] = name
            cells[cell] = data
            if i % 25 == 0:
                print(f"  {i}/{len(chosen)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "library": "sky130_fd_sc_hd",
                "source": REPO,
                "corner": CORNER,
                "cells": cells,
            },
            indent=1,
            sort_keys=True,
        )
    )
    combinational = sum(1 for c in cells.values() if "ff" not in c and "latch" not in c)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    print(f"  {combinational} combinational, {len(cells) - combinational} sequential")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
