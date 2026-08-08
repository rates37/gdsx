"""Turn traced connectivity into a named netlist and emit it"""

from __future__ import annotations
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import klayout.db as db

from .connectivity import Connectivity, trace
from .loader import Design
from .pins import OUTPUT_PINS, PinOracle


@dataclass
class Instance:
    name: str
    cell: str
    connections: dict[str, str] = field(default_factory=dict)  # pin -> net name


@dataclass
class Netlist:
    top: str
    instances: list[Instance] = field(default_factory=list)
    nets: dict[str, list[str]] = field(default_factory=dict)  # net -> ["inst/pin", ...]
    ports: dict[str, str] = field(default_factory=dict)  # net name -> direction
    power_nets: set[str] = field(default_factory=set)
    floating: list[str] = field(default_factory=list)  # "inst/pin" with no net
    conflicts: list[str] = field(
        default_factory=list
    )  # "inst/pin" connecting to multiple nets

    def to_dict(self) -> dict:
        return {
            "top": self.top,
            "ports": self.ports,
            "power_nets": sorted(self.power_nets),
            "instances": [
                {"name": i.name, "cell": i.cell, "connections": i.connections}
                for i in self.instances
            ],
            "nets": self.nets,
            "floating": self.floating,
            "conflicts": self.conflicts,
        }


def build(design: Design, conn: Connectivity | None = None) -> Netlist:
    tech = design.tech
    conn = conn or trace(design)
    oracle = PinOracle(design)

    nl = Netlist(top=design.top.name)

    #  collect pin -> net id, keeping instances in a stable placement order
    # A pin often carries several labels, so collect refs in a set, one entry
    # per instance pin
    members: dict[int, set[str]] = defaultdict(set)
    placements = sorted(
        design.instances(), key=lambda t: (t[0], t[1].disp.y, t[1].disp.x)
    )

    counters: dict[str, int] = defaultdict(int)
    for cell_name, trans in placements:
        if not tech.is_logic_cell(cell_name):
            continue
        counters[cell_name] += 1
        short = cell_name.split("__", 1)[1]
        inst = Instance(name=f"{short}_{counters[cell_name]}", cell=cell_name)

        by_pin: dict[str, set[int]] = defaultdict(set)
        for pin in oracle.pins(cell_name):
            net_id = conn.net_at(pin.layer, trans * pin.point)
            if net_id is not None:
                by_pin[pin.name].add(net_id)

        for pin_name in sorted({p.name for p in oracle.pins(cell_name)}):
            ref = f"{inst.name}/{pin_name}"
            found = by_pin.get(pin_name)
            if not found:
                nl.floating.append(ref)
                continue
            if len(found) > 1:
                # Labels for one pin landed on different nets, so either the cell
                # is genuinely shorted or tracing lost a connection
                nl.conflicts.append(ref)
            net_id = min(found)
            inst.connections[pin_name] = net_id  # provisional: id, renamed below
            members[net_id].add(ref)
        nl.instances.append(inst)

    # naming: top-level labels preserved, everything else gets n<id>
    labels = _top_labels(design, conn)
    names = _name_nets(labels, members)

    for inst in nl.instances:
        inst.connections = {p: names[i] for p, i in inst.connections.items()}
    nl.nets = {names[i]: sorted(refs) for i, refs in members.items()}
    nl.power_nets = {names[i] for i in members if names[i] in tech.power_pins}

    # A labelled net reaches the outside world, so it is a port
    for net_id in labels:
        net = names[net_id]
        if net in nl.power_nets or net not in nl.nets:
            continue
        driven = any(ref.split("/")[1] in OUTPUT_PINS for ref in nl.nets[net])
        nl.ports[net] = "output" if driven else "input"

    return nl


def _top_labels(design: Design, conn: Connectivity) -> dict[int, set[str]]:
    """Text labels placed at the top level, resolved to the net they sit on"""
    labels: dict[int, set[str]] = defaultdict(set)
    for rl in design.tech.routing:
        idx = design.index_of(rl.pin)
        if idx is None:
            continue
        for shape in design.top.shapes(idx).each():
            if not shape.is_text():
                continue
            net_id = conn.net_at(rl.name, db.Point(shape.text.x, shape.text.y))
            if net_id is not None:
                labels[net_id].add(shape.text.string)
    return labels


def _name_nets(
    labels: dict[int, set[str]], members: dict[int, list[str]]
) -> dict[int, str]:
    names: dict[int, str] = {}
    taken: set[str] = set()
    for net_id in sorted(set(members) | set(labels)):
        candidates = sorted(labels.get(net_id, ()))
        name = candidates[0] if candidates else f"n{net_id}"
        while name in taken:
            name += "_"
        taken.add(name)
        names[net_id] = name
    return names


# emitters
def to_json(nl: Netlist) -> str:
    return json.dumps(nl.to_dict(), indent=2)


def to_verilog(nl: Netlist) -> str:
    ports = sorted(nl.ports)
    lines = [
        "// generated by gdsx",
        f"module {nl.top} ({', '.join(ports)});",
    ]
    for p in ports:
        lines.append(f"  {nl.ports[p]} {p};")
    wires = sorted(set(nl.nets) - set(ports) - nl.power_nets)
    for chunk in _chunks(wires, 8):
        lines.append("  wire " + ", ".join(chunk) + ";")
    for net in sorted(nl.power_nets):
        lines.append(f"  {'supply0' if net.endswith('GND') else 'supply1'} {net};")
    lines.append("")
    for inst in nl.instances:
        conns = ", ".join(f".{p}({n})" for p, n in sorted(inst.connections.items()))
        lines.append(f"  {inst.cell} {inst.name} ({conns});")
    lines += ["", "endmodule", ""]
    return "\n".join(lines)


def to_dot(nl: Netlist) -> str:
    lines = ["digraph netlist {", "  rankdir=LR;", "  node [shape=box];"]
    for inst in nl.instances:
        lines.append(
            f'  "{inst.name}" [label="{inst.name}\\n{inst.cell.split("__")[1]}"];'
        )
    for net, refs in sorted(nl.nets.items()):
        if net in nl.power_nets:
            continue
        lines.append(f'  "{net}" [shape=ellipse, style=dashed];')
        for ref in refs:
            inst, pin = ref.split("/")
            if pin in {"X", "Y", "Q", "Q_N", "SUM", "COUT"}:
                lines.append(f'  "{inst}" -> "{net}" [label="{pin}"];')
            else:
                lines.append(f'  "{net}" -> "{inst}" [label="{pin}"];')
    lines.append("}")
    return "\n".join(lines)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def write_all(nl: Netlist, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, text in (
        ("json", to_json(nl)),
        ("v", to_verilog(nl)),
        ("dot", to_dot(nl)),
    ):
        path = outdir / f"{nl.top}.{suffix}"
        path.write_text(text)
        written.append(path)
    return written
