from __future__ import annotations

from gdsx import guards
from gdsx.netlist import Instance, Netlist


def wire(nl: Netlist) -> Netlist:
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    return nl


def enabled_flop(select: str = "en", extra: int = 3) -> Netlist:
    """q <= en ? d : q, built the way synthesis builds it, as a mux

    `extra` spare readers on the select exist only so the net clears the
    fan-out threshold, exactly as a real broadcast enable would.
    """
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            "mux_1",
            "sky130_fd_sc_hd__mux2_1",
            {"A0": "q", "A1": "d", "S": select, "X": "n1"},
        ),
        Instance(
            "dff_1", "sky130_fd_sc_hd__dfxtp_1", {"CLK": "clk", "D": "n1", "Q": "q"}
        ),
    ]
    for i in range(extra):
        nl.instances.append(
            Instance(
                f"pad_{i}", "sky130_fd_sc_hd__inv_2", {"A": select, "Y": f"spare{i}"}
            )
        )
    nl.ports = {"d": "input", "clk": "input", select: "input", "q": "output"}
    return wire(nl)


def test_the_enable_is_recovered_from_a_mux():
    result = guards.find(enabled_flop())
    found = result.of("dff_1")

    assert [(g.net, g.value) for g in found] == [("en", 0)], (
        "frozen exactly when en is low"
    )


def test_an_enable_built_from_gates_is_found_too():
    """After mapping there is no mux cell left

    (en & d) | (~en & q) is an AO22 with an inverter, and nothing about it
    looks like a mux until you assume a value for `en`
    """
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_2", {"A": "en", "Y": "nen"}),
        Instance(
            "ao22_1",
            "sky130_fd_sc_hd__a22o_2",
            {"A1": "en", "A2": "d", "B1": "nen", "B2": "q", "X": "n1"},
        ),
        Instance(
            "dff_1", "sky130_fd_sc_hd__dfxtp_1", {"CLK": "clk", "D": "n1", "Q": "q"}
        ),
        Instance("pad_1", "sky130_fd_sc_hd__inv_2", {"A": "en", "Y": "s1"}),
        Instance("pad_2", "sky130_fd_sc_hd__inv_2", {"A": "en", "Y": "s2"}),
    ]
    nl.ports = {"d": "input", "clk": "input", "en": "input", "q": "output"}
    result = guards.find(wire(nl))

    assert ("en", 0) in [(g.net, g.value) for g in result.of("dff_1")]


def test_an_ungated_flop_reports_no_guard():
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_2", {"A": "d", "Y": "n1"}),
        Instance(
            "dff_1", "sky130_fd_sc_hd__dfxtp_1", {"CLK": "clk", "D": "n1", "Q": "q"}
        ),
        Instance("pad_1", "sky130_fd_sc_hd__inv_2", {"A": "d", "Y": "s1"}),
        Instance("pad_2", "sky130_fd_sc_hd__inv_2", {"A": "d", "Y": "s2"}),
        Instance("pad_3", "sky130_fd_sc_hd__inv_2", {"A": "d", "Y": "s3"}),
    ]
    nl.ports = {"d": "input", "clk": "input", "q": "output"}
    result = guards.find(wire(nl))

    assert result.of("dff_1") == []
    assert result.ungated == ["dff_1"]


def test_a_flop_wired_d_to_q_is_not_reported_as_gated():
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance(
            "dff_1", "sky130_fd_sc_hd__dfxtp_1", {"CLK": "clk", "D": "q", "Q": "q"}
        ),
        Instance("pad_1", "sky130_fd_sc_hd__inv_2", {"A": "x", "Y": "s1"}),
        Instance("pad_2", "sky130_fd_sc_hd__inv_2", {"A": "x", "Y": "s2"}),
        Instance("pad_3", "sky130_fd_sc_hd__inv_2", {"A": "x", "Y": "s3"}),
        Instance("pad_4", "sky130_fd_sc_hd__inv_2", {"A": "x", "Y": "s4"}),
    ]
    nl.ports = {"clk": "input", "x": "input", "q": "output"}
    result = guards.find(wire(nl))

    assert result.guards == []


def test_flops_sharing_an_enable_are_grouped():
    nl = enabled_flop()
    second = Netlist(top="t")
    second.instances = [
        Instance(
            "mux_2",
            "sky130_fd_sc_hd__mux2_1",
            {"A0": "q2", "A1": "d", "S": "en", "X": "n2"},
        ),
        Instance(
            "dff_2", "sky130_fd_sc_hd__dfxtp_1", {"CLK": "clk", "D": "n2", "Q": "q2"}
        ),
    ]
    nl.instances += second.instances
    nl.ports["q2"] = "output"
    result = guards.find(wire(nl))

    groups = result.groups()
    assert groups[(("en", 0),)] == ["dff_1", "dff_2"]
