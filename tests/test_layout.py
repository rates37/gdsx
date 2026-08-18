"""`analysis.layout.layered`: the Sugiyama layered DAG layout.

Hand-built netlists small enough that the right layer/order answer is
obvious, plus a size check for the `MAX_NODES` cap that the whole point of
this module is to enforce.
"""

from __future__ import annotations

import pytest

from gdsx.analysis import layout
from gdsx.core.netlist import Instance, Netlist


def _wire(nl: Netlist) -> Netlist:
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    return nl


def one_gate_netlist() -> Netlist:
    """A single AND2: `A`, `B` -> `g` -> `X`."""
    nl = Netlist(top="one_gate", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("g", "sky130_fd_sc_hd__and2_2", {"A": "A", "B": "B", "X": "X"}),
    ]
    nl.ports = {"A": "input", "B": "input", "X": "output"}
    return _wire(nl)


def chain_netlist(length: int) -> Netlist:
    """`length` inverters in series: `I -> inv0 -> ... -> O`."""
    nl = Netlist(top="chain", power_nets={"VGND", "VPWR"})
    nets = ["I"] + [f"n{i}" for i in range(length - 1)] + ["O"]
    nl.instances = [
        Instance(f"inv{i}", "sky130_fd_sc_hd__inv_1", {"A": nets[i], "Y": nets[i + 1]})
        for i in range(length)
    ]
    nl.ports = {"I": "input", "O": "output"}
    return _wire(nl)


def sticky_flop_netlist() -> Netlist:
    """`D = Q | (I & en)`: the flop's own output feeds back into its input
    through two combinational gates, a real cycle at gate granularity."""
    nl = Netlist(top="sticky", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("g_and", "sky130_fd_sc_hd__and2_2", {"A": "I", "B": "en", "X": "p"}),
        Instance("g_or", "sky130_fd_sc_hd__or2_2", {"A": "q", "B": "p", "X": "d"}),
        Instance(
            "flop",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "d", "RESET_B": "rst_n", "Q": "q"},
        ),
    ]
    nl.ports = {"clk": "input", "rst_n": "input", "I": "input", "en": "input"}
    return _wire(nl)


def many_unconnected_inverters(n: int) -> Netlist:
    """`n` inverters, each on its own pair of nets -- `3n` layout nodes."""
    nl = Netlist(top="many", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(f"inv{i}", "sky130_fd_sc_hd__inv_1", {"A": f"a{i}", "Y": f"y{i}"})
        for i in range(n)
    ]
    return _wire(nl)


#! layering


def test_a_single_gate_gets_three_layers_source_gate_sink():
    got = layout.layered(one_gate_netlist())
    by_id = {n.id: n for n in got.nodes}
    assert by_id["n:A"].layer == 0
    assert by_id["n:B"].layer == 0
    assert by_id["g:g"].layer == 1
    assert by_id["n:X"].layer == 2
    assert got.n_layers == 3


def test_a_chain_of_gates_gets_strictly_increasing_layers():
    got = layout.layered(chain_netlist(5))
    by_id = {n.id: n for n in got.nodes}
    ordered_ids = [f"g:inv{i}" for i in range(5)]
    layers = [by_id[i].layer for i in ordered_ids]
    assert layers == sorted(layers)
    assert len(set(layers)) == 5  # strictly increasing, one gate per layer


def test_edges_reproduce_the_netlist_connectivity():
    got = layout.layered(one_gate_netlist())
    seen = {(e.source, e.target, e.net) for e in got.edges}
    assert ("n:A", "g:g", "A") in seen
    assert ("n:B", "g:g", "B") in seen
    assert ("g:g", "n:X", "X") in seen
    assert len(got.edges) == 3
    assert all(not e.feedback for e in got.edges)


def test_coordinates_are_monotonic_in_layer_and_order():
    got = layout.layered(chain_netlist(4))
    for node in got.nodes:
        assert node.x == node.layer * layout._LAYER_GAP
        assert node.y == node.order * layout._ROW_GAP


#! cycles


def test_a_sequential_feedback_loop_is_broken_not_rejected():
    got = layout.layered(sticky_flop_netlist())
    # every node still gets a layer, and the graph came back acyclic --
    # nothing raised and every node/edge round-tripped
    assert {n.id for n in got.nodes} == {
        "g:g_and",
        "g:g_or",
        "g:flop",
        "n:I",
        "n:en",
        "n:p",
        "n:q",
        "n:d",
        "n:clk",
        "n:rst_n",
    }
    assert any(e.feedback for e in got.edges), "the Q -> D loop must break somewhere"


def test_feedback_edges_still_carry_their_real_endpoints():
    got = layout.layered(sticky_flop_netlist())
    # 'q' is driven by the flop and read by g_or, both edges exist
    # regardless of which one got marked feedback to break the cycle
    reader = next(e for e in got.edges if e.net == "q" and e.target == "g:g_or")
    driver = next(e for e in got.edges if e.net == "q" and e.source == "g:flop")
    assert reader.source == "n:q"
    assert driver.target == "n:q"


#! the size cap


def test_layered_refuses_more_than_max_nodes(monkeypatch):
    monkeypatch.setattr(layout, "MAX_NODES", 10)
    with pytest.raises(layout.TooManyNodes) as exc:
        layout.layered(many_unconnected_inverters(5))  # 5 gates + 10 nets = 15
    assert exc.value.count == 15


def test_a_design_under_the_cap_is_unaffected():
    got = layout.layered(many_unconnected_inverters(10))  # 30 nodes, well under 200
    assert len(got.nodes) == 30
