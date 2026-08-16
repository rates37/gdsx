import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import cone
from harness import NETLIST
from gdsx.functions import lookup, is_sequential

FFS = [i.name for i in NETLIST.instances if is_sequential(i.cell)]


def support(net, seen=None):
    seen = set() if seen is None else seen
    lab = cone.leaf(net)
    if lab is not None:
        return {lab} if lab not in {"0", "1"} else set()
    if net in seen:
        return set()

    _, c, f, connections = cone.gate_of(net)
    cell = lookup(c)
    out = set()
    for p in cell.inputs:
        if p in connections:
            out |= support(connections[p], seen)
    return out


def dsupport(ff):
    d = cone.dpin(ff)
    return support(d) if d else set()


if __name__ == "__main__":
    for f in sorted(FFS, key=lambda s: (s.rsplit("_", 1)[0], int(s.rsplit("_", 1)[1]))):
        s = dsupport(f)
        names = sorted(x.split(".")[0] for x in s)
        print(f"{f}\tD <- {','.join(names)}")
