"""Turn small Verilog modules into netlists, so the analysis can be tested
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from gdsx.netlist import Instance, Netlist

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
    "$_DLATCH_P_": ("dlxtp", {"E": "GATE", "D": "D", "Q": "Q"}),
}

GATES = "AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,ORNOT,MUX,AOI3,OAI3,AOI4,OAI4"

SCRIPT = """\
read_verilog {source}
synth -top {top} -flatten
dfflegalize -cell $_DFF_P_ 0 -cell $_DFF_PN0_ 0 -cell $_DLATCH_P_ 0
abc -g {gates}
opt_clean
write_json {out}
"""


def yosys_available() -> bool:
    return shutil.which("yosys") is not None


def synthesise(source: str, top: str, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / f"{top}.v"
    src.write_text(source)
    out = workdir / f"{top}.json"
    script = workdir / f"{top}.ys"
    script.write_text(SCRIPT.format(source=src, top=top, gates=GATES, out=out))

    proc = subprocess.run(["yosys", "-q", str(script)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yosys failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(out.read_text())


def _net_names(module: dict) -> dict[int, str]:
    """bit id -> readable net name, preferring port and declared names"""
    names: dict[int, str] = {}

    def claim(base: str, bits: list) -> None:
        for i, bit in enumerate(bits):
            if isinstance(bit, int) and bit not in names:
                names[bit] = base if len(bits) == 1 else f"{base}_{i}"

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
        if cell["type"] not in GATE_MAP:
            raise KeyError(f"no mapping for yosys cell {cell['type']}")
        base, pin_map = GATE_MAP[cell["type"]]
        counters[base] = counters.get(base, 0) + 1
        inst = Instance(
            name=f"{base}_{counters[base]}",
            cell=f"sky130_fd_sc_hd__{base}_1",
            connections={
                pin_map[port]: net_of(bits[0]) for port, bits in cell["connections"].items()
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


def from_verilog(source: str, top: str, workdir: Path) -> Netlist:
    return to_netlist(synthesise(source, top, workdir), top)


# the circuit shapes the analysis needs to cope with

COUNTER = """
module counter(input clk, input rst_n, input en, output [7:0] q);
  reg [7:0] c;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) c <= 8'd0; else if (en) c <= c + 1;
  assign q = c;
endmodule
"""

ACCUMULATOR = """
module accumulator(input clk, input rst_n, input [3:0] d, output [3:0] q);
  reg [3:0] acc;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) acc <= 4'd0; else acc <= acc + d;
  assign q = acc;
endmodule
"""

PARALLEL_LOAD = """
module parallel_load(input clk, input rst_n, input load, input [7:0] d, output [7:0] q);
  reg [7:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 8'd0; else if (load) r <= d;
  assign q = r;
endmodule
"""

SHIFT_REGISTER = """
module shifter(input clk, input rst_n, input en, input si, output [7:0] q);
  reg [7:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 8'd0; else if (en) r <= {r[6:0], si};
  assign q = r;
endmodule
"""

TWO_REGISTERS = """
module two_regs(input clk, input rst_n, input a_in, input b_in, output eq);
  reg [3:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else begin a <= {a[2:0], a_in}; b <= {b[2:0], b_in}; end
  assign eq = (a + b == 5'd20);
endmodule
"""
