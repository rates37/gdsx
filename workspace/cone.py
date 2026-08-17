# the fan-in pretty printer. everything else this file used to hold now lives
# on gdsx.core.graph.Graph: leaf -> label, expr_of -> formula, dpin -> d_pin,
# gate_of -> driver_of + function_of. import GRAPH from harness and call those.

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from harness import GRAPH

# pins a gate listing does not need to repeat: the output it is already
# labelled with, and the supply pins every cell carries
HIDDEN = ("X", "Y", "Q", "Q_N", "VPWR", "VGND", "VPB", "VNB")


def walk(net, depth=3, indent=0) -> None:
    # pretty printer for the fan-in walk stopping at FFs/inputs:
    for level, node in GRAPH.fanin_tree(net, depth=depth).walk():
        pad = " " * (indent + level)
        if node.leaf is not None:
            print(f"{pad}{node.net} = <{GRAPH.label(node.net)}>")
            continue
        connections = GRAPH.by_name[node.driver.instance].connections
        ins = {p: v for p, v in connections.items() if p not in HIDDEN}
        c = node.driver.cell.replace("sky130_fd_sc_hd__", "")
        print(
            f"{pad}{node.net} <- {node.driver.instance} ({c})  {GRAPH.function_of(node.net)}  {ins}"
        )
