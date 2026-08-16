import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

from harness import NETLIST
from gdsx.functions import lookup, is_sequential
from gdsx import liberty

# lookup for netlist instances by name
BY_NAME = {i.name: i for i in NETLIST.instances}

# net -> (instance, output pin) tuple:
DRIVER = {}
for inst in NETLIST.instances:
    cell = lookup(inst.cell)
    if not cell:
        continue
    for p in cell.functions:
        if p in inst.connections:
            DRIVER[inst.connections[p]] = (inst.name, p)

POWER = set(NETLIST.power_nets)

def leaf(net: str):
    if net in POWER:
        return '1' if not net.endswith('GND') else 0
    if net not in DRIVER:
        return net # input or undriven net
    n, p = DRIVER[net]
    if is_sequential(BY_NAME[n].cell):
        return f'{n}.{p}'
    return None

def expr_of(net, depth=99):
    # returns boolean string for `net`
    lab = leaf(net)
    if lab is not None:
        return lab
    if depth <= 0:
        return net
    n,pin = DRIVER[net]
    inst = BY_NAME[n]
    cell = lookup(inst.cell)
    f = liberty.to_verilog(cell.functions[pin])

    # substitute each pin name with its sub expression:
    def sub(m):
        p = m.group(0)
        if p not in inst.connections:
            return p
        return '(' + expr_of(inst.connections[p], depth-1) + ')'

    pins = sorted(cell.inputs, key=len, reverse=True)
    pat = re.compile(r'\b(' + '|'.join(re.escape(p) for p in pins) + r')\b')
    return pat.sub(sub, f)

def gate_of(net):
    if net not in DRIVER:
        return None
    n,p = DRIVER[net]
    inst = BY_NAME[n]
    cell = lookup(inst.cell)
    return n, inst.cell, liberty.to_verilog(cell.functions[p]), inst.connections

def dpin(ff):
    return BY_NAME[ff].connections.get('D')

def walk(net, depth=3, indent=0, seen=None) -> None:
    # recursive pretty printer for the fan-in walk stopping at FFs/inputs:
    seen = seen if seen else set()

    pad = ' '*indent
    lab = leaf(net)
    if lab:
        print(f"{pad}{net} = <{lab}>")
        return
    n, c, f, connections = gate_of(net)
    ins = {p:v for p,v in connections.items() if p not in (('X','Y','Q','Q_N','VPWR','VGND','VPB','VNB'))}
    print(f'{pad}{net} <- {n} ({c.replace("sky130_fd_sc_hd__", "")})  {f}  {ins}')
    if net in seen or depth <= 0:
        return

    seen.add(net)
    cell = lookup(BY_NAME[n].cell)
    for p in cell.inputs:
        if p in connections:
            walk(connections[p], depth-1, indent+1, seen)
