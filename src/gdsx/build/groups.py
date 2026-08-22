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

Some combinational logic reaches no flop at all going forward -- it sits
*after* the last register on its path, driving only a top-level output port
(puzzle 2's byte-select mux reads the LFSR's 32 bits and drives `O`, with no
flop downstream of it). For that case there is a second pass, breadth-first
*backward* through fan-in, labelling the cell by the nearest flop that feeds
it instead.

A few cells have a flop in neither direction: their whole cone runs from an
input port to an output port without passing through a register. Puzzle 3's
read-mux address decode is the example -- it sits between the `addr` port and
the `O` port, and the register banks it selects between are its *siblings* on
the mux inputs, not its ancestors or its descendants. A third pass finds those
by breadth-first search ignoring direction, which lands such a cell in the
band of whichever register it is nearest to in the netlist. There is no better
answer for a cell shared by eight banks, and a deterministic one is what the
placer needs. A cell that no flop is reachable from even undirected is not
connected to any register at all, which is what the `ValueError` is for.
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


def by_cone(nl, roots: list[tuple[str, str]]) -> dict[str, str]:
    """Instance name -> group label, for cells in a named net's fan-in cone.

    `by_register` labels a combinational cell by the register it is nearest
    to, which is the right answer when the groups the author cares about
    *are* register banks. Puzzle 5's are not: five comparators all read the
    same 32-stage shift register, so every one of their cells is nearest to
    the same bank and they come back as one group. What tells them apart is
    the cone each belongs to, so that is what an author names here.

    `roots` is ordered and the first entry to claim a cell keeps it. The
    comparators share subtrees -- one constant's low half is another's -- so
    some cell always belongs to two cones, and an order is the only way to
    say which group it counts towards. Name the smaller cone first and the
    larger one gets the remainder.

    A root whose driver is a flop is walked from that flop's `D` instead, so
    that `"success"` names the logic that sets a registered output rather
    than the empty cone above the flop itself.
    """
    driver_of: dict[str, str] = {}
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            if pin in OUTPUT_PINS or pin == "Q":
                driver_of[net] = inst.name
    by_name = {i.name: i for i in nl.instances}

    labels: dict[str, str] = {}
    for root, label in roots:
        if root not in driver_of and root not in nl.nets:
            raise ValueError(f"cone root {root!r} is not a net in the netlist")
        seeds = [root]
        driver = driver_of.get(root)
        if driver is not None and is_sequential(by_name[driver].cell):
            seeds = [
                n for p, n in by_name[driver].connections.items() if p == "D"
            ]
        seen: set[str] = set()
        queue = deque(seeds)
        while queue:
            net = queue.popleft()
            if net in seen:
                continue
            seen.add(net)
            name = driver_of.get(net)
            if name is None or is_sequential(by_name[name].cell):
                continue  # a primary input, or the far side of a register
            labels.setdefault(name, label)
            for pin, n in by_name[name].connections.items():
                if pin not in OUTPUT_PINS:
                    queue.append(n)
    return labels


def by_register(nl, groups_by_prefix: dict[str, str]) -> dict[str, str]:
    """Instance name -> group label.

    `groups_by_prefix` maps a register's RTL name to the group it belongs
    to; several register names may share a group, which is what happens when
    a bus is split across two names because part of it is wired straight to
    an output port. A one-bit register has no index on its `Q` net, so its
    RTL name *is* the net name and is looked up as written -- `{"armed":
    "control"}` groups the flop whose `Q` is `armed`.

    Raises `ValueError` rather than guessing: a flop whose `Q` net has no
    recognised prefix, or a cell from which no flop is reachable, means the
    caller's map does not describe this netlist, and silently dropping such
    a cell into a default band would make the layout disagree with the spec
    it claims to implement.
    """
    fanout: dict[str, list[str]] = defaultdict(list)
    driver_of: dict[str, str] = {}
    for inst in nl.instances:
        for net in inst.connections.values():
            fanout[net].append(inst.name)
        for pin, net in inst.connections.items():
            if pin in OUTPUT_PINS or pin == "Q":
                driver_of[net] = inst.name
    by_name = {i.name: i for i in nl.instances}

    labels: dict[str, str] = {}
    for inst in nl.instances:
        if not is_sequential(inst.cell):
            continue
        q = inst.connections.get("Q")
        group = groups_by_prefix.get(register_prefix(q) or q) if q else None
        if group is None:
            raise ValueError(f"{inst.name}: Q net {q!r} has no recognised group prefix")
        labels[inst.name] = group
    flops = set(labels)

    unresolved = []
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
            unresolved.append(inst)
        else:
            labels[inst.name] = found

    # Second pass, backward through fan-in, for cells with no flop downstream
    # -- see the module docstring.
    still: list = []
    for inst in unresolved:
        inputs = [n for p, n in inst.connections.items() if p not in OUTPUT_PINS]
        seen = set(inputs)
        queue = deque(inputs)
        found = None
        while queue and found is None:
            net = queue.popleft()
            driver = driver_of.get(net)
            if driver is None:
                continue
            if driver in flops:
                found = labels[driver]
                break
            for p, n in by_name[driver].connections.items():
                if p not in OUTPUT_PINS and n not in seen:
                    seen.add(n)
                    queue.append(n)
        if found is None:
            still.append(inst)
        else:
            labels[inst.name] = found

    # Third pass, ignoring direction, for cells whose whole cone runs from an
    # input port to an output port without passing through a register -- see
    # the module docstring.
    for inst in still:
        pins = list(inst.connections.values())
        seen = set(pins)
        queue = deque(pins)
        found = None
        while queue and found is None:
            net = queue.popleft()
            for neighbour in fanout.get(net, ()):
                if neighbour in flops:
                    found = labels[neighbour]
                    break
                for n in by_name[neighbour].connections.values():
                    if n not in seen:
                        seen.add(n)
                        queue.append(n)
        if found is None:
            raise ValueError(f"{inst.name}: no flop reachable from any of its pins")
        labels[inst.name] = found

    return labels