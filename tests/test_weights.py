"""`analysis.weights`: bit-weight inference by observation"""

from __future__ import annotations

import pytest

from gdsx.analysis import weights
from gdsx.core.netlist import Instance, Netlist


def ripple3() -> Netlist:
    """A plain 3-bit ripple-carry up counter: b0(1), b1(2), b2(4)."""
    nl = Netlist(top="ripple3", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv0", "sky130_fd_sc_hd__inv_1", {"A": "b0", "Y": "nb0"}),
        Instance("xor1", "sky130_fd_sc_hd__xor2_1", {"A": "b0", "B": "b1", "X": "nb1"}),
        Instance(
            "and01", "sky130_fd_sc_hd__and2_2", {"A": "b0", "B": "b1", "X": "carry01"}
        ),
        Instance(
            "xor2i", "sky130_fd_sc_hd__xor2_1", {"A": "carry01", "B": "b2", "X": "nb2"}
        ),
        Instance(
            "f0",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb0", "RESET_B": "rst_n", "Q": "b0"},
        ),
        Instance(
            "f1",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb1", "RESET_B": "rst_n", "Q": "b1"},
        ),
        Instance(
            "f2",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "nb2", "RESET_B": "rst_n", "Q": "b2"},
        ),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"clk": "input", "rst_n": "input"}
    return nl


GROUP = ["f0", "f1", "f2"]
STIMULUS = {"rst_n": 1}


def test_infer_observes_every_weight_when_cycles_reach_the_top_bit():
    got = weights.infer(ripple3(), GROUP, stimulus=STIMULUS, cycles=8)
    assert got["f0"] == weights.Weight(1, "observed")
    assert got["f1"] == weights.Weight(2, "observed")
    assert got["f2"] == weights.Weight(4, "observed")


def test_infer_fills_the_last_flop_by_elimination_when_its_bit_never_sets():
    # cycles=3 never reaches k=4, so f2's weight (4) is never seen one-hot.
    got = weights.infer(ripple3(), GROUP, stimulus=STIMULUS, cycles=3)
    assert got["f0"] == weights.Weight(1, "observed")
    assert got["f1"] == weights.Weight(2, "observed")
    assert got["f2"] == weights.Weight(4, "by_elimination")


def test_infer_reports_unknown_rather_than_guessing_with_two_flops_unresolved():
    # cycles=1 only ever confirms f0; f1 and f2 both stay undetermined, and
    # elimination only applies when exactly one flop is left.
    got = weights.infer(ripple3(), GROUP, stimulus=STIMULUS, cycles=1)
    assert got["f0"] == weights.Weight(1, "observed")
    assert got["f1"].confidence == "unknown"
    assert got["f2"].confidence == "unknown"


@pytest.mark.slow
def test_infer_reproduces_popcounters_documented_derivation(puzzle_netlist):
    group = [
        "dfrtp_2_1",
        "dfrtp_2_2",
        "dfrtp_2_3",
        "dfrtp_2_4",
        "dfrtp_2_5",
        "dfrtp_2_6",
        "dfrtp_2_9",
        "dfrtp_2_10",
    ]
    got = weights.infer(
        puzzle_netlist,
        group,
        stimulus={"clk": 0, "rst_n": 1, "enable": 1, "I": 1},
        cycles=121,
    )
    expected = {
        "dfrtp_2_9": 1,
        "dfrtp_2_2": 2,
        "dfrtp_2_3": 4,
        "dfrtp_2_6": 8,
        "dfrtp_2_1": 16,
        "dfrtp_2_4": 32,
        "dfrtp_2_5": 64,
    }
    for flop, value in expected.items():
        assert got[flop] == weights.Weight(value, "observed"), flop

    assert got["dfrtp_2_10"] == weights.Weight(128, "by_elimination")
