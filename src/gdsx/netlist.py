"""Turn traced connectivity into a named netlist and emit it"""

from __future__ import annotations
import json
import re
from collections import defaultdict
from pathlib import Path
import klayout.db as db

from .connectivity import Connectivity, trace
from .core.netlist import Instance, Netlist  # noqa: F401  (re-exported)
from .functions import generic_name, lookup
from .loader import Design
from .pins import PinOracle, direction_of


def _is_driven(nl: Netlist, net: str) -> bool:
    """True if any cell output pin sits on this net"""
    cells = {inst.name: inst.cell for inst in nl.instances}
    for ref in nl.nets[net]:
        inst, pin = ref.split("/")
        if direction_of(cells[inst], pin) == "output":
            return True
    return False


def build(
    design: Design, conn: Connectivity | None = None, macros: dict | None = None
) -> Netlist:
    """Resolve every instance pin to a net and name the result"""
    tech = design.tech
    oracle = PinOracle(design, macros)
    if conn is None:
        from .pins import abstract_shapes, bond_pins

        conn = trace(design, abstract_shapes(design, oracle))
        bond_pins(design, conn, oracle)

    nl = Netlist(top=design.top.name)

    #  collect pin -> net id, keeping instances in a stable placement order
    # A pin often carries several labels, so collect refs in a set, one entry
    # per instance pin
    members: dict[int, set[str]] = defaultdict(set)
    placements = sorted(
        design.instances(), key=lambda t: (t[0], t[1].disp.y, t[1].disp.x, str(t[1]))
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
        nl.ports[net] = "output" if _is_driven(nl, net) else "input"

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
_VALID = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")


def ident(name: str) -> str:
    """Verilog identifier for a net name

    Layout labels are not constrained to Verilog syntax so anything irregular
    becomes an escaped identifier
    """
    return name if _VALID.match(name) else f"\\{name} "


def to_json(nl: Netlist) -> str:
    return json.dumps(nl.to_dict(), indent=2)


def to_generic_dict(nl: Netlist) -> dict:
    """Like `Netlist.to_dict`, but with library cells replaced by generic primitives

    Same instance names and net names, so this stays diffable against the
    sky130 JSON. Cells the library doesn't describe are dropped, same as
    `to_generic_verilog`.
    """
    d = nl.to_dict()
    instances = []
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        pins = list(cell.inputs) + list(cell.outputs)
        connections = {p: n for p, n in inst.connections.items() if p in pins}
        instances.append(
            {
                "name": inst.name,
                "cell": generic_name(inst.cell),
                "connections": connections,
            }
        )
    d["instances"] = instances
    return d


def to_generic_json(nl: Netlist) -> str:
    return json.dumps(to_generic_dict(nl), indent=2)


def to_verilog(nl: Netlist) -> str:
    ports = sorted(nl.ports)
    lines = [
        "// generated by gdsx",
        f"module {nl.top} ({', '.join(ident(p) for p in ports)});",
    ]
    for p in ports:
        lines.append(f"  {nl.ports[p]} {ident(p)};")
    wires = sorted(set(nl.nets) - set(ports) - nl.power_nets)
    for chunk in _chunks([ident(w) for w in wires], 8):
        lines.append("  wire " + ", ".join(chunk) + ";")
    for net in sorted(nl.power_nets):
        lines.append(
            f"  {'supply0' if net.endswith('GND') else 'supply1'} {ident(net)};"
        )
    lines.append("")
    for inst in nl.instances:
        conns = ", ".join(
            f".{p}({ident(n)})" for p, n in sorted(inst.connections.items())
        )
        lines.append(f"  {inst.cell} {inst.name} ({conns});")
    lines += ["", "endmodule", ""]
    return "\n".join(lines)


def to_generic_verilog(nl: Netlist) -> str:
    """The same netlist with the library cells replaced by generic primitives"""
    ports = sorted(nl.ports)
    lines = [
        "// generated by gdsx: technology-independent view",
        f"module {nl.top} ({', '.join(ident(p) for p in ports)});",
    ]
    for p in ports:
        lines.append(f"  {nl.ports[p]} {ident(p)};")
    wires = sorted(set(nl.nets) - set(ports) - nl.power_nets)
    for chunk in _chunks([ident(w) for w in wires], 8):
        lines.append("  wire " + ", ".join(chunk) + ";")
    lines.append("")
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None:
            lines.append(f"  // no library description for {inst.cell} ({inst.name})")
            continue
        pins = [
            p for p in list(cell.inputs) + list(cell.outputs) if p in inst.connections
        ]
        conns = ", ".join(f".{p}({ident(inst.connections[p])})" for p in pins)
        lines.append(f"  {generic_name(inst.cell)} {inst.name} ({conns});")
    lines += ["", "endmodule", ""]
    return "\n".join(lines)


def to_cone_verilog(
    nl: Netlist,
    name: str,
    instances: set[str],
    rename: dict[str, str],
    outputs: list[str],
) -> str:
    """Emit part of the netlist as a standalone module, for proving in isolation

    `rename` maps boundary nets (register outputs, control signals) to the port
    names the reference model uses, so a miter can match the two by name. Any
    net the cone reads but does not drive becomes an input
    """
    chosen = [inst for inst in nl.instances if inst.name in instances]
    driven, read = set(), set()
    for inst in chosen:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        driven |= {inst.connections[p] for p in cell.functions if p in inst.connections}
        read |= {inst.connections[p] for p in cell.inputs if p in inst.connections}

    port = lambda net: rename.get(net, net)  # noqa: E731
    inputs = sorted(port(n) for n in read - driven - nl.power_nets)
    out_ports = [port(n) for n in outputs]
    internal = sorted({port(n) for n in driven} - set(out_ports))

    lines = [f"module {name} ({', '.join(inputs + out_ports)});"]
    if inputs:
        lines.append(f"  input {', '.join(inputs)};")
    lines.append(f"  output {', '.join(out_ports)};")
    for chunk in _chunks(internal, 8):
        lines.append("  wire " + ", ".join(chunk) + ";")
    lines.append("")
    for inst in chosen:
        cell = lookup(inst.cell)
        pins = [
            p for p in list(cell.inputs) + list(cell.outputs) if p in inst.connections
        ]
        conns = ", ".join(f".{p}({port(inst.connections[p])})" for p in pins)
        lines.append(f"  {generic_name(inst.cell)} {inst.name} ({conns});")
    lines += ["", "endmodule", ""]
    return "\n".join(lines)


def to_dot(nl: Netlist) -> str:
    cells = {inst.name: inst.cell for inst in nl.instances}
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
            if direction_of(cells[inst], pin) == "output":
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
    outputs = (
        ("json", to_json(nl)),
        ("v", to_verilog(nl)),
        ("generic.v", to_generic_verilog(nl)),
        ("generic.json", to_generic_json(nl)),
        ("dot", to_dot(nl)),
    )
    for suffix, text in outputs:
        path = outdir / f"{nl.top}.{suffix}"
        path.write_text(text)
        written.append(path)
    return written
