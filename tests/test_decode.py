"""`analysis.decode`: address-decode discovery (`selects`) and orbit
classification (`orbit`)
"""

from __future__ import annotations

import pathlib

import pytest

from gdsx.analysis import decode
from gdsx.core.graph import Graph
from gdsx.core.netlist import Instance, Netlist

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _wire(nl: Netlist) -> None:
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")


#! selects()


def mux_decoder_netlist() -> Netlist:
    """One flop `f` whose D is `sel ? I : f.Q`, it only reacts to `I` when the
    control flop `sf` (driving `sel`) is 1."""
    nl = Netlist(top="dec", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            "mux",
            "sky130_fd_sc_hd__mux2_1",
            {"A0": "q", "A1": "I", "S": "sel", "X": "d"},
        ),
        Instance(
            "sf",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "seld", "RESET_B": "rst_n", "Q": "sel"},
        ),
        Instance(
            "f",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "d", "RESET_B": "rst_n", "Q": "q"},
        ),
    ]
    _wire(nl)
    nl.ports = {"clk": "input", "rst_n": "input", "I": "input", "seld": "input"}
    return nl


def test_selects_finds_the_one_address_that_reacts():
    graph = Graph.of(mux_decoder_netlist())
    hits = decode.selects(
        graph, ["f"], ["sf"], baseline={"seld": 0, "I": 0}, perturb={"I": 1}
    )
    assert hits == [{"sf": 1}]


def test_selects_finds_nothing_when_perturb_never_reaches_the_group():
    graph = Graph.of(mux_decoder_netlist())
    hits = decode.selects(
        graph, ["f"], ["sf"], baseline={"seld": 0}, perturb={"seld": 1}
    )
    assert hits == []


#! orbit()


def toggle_flop_netlist() -> Netlist:
    """A single flop with `D = ~Q`: a 1-bit wrapping counter (0,1,0,1,...)."""
    nl = Netlist(top="toggle", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv", "sky130_fd_sc_hd__inv_1", {"A": "q", "Y": "d"}),
        Instance(
            "f",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "d", "RESET_B": "rst_n", "Q": "q"},
        ),
    ]
    _wire(nl)
    nl.ports = {"clk": "input", "rst_n": "input"}
    return nl


def latch_high_netlist() -> Netlist:
    """A flop with `D = Q | set`: fixed at 0 unless perturbed, then saturates."""
    nl = Netlist(top="latch", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("g_or", "sky130_fd_sc_hd__or2_2", {"A": "q", "B": "set", "X": "d"}),
        Instance(
            "f",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "d", "RESET_B": "rst_n", "Q": "q"},
        ),
    ]
    _wire(nl)
    nl.ports = {"clk": "input", "rst_n": "input", "set": "input"}
    return nl


def ring3_netlist() -> Netlist:
    """A 3-bit rotate: q0 <- q2, q1 <- q0, q2 <- q1. A ring/shift counter."""
    nl = Netlist(top="ring", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            "f0",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "q2", "RESET_B": "rst_n", "Q": "q0"},
        ),
        Instance(
            "f1",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "q0", "RESET_B": "rst_n", "Q": "q1"},
        ),
        Instance(
            "f2",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "q1", "RESET_B": "rst_n", "Q": "q2"},
        ),
    ]
    _wire(nl)
    nl.ports = {"clk": "input", "rst_n": "input"}
    return nl


def test_orbit_classifies_a_stuck_flop_as_fixed_point():
    nl = latch_high_netlist()
    graph = Graph.of(nl)
    got = decode.orbit(graph, ["f"], stimulus={"rst_n": 1, "set": 0})
    assert got.kind == "fixed-point"
    assert got.states == ((0,), (0,))


def test_orbit_classifies_a_toggle_flop_as_wrapping():
    nl = toggle_flop_netlist()
    graph = Graph.of(nl)
    got = decode.orbit(graph, ["f"], stimulus={"rst_n": 1})
    assert got.kind == "wrapping"
    assert got.states == ((0,), (1,), (0,))


def test_orbit_classifies_a_set_then_hold_flop_as_saturating():
    nl = latch_high_netlist()
    graph = Graph.of(nl)
    got = decode.orbit(graph, ["f"], stimulus={"rst_n": 1, "set": 1})
    assert got.kind == "saturating"
    assert got.states == ((0,), (1,), (1,))


def test_orbit_classifies_a_ring_counter_as_shift():
    nl = ring3_netlist()
    graph = Graph.of(nl)
    got = decode.orbit(
        graph, ["f0", "f1", "f2"], stimulus={"rst_n": 1}, start={"f0": 1}
    )
    assert got.kind == "shift"
    assert got.states == ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0))


#! against the real Two Stars pairs

CTRL_LOW = ["dfrtp_2_40", "dfrtp_2_41", "dfrtp_2_47", "dfrtp_2_51"]
CTRL_HIGH = ["dfrtp_2_17", "dfrtp_2_20", "dfrtp_2_25", "dfrtp_2_26"]
CTRL = CTRL_LOW + CTRL_HIGH

# name -> (nhits, low, high)
PAIRS = {
    ("dfrtp_2_22", "dfrtp_2_23"): (7, 1, 2),
    ("dfrtp_2_19", "dfrtp_2_24"): (5, 1, 0),
    ("dfrtp_2_31", "dfrtp_2_30"): (16, 2, 4),
    ("dfrtp_2_28", "dfrtp_2_29"): (23, 0, 0),
    ("dfrtp_2_11", "dfrtp_2_12"): (15, 4, 1),
    ("dfrtp_2_7", "dfrtp_2_8"): (101, 0, 1),
    ("dfrtp_2_13", "dfrtp_2_15"): (8, 4, 12),
    ("dfrtp_2_16", "dfrtp_2_14"): (9, 1, 3),
    ("dfrtp_2_39", "dfrtp_2_36"): (30, 1, 1),
    ("dfrtp_2_32", "dfrtp_2_35"): (36, 0, 3),
    ("dfrtp_2_45", "dfrtp_2_46"): (6, 3, 3),
}


@pytest.mark.slow
def test_selects_reproduces_pairtables_address_table(puzzle_netlist):
    graph = Graph.of(puzzle_netlist)
    for (f, g), (nhits, low, high) in PAIRS.items():
        hits = decode.selects(
            graph, [f, g], CTRL, baseline={"enable": 1, "I": 0}, perturb={"I": 1}
        )
        assert len(hits) == nhits, (f, g)
        addr = hits[0]
        got_low = int("".join(str(addr[c]) for c in CTRL_LOW), 2)
        got_high = int("".join(str(addr[c]) for c in CTRL_HIGH), 2)
        assert (got_low, got_high) == (low, high), (f, g)


@pytest.mark.slow
def test_orbit_confirms_every_pair_is_saturating_not_wrapping(puzzle_netlist):
    """This is the distinction the whole lock depends on: a saturating pair
    only ever means 'at least 2 pulses', never 'exactly 2'."""
    graph = Graph.of(puzzle_netlist)
    for (f, g), (_, low, high) in PAIRS.items():
        addr = {c: 0 for c in CTRL}
        for c, v in zip(CTRL_LOW, format(low, "04b")):
            addr[c] = int(v)
        for c, v in zip(CTRL_HIGH, format(high, "04b")):
            addr[c] = int(v)
        got = decode.orbit(
            graph, [f, g], stimulus={"enable": 1, "I": 1, **addr}, start=addr
        )
        assert got.kind == "saturating", (f, g)
        assert got.states[-1] == (1, 1)
