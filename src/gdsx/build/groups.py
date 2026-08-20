"""Group labels for a `LayoutSpec`, derived from a flattened netlist.

docs/game/layout-guide.md §9 gets group labels from the RTL's module
hierarchy, by synthesising with `Recipe(flatten=False)` and splitting yosys's
cell names on their module path. That works when there is a hierarchy to
keep. Several puzzles in the pack are a single flat module, and for those §9
names the fallback: "label groups by walking the netlist from each register
bank", which is what this does.

A flop is labelled by the register its `Q` net is named after -- yosys keeps
the RTL's register name where it can. Every other cell is labelled by the
nearest flop its output reaches, breadth-first forwards through the netlist,
so combinational logic lands in the same band as the register it feeds.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

from ..functions import is_sequential

OUTPUT_PINS = ("X", "Y")  # the output pin of every combinational cell in the
# sky130 high-density library that this generator can place

# `acc[7]` is the shape yosys gives a bus bit; `acc_7` is accepted too
# so that a hand-written or differently-synthesised netlist still groups.
_INDEXED = re.compile(r"([A-Za-z][A-Za-z0-9]*)(?:_|\[)(\d+)\]?\Z")


def register_prefix(net: str) -> str | None:
    """`acc[7]` or `acc_7` -> `acc`; anything not of that shape -> None."""
    m = _INDEXED.match(net)
    return m.group(1) if m else None


def by_register(nl, groups_by_prefix: dict[str, str]) -> dict[str, str]:
    """Instance name -> group label.

    `groups_by_prefix` maps a register's RTL name to the group it belongs
    to; several register names may share a group, which is what happens when
    a bus is split across two names because part of it is wired straight to
    an output port.

    Raises `ValueError` rather than guessing: a flop whose `Q` net has no
    recognised prefix, or a cell from which no flop is reachable, means the
    caller's map does not describe this netlist, and silently dropping such
    a cell into a default band would make the layout disagree with the spec
    it claims to implement.
    """
    fanout: dict[str, list[str]] = defaultdict(list)
    for inst in nl.instances:
        for net in inst.connections.values():
            fanout[net].append(inst.name)
    by_name = {i.name: i for i in nl.instances}

    labels: dict[str, str] = {}
    for inst in nl.instances:
        if not is_sequential(inst.cell):
            continue
        q = inst.connections.get("Q")
        group = groups_by_prefix.get(register_prefix(q) or "") if q else None
        if group is None:
            raise ValueError(f"{inst.name}: Q net {q!r} has no recognised group prefix")
        labels[inst.name] = group
    flops = set(labels)

    for inst in nl.instances:
        if inst.name in flops:
            continue
        outputs = [n for p, n in inst.connections.items() if p in OUTPUT_PINS]
        seen = set(outputs)
        queue = deque(outputs)
        found = None
        while queue and found is None:
            net = queue.popleft()
            for consumer in fanout.get(net, ()):
                if consumer in flops:
                    found = labels[consumer]
                    break
                for p, n in by_name[consumer].connections.items():
                    if p in OUTPUT_PINS and n not in seen:
                        seen.add(n)
                        queue.append(n)
        if found is None:
            raise ValueError(f"{inst.name}: no flop reachable from its output")
        labels[inst.name] = found

    return labels