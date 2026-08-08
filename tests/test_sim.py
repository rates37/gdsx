import pytest

from gdsx import analyse
from gdsx.functions import COMBINATIONAL, base_name
from gdsx.sim import Simulator


def test_base_name_strips_library_and_drive():
    assert base_name("sky130_fd_sc_hd__nand2_2") == "nand2"
    assert base_name("sky130_fd_sc_hd__a21boi_2") == "a21boi"
    assert base_name("sky130_fd_sc_hd__clkbuf_16") == "clkbuf"


@pytest.mark.parametrize(
    "cell,inputs,expected",
    [
        ("nand2", {"A": 1, "B": 1}, 0),
        ("nand2", {"A": 1, "B": 0}, 1),
        ("nor2", {"A": 0, "B": 0}, 1),
        ("xor2", {"A": 1, "B": 0}, 1),
        ("xnor2", {"A": 1, "B": 1}, 1),
        ("mux2", {"A0": 0, "A1": 1, "S": 1}, 1),
        ("mux2", {"A0": 0, "A1": 1, "S": 0}, 0),
        ("a21bo", {"A1": 0, "A2": 0, "B1_N": 0}, 1),
        ("a21boi", {"A1": 1, "A2": 1, "B1_N": 1}, 0),
        ("a31o", {"A1": 1, "A2": 1, "A3": 1, "B1": 0}, 1),
        ("o21bai", {"A1": 1, "A2": 0, "B1_N": 0}, 0),
        ("and4bb", {"A_N": 0, "B_N": 0, "C": 1, "D": 1}, 1),
    ],
)
def test_cell_functions(cell, inputs, expected):
    assert COMBINATIONAL[cell].evaluate(inputs) == expected


def test_chain_fixture_simulates(build, make):
    """Five nands in series, each stage inverts when its B input is high."""
    nl = build(make.chain)
    sim = Simulator(nl)
    hold_b_high = {n: 1 for n in sim.free_nets if n != "head"}
    assert sim.settle({"head": 1, **hold_b_high})["tail"] == 0  # 5 inversions
    assert sim.settle({"head": 0, **hold_b_high})["tail"] == 1


def test_two_gates_fixture_truth_table(build, make):
    nl = build(make.two_gates)
    sim = Simulator(nl)
    nand_b = next(n for n, refs in nl.nets.items() if refs == ["nand2_2_1/B"])
    nor_b = next(n for n, refs in nl.nets.items() if refs == ["nor2_2_1/B"])
    for a, b, other in [(0, 0, 0), (1, 1, 0), (1, 0, 0), (1, 1, 1)]:
        got = sim.settle({"in_a": a, nand_b: b, nor_b: other})["out"]
        assert got == (1 - ((1 - (a & b)) | other))


# testing on the warmup design


def shift_in(sim, a, b, cycles=8):
    """Drive the two serial inputs MSB first with the enable held high"""
    sim.reset()
    for bit in range(cycles - 1, -1, -1):
        values = sim.step(
            {"clk": 0, "en": 1, "rst_n": 1, "A": (a >> bit) & 1, "B": (b >> bit) & 1}
        )
    return values


def test_sample_every_cell_has_a_function(sample_netlist):
    Simulator(sample_netlist)  # raises UnsupportedCell otherwise


def test_sample_clock_tree_is_non_inverting(sample_netlist):
    sim = Simulator(sample_netlist)
    assert set(sim.clock_sense({"rst_n": 1}).values()) == {1}


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (241, 255, 1),
        (248, 248, 1),
        (255, 241, 1),
        (240, 255, 0),
        (255, 255, 0),
        (0, 0, 0),
        (200, 40, 0),
    ],
)
def test_sample_asserts_on_496(sample_netlist, a, b, expected):
    assert shift_in(Simulator(sample_netlist), a, b)["S"] == expected


def test_sample_reset_clears_the_registers(sample_netlist):
    sim = Simulator(sample_netlist)
    shift_in(sim, 241, 255)
    sim.step({"clk": 0, "en": 1, "rst_n": 0, "A": 1, "B": 1})
    assert set(sim.state.values()) == {0}


def test_sample_enable_holds_the_registers(sample_netlist):
    sim = Simulator(sample_netlist)
    shift_in(sim, 241, 255)
    before = dict(sim.state)
    for _ in range(4):
        sim.step({"clk": 0, "en": 0, "rst_n": 1, "A": 1, "B": 1})
    assert sim.state == before


@pytest.mark.slow
def test_sample_matches_the_adder_over_the_whole_input_space(sample_netlist):
    """Sweep all 65536 register states through the combinational cone."""
    nl = sample_netlist
    registers = [r for r in analyse.find_registers(nl) if r.width > 1]
    sim = Simulator(nl)
    for (a, b), out in analyse.sweep(sim, registers, "S", {"en": 1, "rst_n": 1}):
        assert out == int(a + b == 496), f"wrong at a={a} b={b}"
