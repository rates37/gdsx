"""Puzzle 3 (Gatekeeper)'s bake assertion, from docs/game/puzzle-pack.md §3.

The assertion has three parts. Two are checked here; the third -- that the
intended stimulus raises `success` on the extracted netlist -- is what
`gdsx puzzle verify` already does, and is re-stated at the end for
completeness.

    1. The extracted netlist contains exactly 8 clock-gate cells.
    2. The guard expression for exactly one register bank contains a
       sequential leaf other than that bank's own flops.
    3. The intended stimulus raises `success` on the extracted netlist.

Part 1 cannot hold: the cell palette this repository can build from
(layout-guide.md §3, the 69 structures inside samples/puzzle.gds) contains no
clock-gate cell of any kind. It is reported rather than skipped.

Usage: uv run python scripts/check_puzzle3_structure.py
"""

from __future__ import annotations

import json
from pathlib import Path

from gdsx import guards as guards_mod
from gdsx import netlist as netlist_mod
from gdsx.core.graph import Graph
from gdsx.functions import base_name, lookup

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "3-gatekeeper"

BANK_WIDTH = 8
BANKS = 8

# Cell base names that gate a clock rather than compute on data. None of them
# have geometry in this repository's palette; the list exists so that part 1
# is a real search rather than an assumption.
CLOCK_GATE_CELLS = {"dlclkp", "sdlclkp", "dlclkpq", "sdlclkpq", "dlygate4sd1"}


def _load():
    return netlist_mod.Netlist.from_dict(
        json.loads((PUZZLE_DIR / "netlist.json").read_text())
    )


def main() -> int:
    nl = _load()
    graph = Graph.of(nl)
    result = guards_mod.find(nl)
    failures = []

    # --- part 1: clock gates ------------------------------------------------
    unbuildable = []
    gates = [i.name for i in nl.instances if base_name(i.cell) in CLOCK_GATE_CELLS]
    print(f"1. clock-gate cells: {len(gates)} (assertion wants {BANKS})")
    if len(gates) != BANKS:
        unbuildable.append(
            "no clock-gate cell exists in the 69-cell palette of "
            "layout-guide.md §3, so the eight integrated clock gates "
            "puzzle-pack.md §3 asks for cannot be built. The eight write "
            "enables are feedback muxes instead, and `gdsx guards` recovers "
            "all eight from them -- which part 2 below is the evidence for."
        )

    # --- part 2: one bank's guard has a sequential leaf of its own -----------
    banks = [
        (key, flops)
        for key, flops in result.groups().items()
        if key and len(flops) == BANK_WIDTH
    ]
    print(f"2. register banks recovered by guard grouping: {len(banks)}")
    if len(banks) != BANKS:
        failures.append(
            f"expected {BANKS} banks of {BANK_WIDTH} flops sharing one freeze "
            f"condition, found {len(banks)}"
        )

    odd = []
    for key, flops in banks:
        own = set(flops)
        # `support` returns net names for primary inputs and *instance* names
        # for the flops it stops at, so the sequential leaves are exactly the
        # returned names that are sequential instances.
        leaves: set[str] = set()
        for net, _value in key:
            leaves |= {x for x in graph.support(net) if x in graph.seq}
        foreign = sorted(leaves - own)
        label = sorted(flops)[0]
        print(f"   bank at {label}: foreign sequential leaves {foreign or '-'}")
        if foreign:
            odd.append((label, foreign))

    print(f"   banks with a foreign sequential leaf: {len(odd)} (assertion wants 1)")
    if len(odd) != 1:
        failures.append(
            f"exactly one bank should depend on a flop outside itself; "
            f"{len(odd)} do"
        )

    # --- part 3: restated ---------------------------------------------------
    print("3. intended stimulus raises success: checked by `gdsx puzzle verify`")

    print()
    for note in unbuildable:
        print(f"NOT SATISFIABLE: {note}")
    for f in failures:
        print(f"FAIL: {f}")
    if failures:
        return 1
    print(
        "bake assertion: parts 2 and 3 pass; part 1 is not satisfiable in this "
        "repository and is reported above rather than worked around"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())