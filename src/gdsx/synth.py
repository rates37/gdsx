"""Verilog in `Netlist` out, via yosys"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .external import yosys
from .netlist import Instance, Netlist

# yosys gate -> (sky130 cell, {yosys port: sky130 pin})
GATE_MAP = {
    "$_NOT_": ("inv", {"A": "A", "Y": "Y"}),
    "$_BUF_": ("buf", {"A": "A", "Y": "X"}),
    "$_AND_": ("and2", {"A": "A", "B": "B", "Y": "X"}),
    "$_NAND_": ("nand2", {"A": "A", "B": "B", "Y": "Y"}),
    "$_OR_": ("or2", {"A": "A", "B": "B", "Y": "X"}),
    "$_NOR_": ("nor2", {"A": "A", "B": "B", "Y": "Y"}),
    "$_XOR_": ("xor2", {"A": "A", "B": "B", "Y": "X"}),
    "$_XNOR_": ("xnor2", {"A": "A", "B": "B", "Y": "Y"}),
    # A & ~B, so yosys's B is the inverted input of sky130's and2b
    "$_ANDNOT_": ("and2b", {"A": "B", "B": "A_N", "Y": "X"}),
    "$_ORNOT_": ("or2b", {"A": "A", "B": "B_N", "Y": "X"}),
    "$_MUX_": ("mux2", {"A": "A0", "B": "A1", "S": "S", "Y": "X"}),
    "$_NMUX_": ("mux2i", {"A": "A0", "B": "A1", "S": "S", "Y": "Y"}),
    "$_AOI3_": ("a21oi", {"A": "A1", "B": "A2", "C": "B1", "Y": "Y"}),
    "$_OAI3_": ("o21ai", {"A": "A1", "B": "A2", "C": "B1", "Y": "Y"}),
    "$_AOI4_": ("a22oi", {"A": "A1", "B": "A2", "C": "B1", "D": "B2", "Y": "Y"}),
    "$_OAI4_": ("o22ai", {"A": "A1", "B": "A2", "C": "B1", "D": "B2", "Y": "Y"}),
    "$_DFF_P_": ("dfxtp", {"C": "CLK", "D": "D", "Q": "Q"}),
    "$_DFF_PN0_": ("dfrtp", {"C": "CLK", "R": "RESET_B", "D": "D", "Q": "Q"}),
    "$_DFF_PN1_": ("dfstp", {"C": "CLK", "R": "SET_B", "D": "D", "Q": "Q"}),
}

# no ORNOT (-> or2b), no NMUX (-> mux2i): neither has geometry in
# samples/puzzle.gds
GATES = "AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,MUX,AOI3,OAI3,AOI4,OAI4"

# FFs we can map. Everything else has to be legalised into these.
#
# $_DLATCH_P_ stays in this list even though `dlxtp` has no geometry and is
# deliberately absent from GATE_MAP. Legalising latches into a shape we then
# refuse lets `to_netlist` raise a message naming the RTL problem (an
# incompletely-assigned combinational block); dropping it here instead makes
# yosys fail first, with an error about cell types.
LEGALIZE = "dfflegalize -cell $_DFF_P_ 0 -cell $_DFF_PN0_ 0 -cell $_DFF_PN1_ 1 -cell $_DLATCH_P_ 0"

# base cell name -> drive-strength suffix that actually has geometry in
# samples/puzzle.gds. Everything not listed here is "_2"; only these four
# deviate.
DRIVE_SUFFIX = {
    "mux2": "1",
    "conb": "1",
    "decap": "3",
    "tapvpwrvgnd": "1",
}


def cell_name(base: str) -> str:
    return f"sky130_fd_sc_hd__{base}_{DRIVE_SUFFIX.get(base, '2')}"


class UnmappableCell(RuntimeError):
    """yosys produced a cell we cannot place, and why"""


def _unmappable_message(cell: dict) -> str:
    """Say what the author has to change, not just which cell was missing.

    A latch is the common case by a wide margin, and the cause is always in
    the RTL rather than in this module, so name the signal.
    """
    kind = cell["type"]
    if "DLATCH" in kind or "DFFSR" in kind or "SR_" in kind:
        signal = ", ".join(
            sorted(
                str(bits[0])
                for port, bits in cell.get("connections", {}).items()
                if port in ("Q", "Y") and bits
            )
        )
        where = f" (output bit {signal})" if signal else ""
        return (
            f"yosys inferred a latch or an SR element{where}, which has no "
            f"geometry in this cell library. Almost always this is an "
            f"incompletely-assigned combinational always block in the RTL: "
            f"assign every output on every path, or make the block "
            f"`always @(posedge clk)`."
        )
    return (
        f"no mapping for yosys cell {kind}. Either add it to GATE_MAP with a "
        f"sky130 cell that has geometry, or remove whatever gate produces it "
        f"from GATES."
    )


@dataclass(frozen=True)
class Recipe:
    """A way of synthesising a design"""

    name: str
    gates: str = GATES
    # extra yosys commands run before technology mapping
    pre: tuple[str, ...] = ()
    # ABC commands, run instead of ABC's default optimisation. yosys splits
    # these on ';' and turns ',' back into a space, so write `rewrite,-z`
    abc_script: str | None = None
    flatten: bool = True

    def script(self, source: Path, top: str, out: Path) -> str:
        synth = f"synth -top {top}" + (" -flatten" if self.flatten else "")
        lines = [f"read_verilog {source}", synth, *self.pre, LEGALIZE]
        abc = f"abc -g {self.gates}"
        if self.abc_script:
            # A script replaces ABC's default entirely, mapping included,
            # so `map` has to be put back or the result comes out as LUTs
            abc += f" -script +{self.abc_script};map"
        lines += [abc, "opt_clean", f"write_json {out}"]
        return "\n".join(lines) + "\n"


# A spread of recipes to shake out netlists of different shapes for the same
# RTL. `resyn2` and `dc2` are ABC's own rewriting scripts; `share`/`opt -fast`
# change what yosys hands ABC in the first place.
RECIPES = (
    Recipe("default"),
    Recipe("fast", pre=("opt -fast",)),
    # ABC's own resyn2, spelled out, and a cheap depth-oriented alternative
    Recipe(
        "resyn2",
        abc_script="strash;balance;rewrite;refactor;balance;rewrite;rewrite,-z;"
        "balance;refactor,-z;rewrite,-z;balance",
    ),
    Recipe("dc2", abc_script="strash;dc2"),
    Recipe("nand-only", gates="NAND,NOR,ANDNOT,ORNOT"),
    Recipe("simple", gates="AND,OR,NAND,NOR,XOR,XNOR"),
    Recipe("aoi-rich", gates=GATES + ",NMUX"),
    Recipe("shared", pre=("share", "opt")),
)


def yosys_available() -> bool:
    return yosys.available()


def synthesize(
    source: str, top: str, workdir: Path, recipe: Recipe | None = None
) -> dict:
    recipe = recipe or RECIPES[0]
    workdir.mkdir(parents=True, exist_ok=True)
    stem = f"{top}.{recipe.name}"
    src = workdir / f"{top}.v"
    src.write_text(source)
    out = workdir / f"{stem}.json"

    yosys.run(recipe.script(src, top, out))
    return json.loads(out.read_text())


def _net_names(module: dict) -> dict[int, str]:
    """bit id -> readable net name, preferring port and declared names"""
    names: dict[int, str] = {}

    def claim(base: str, bits: list) -> None:
        for i, bit in enumerate(bits):
            if isinstance(bit, int) and bit not in names:
                names[bit] = base if len(bits) == 1 else f"{base}[{i}]"

    for name, port in module["ports"].items():
        claim(name, port["bits"])
    for name, net in module.get("netnames", {}).items():
        if not name.startswith("$"):
            claim(name, net["bits"])
    return names


def to_netlist(design: dict, top: str) -> Netlist:
    module = design["modules"][top]
    names = _net_names(module)

    def net_of(bit) -> str:
        if bit == "0":
            return "VGND"
        if bit == "1":
            return "VPWR"
        return names.get(bit, f"n{bit}")

    nl = Netlist(top=top, power_nets={"VGND", "VPWR"})
    counters: dict[str, int] = {}
    for cell in module["cells"].values():
        if cell["type"] == "$scopeinfo":
            continue  # a marker left where a module boundary used to be
        if cell["type"] not in GATE_MAP:
            raise UnmappableCell(_unmappable_message(cell))
        base, pin_map = GATE_MAP[cell["type"]]
        counters[base] = counters.get(base, 0) + 1
        inst = Instance(
            name=f"{base}_{counters[base]}",
            cell=cell_name(base),
            connections={
                pin_map[port]: net_of(bits[0])
                for port, bits in cell["connections"].items()
            },
        )
        nl.instances.append(inst)

    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    for net in nl.nets:
        nl.nets[net].sort()

    for name, port in module["ports"].items():
        for bit_name in {net_of(b) for b in port["bits"]}:
            if bit_name in nl.nets:
                nl.ports[bit_name] = port["direction"]
    return nl


def from_verilog(
    source: str, top: str, workdir: Path, recipe: Recipe | None = None
) -> Netlist:
    return to_netlist(synthesize(source, top, workdir, recipe), top)
