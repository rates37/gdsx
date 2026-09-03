"""sequential.sticky(): one-way latch detection, exhaustive over the
D-driver cell's Liberty truth table.

Small hand-built netlists exercise the exhaustive check directly (a pure OR
term, a pure AND term, and no feedback at all).
"""

from __future__ import annotations

import pytest

from gdsx import sequential
from gdsx.core.graph import Graph
from gdsx.core.netlist import Instance, Netlist


def netlist_of(*instances: Instance, ports: dict[str, str] | None = None) -> Netlist:
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"}, ports=dict(ports or {}))
    nl.instances = list(instances)
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    return nl


def gate(name: str, cell: str, **conns: str) -> Instance:
    return Instance(name, f"sky130_fd_sc_hd__{cell}", dict(conns))


def flop(name: str, *, d: str, q: str) -> Instance:
    return gate(name, "dfrtp_2", CLK="clk", D=d, Q=q, RESET_B="rst_n")


def test_sticky_detects_a_pure_or_latch():
    """D = cond | Q: forcing Q high forces D high for every setting of cond"""
    nl = netlist_of(
        flop("f1", d="d1", q="q1"),
        gate("g1", "or2_2", A="cond", B="q1", X="d1"),
        ports={"cond": "input"},
    )
    graph = Graph.of(nl)
    result = sequential.sticky(graph)

    assert len(result) == 1
    (got,) = result
    assert got.flop == "f1"
    assert got.polarity == 1
    assert got.condition == "A"


def test_sticky_detects_a_pure_and_latch():
    """D = cond & Q: forcing Q low forces D low for every setting of cond"""
    nl = netlist_of(
        flop("f2", d="d2", q="q2"),
        gate("g2", "and2_2", A="cond", B="q2", X="d2"),
        ports={"cond": "input"},
    )
    graph = Graph.of(nl)
    result = sequential.sticky(graph)

    assert len(result) == 1
    (got,) = result
    assert got.flop == "f2"
    assert got.polarity == 0
    assert got.condition == "A"


def test_sticky_finds_a_companion_cube_when_the_bare_pin_is_not_decisive():
    """D = (A1 & A2 & A3) | (B1 & B2), B2 = own Q: B2 alone never decides the
    output (B1 might be 0), but B2 & B1 does, for every A1/A2/A3. This is the
    exact shape `success` turned out to have in Two Stars: the feedback pin
    needs one companion, not zero, before it is a genuine OR term."""
    nl = netlist_of(
        flop("f3", d="d3", q="q3"),
        gate(
            "g3",
            "a32o_2",
            A1="a1",
            A2="a2",
            A3="a3",
            B1="b1",
            B2="q3",
            X="d3",
        ),
        ports={"a1": "input", "a2": "input", "a3": "input", "b1": "input"},
    )
    graph = Graph.of(nl)
    result = sequential.sticky(graph)

    assert len(result) == 1
    (got,) = result
    assert got.flop == "f3"
    assert got.polarity == 1
    assert got.condition == "((A1 & A2) & A3)"


def test_sticky_ignores_a_flop_with_no_feedback():
    nl = netlist_of(
        flop("f4", d="d4", q="q4"),
        gate("g4", "buf_2", A="cond", X="d4"),
        ports={"cond": "input"},
    )
    graph = Graph.of(nl)
    assert sequential.sticky(graph) == []


def test_sticky_ignores_a_flop_fed_by_someone_elses_q():
    """Feedback from a different flop's Q is not self-latching"""
    nl = netlist_of(
        flop("f5", d="d5", q="q5"),
        flop("f6", d="d6", q="q6"),
        gate("g5", "or2_2", A="cond2", B="q6", X="d5"),
        gate("g6", "buf_2", A="cond", X="d6"),
        ports={"cond": "input", "cond2": "input"},
    )
    graph = Graph.of(nl)
    assert sequential.sticky(graph) == []


@pytest.mark.slow
def test_sticky_finds_success_and_both_traps_on_the_puzzle(puzzle_netlist):
    graph = Graph.of(puzzle_netlist)
    result = sequential.sticky(graph)
    by_flop = {s.flop: s for s in result}

    for name in ("dfrtp_2_83", "dfrtp_2_18", "dfrtp_2_50"):
        assert name in by_flop, f"{name} should be detected as sticky"
        assert by_flop[name].polarity == 1


@pytest.mark.slow
def test_sticky_does_not_classify_checkpoint_vs_trap(puzzle_netlist):
    graph = Graph.of(puzzle_netlist)
    result = sequential.sticky(graph)

    assert result, "sanity: puzzle.gds has sticky flops to check"
    fields = {f for s in result for f in vars(s)}
    assert fields == {"flop", "polarity", "condition"}
