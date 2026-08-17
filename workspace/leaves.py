import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from harness import GRAPH


def leaves(net, polarity=1, acc=None):
    acc = [] if acc is None else acc
    lab = GRAPH.label(net)
    if lab:
        acc.append((lab, polarity))
        return acc

    ref = GRAPH.driver_of(net)
    connections = GRAPH.by_name[ref.instance].connections
    short = ref.cell.replace("sky130_fd_sc_hd__", "")
    cell = GRAPH.cell_of[ref.instance]
    if short.startswith("and") and polarity == 1:
        for p in cell.inputs:
            if p in connections:
                leaves(connections[p], 0 if p.endswith("_N") else 1, acc)
    elif short.startswith("inv"):
        leaves(connections["A"], 1 - polarity, acc)

    else:
        acc.append((f"<{net}:{short}:{GRAPH.function_of(net)}>", polarity))
    return acc


if __name__ == "__main__":
    for n in sys.argv[1:]:
        L = leaves(n)
        print(f"{n}: {len(L)} leaves")
        for n, p in L:
            print(f"\t{'' if p else '~'}{n}")
