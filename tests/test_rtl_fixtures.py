"""The yosys-based fixture generator, and the gate mapping it depends on"""

from itertools import product

import pytest

import rtl_fixtures
from gdsx.functions import base_name
from gdsx.liberty import library
from gdsx.sim import Simulator

needs_yosys = pytest.mark.skipif(
    not rtl_fixtures.yosys_available(), reason="yosys not installed"
)

# What each yosys gate means
YOSYS_SEMANTICS = {
    "$_NOT_": (("A",), lambda a: 1 - a),
    "$_BUF_": (("A",), lambda a: a),
    "$_AND_": (("A", "B"), lambda a, b: a & b),
    "$_NAND_": (("A", "B"), lambda a, b: 1 - (a & b)),
    "$_OR_": (("A", "B"), lambda a, b: a | b),
    "$_NOR_": (("A", "B"), lambda a, b: 1 - (a | b)),
    "$_XOR_": (("A", "B"), lambda a, b: a ^ b),
    "$_XNOR_": (("A", "B"), lambda a, b: 1 - (a ^ b)),
    "$_ANDNOT_": (("A", "B"), lambda a, b: a & (1 - b)),
    "$_ORNOT_": (("A", "B"), lambda a, b: a | (1 - b)),
    "$_MUX_": (("A", "B", "S"), lambda a, b, s: b if s else a),
    "$_NMUX_": (("A", "B", "S"), lambda a, b, s: 1 - (b if s else a)),
    "$_AOI3_": (("A", "B", "C"), lambda a, b, c: 1 - ((a & b) | c)),
    "$_OAI3_": (("A", "B", "C"), lambda a, b, c: 1 - ((a | b) & c)),
    "$_AOI4_": (("A", "B", "C", "D"), lambda a, b, c, d: 1 - ((a & b) | (c & d))),
    "$_OAI4_": (("A", "B", "C", "D"), lambda a, b, c, d: 1 - ((a | b) & (c | d))),
}


@pytest.mark.parametrize("gate", sorted(YOSYS_SEMANTICS))
def test_gate_mapping_matches_the_library(gate):
    inputs, fn = YOSYS_SEMANTICS[gate]
    base, pin_map = rtl_fixtures.GATE_MAP[gate]
    cell = library()[base]
    output = pin_map["Y"]

    for combo in product((0, 1), repeat=len(inputs)):
        values = {pin_map[pin]: bit for pin, bit in zip(inputs, combo)}
        assert cell.evaluate(values)[output] == fn(*combo), (
            f"{gate} -> {base} differs at {values}"
        )


def test_every_mapped_cell_exists():
    for gate, (base, pin_map) in rtl_fixtures.GATE_MAP.items():
        cell = library()[base]
        mapped = set(pin_map.values())
        assert mapped <= set(cell.inputs) | set(cell.outputs), (
            f"{gate} -> {base} pin names"
        )


def test_sequential_mappings():
    # Flop mappings can't be truth-tabled, so check their structure instead
    dff = library()[rtl_fixtures.GATE_MAP["$_DFF_P_"][0]]
    assert dff.is_sequential and dff.sequential.clear is None

    dffr = library()[rtl_fixtures.GATE_MAP["$_DFF_PN0_"][0]]
    # yosys's $_DFF_PN0_ resets on a low R, which is sky130's active-low RESET_B
    assert dffr.sequential.clear == ("not", ("var", "RESET_B"))


@needs_yosys
def test_counter_counts(tmp_path):
    nl = rtl_fixtures.from_verilog(rtl_fixtures.COUNTER, "counter", tmp_path)
    sim = Simulator(nl)
    for expected in range(1, 12):
        values = sim.step({"clk": 0, "rst_n": 1, "en": 1})
        assert sum(values[f"q_{b}"] << b for b in range(8)) == expected


@needs_yosys
def test_accumulator_accumulates(tmp_path):
    nl = rtl_fixtures.from_verilog(rtl_fixtures.ACCUMULATOR, "accumulator", tmp_path)
    sim = Simulator(nl)
    total = 0
    for step in (3, 5, 1, 7):
        values = sim.step(
            {"clk": 0, "rst_n": 1, **{f"d_{b}": (step >> b) & 1 for b in range(4)}}
        )
        total = (total + step) % 16
        assert sum(values[f"q_{b}"] << b for b in range(4)) == total


@needs_yosys
def test_synthesised_cells_are_all_mapped(tmp_path):
    for source, top in (
        (rtl_fixtures.COUNTER, "counter"),
        (rtl_fixtures.ACCUMULATOR, "accumulator"),
        (rtl_fixtures.PARALLEL_LOAD, "parallel_load"),
        (rtl_fixtures.SHIFT_REGISTER, "shifter"),
        (rtl_fixtures.TWO_REGISTERS, "two_regs"),
    ):
        nl = rtl_fixtures.from_verilog(source, top, tmp_path)
        for inst in nl.instances:
            assert base_name(inst.cell) in library()
