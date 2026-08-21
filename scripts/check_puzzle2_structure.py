"""Puzzle 2's own bake assertion (docs/game/puzzle-pack.md §2, docs/game/
layout-guide.md §10 point 7 / §12 step 5): the extracted netlist's 32 flops
form a single chain under the Q -> D relation with exactly three XOR
interruptions.

The synthesised design gates every flop's D input with a clock-enable mux
(`D = mux(enable, hold-at-Q, real-next-value)`, since `s <= ...` has no else
branch in the RTL -- see puzzle 2's write-up). That mux is the same at every
one of the 32 flops (same `enable` net on its select, same flop's own Q on
its A0/hold input), so it carries no information about chain order or tap
position; "the Q -> D relation" for this puzzle means the mux's *other*
input (A1, the value loaded when enabled), which is either the previous
stage's Q directly or an XOR of two flops' Q's. This script checks that
relation, not the literal one-hop D driver, and says so rather than silently
redefining what puzzle-pack.md wrote.

The other half of the pack's bake assertion -- 4096 simulated cycles
matching a software Galois LFSR bit for bit -- is
scripts/check_puzzle2_sim.py.

Usage: uv run python scripts/check_puzzle2_structure.py
"""

from __future__ import annotations

from gdsx import config, loader, netlist as N
from gdsx.functions import is_sequential, lookup

PUZZLE_DIR = "puzzles/2-polynomial"


def main() -> int:
    design = loader.load(f"{PUZZLE_DIR}/design.gds", config.load())
    nl = N.build(design)

    flops = [i for i in nl.instances if is_sequential(i.cell)]
    if len(flops) != 32:
        print(f"expected 32 flops, found {len(flops)}")
        return 1

    driver_of: dict[str, object] = {}
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        for pin in cell.outputs:
            if pin in inst.connections:
                driver_of[inst.connections[pin]] = inst

    by_name = {i.name: i for i in flops}
    flop_names = set(by_name)

    predecessor: dict[str, str] = {}  # flop -> flop/tap-pair feeding its D
    taps: dict[str, tuple[str, str]] = {}
    errors: list[str] = []

    for f in flops:
        d_net = f.connections.get("D")
        mux = driver_of.get(d_net)
        if mux is None or "mux2" not in mux.cell:
            errors.append(f"{f.name}: D is driven by {mux.cell if mux else None!r}, "
                           f"not the expected clock-enable mux")
            continue
        a0 = driver_of.get(mux.connections.get("A0"))
        a1 = driver_of.get(mux.connections.get("A1"))
        if a0 is not f:
            errors.append(f"{f.name}: the enable mux's A0 (hold input) is "
                           f"{a0.name if a0 else None!r}, not {f.name} itself")
            continue
        if a1 is None:
            errors.append(f"{f.name}: the enable mux's A1 has no driver")
        elif is_sequential(a1.cell):
            predecessor[f.name] = a1.name
        elif "xor2" in a1.cell:
            ins = [n for p, n in a1.connections.items() if p in ("A", "B")]
            srcs = [driver_of.get(n) for n in ins]
            if len(srcs) != 2 or any(s is None or not is_sequential(s.cell) for s in srcs):
                errors.append(f"{f.name}: tap XOR {a1.name} does not have two flops "
                               f"as its inputs ({ins})")
                continue
            taps[f.name] = (srcs[0].name, srcs[1].name)
        else:
            errors.append(f"{f.name}: the enable mux's A1 is driven by "
                           f"{a1.cell}, expected a flop's Q or an XOR of two flops")

    if errors:
        for e in errors:
            print(e)
        return 1

    if len(taps) != 3:
        print(f"expected exactly 3 XOR-interrupted flops, found {len(taps)}: "
              f"{sorted(taps)}")
        return 1

    # Every tap flop's chain-side input is srcs[0]; fold that into the same
    # predecessor map used for the plain chain links, and confirm the whole
    # thing is a single 32-node cycle.
    last_stage_candidates = set()
    for f, (chain_side, other_side) in taps.items():
        predecessor[f] = chain_side
        last_stage_candidates.add(other_side)

    if len(predecessor) != 32:
        print(f"predecessor map covers {len(predecessor)} of 32 flops")
        return 1

    start = flops[0].name
    visited = [start]
    cur = predecessor[start]
    while cur != start:
        if cur in visited:
            print(f"chain does not close into a single 32-cycle: stuck at "
                  f"{cur} after {len(visited)} steps")
            return 1
        visited.append(cur)
        cur = predecessor.get(cur)
        if cur is None:
            print(f"chain breaks at {visited[-1]}: no predecessor recorded")
            return 1
    if len(visited) != 32:
        print(f"chain closes after {len(visited)} flops, expected all 32")
        return 1

    print("32 flops confirmed")
    print("single chain of 32 under the Q -> D relation (through each flop's "
          "clock-enable mux), closing into one 32-cycle")
    print(f"exactly 3 XOR interruptions: {sorted(taps)}")
    if len(last_stage_candidates) == 1:
        stage = next(iter(last_stage_candidates))
        print(f"all 3 taps' second XOR input is the same flop ({stage}) -- "
              f"the chain's last stage, as puzzle-pack.md §2 describes")
    else:
        print(f"taps' second XOR inputs are NOT all the same flop: "
              f"{sorted(last_stage_candidates)} -- this differs from "
              f"puzzle-pack.md §2's description")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())