from __future__ import annotations

import pytest
from gdsx import normalise, verify
from gdsx.functions import lookup
from gdsx.netlist import Instance, Netlist, to_generic_verilog

needs_yosys = pytest.mark.skipif(not verify.available(), reason="yosys not installed")


def wire(nl: Netlist) -> Netlist:
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    return nl


def buffered() -> Netlist:
    """a -> buf -> buf -> inv -> y: two buffers that do nothing"""
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("buf_1", "sky130_fd_sc_hd__buf_2", {"A": "a", "X": "n1"}),
        Instance("buf_2", "sky130_fd_sc_hd__clkbuf_4", {"A": "n1", "X": "n2"}),
        Instance("inv_1", "sky130_fd_sc_hd__inv_2", {"A": "n2", "Y": "y"}),
    ]
    nl.ports = {"a": "input", "y": "output"}
    return wire(nl)


def double_inverted() -> Netlist:
    """a -> inv -> inv -> nand(with b) -> y"""
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "n1"}),
        Instance("inv_2", "sky130_fd_sc_hd__inv_2", {"A": "n1", "Y": "n2"}),
        Instance("nand_1", "sky130_fd_sc_hd__nand2_2", {"A": "n2", "B": "b", "Y": "y"}),
    ]
    nl.ports = {"a": "input", "b": "input", "y": "output"}
    return wire(nl)


def tied() -> Netlist:
    """Two tie cells, one of which feeds an AND that can therefore never be 1"""
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("conb_1_1", "sky130_fd_sc_hd__conb_1", {"HI": "hi1", "LO": "lo1"}),
        Instance("conb_1_2", "sky130_fd_sc_hd__conb_1", {"HI": "hi2", "LO": "lo2"}),
        Instance("and_1", "sky130_fd_sc_hd__and2_2", {"A": "lo2", "B": "b", "X": "n1"}),
        Instance("or_1", "sky130_fd_sc_hd__or2_2", {"A": "n1", "B": "hi1", "X": "y"}),
    ]
    nl.ports = {"b": "input", "y": "output"}
    return wire(nl)


def test_buffers_collapse_into_their_source():
    result = normalise.normalise(buffered())
    assert len(result.buffers) == 2
    names = {i.name for i in result.netlist.instances}
    assert names == {"inv_1"}
    (inv,) = result.netlist.instances
    assert inv.connections["A"] == "a", "the inverter now reads the port directly"


def test_a_port_is_never_merged_away():
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [Instance("buf_1", "sky130_fd_sc_hd__buf_2", {"A": "a", "X": "y"})]
    nl.ports = {"a": "input", "y": "output"}
    result = normalise.normalise(wire(nl))

    assert result.buffers == [], "collapsing this would delete the output port"
    assert len(result.netlist.instances) == 1


def test_inverter_pairs_collapse():
    result = normalise.normalise(double_inverted())
    assert len(result.inverter_pairs) == 1
    names = {i.name for i in result.netlist.instances}
    assert "nand_1" in names
    nand = next(i for i in result.netlist.instances if i.name == "nand_1")
    assert nand.connections["A"] == "a"


def test_constants_reach_one_driver_and_fold_forward():
    result = normalise.normalise(tied())

    # The AND has an input tied low, so its output cannot be 1.
    assert any(net == "n1" and value == 0 for _, _, net, value in result.folded)
    # And the OR then has an input tied high, so `y` is constant, but `y` is a
    # port, so its driver stays.
    names = {i.name for i in result.netlist.instances}
    assert "or_1" in names
    assert "and_1" not in names
    assert sum("conb" in i.cell for i in result.netlist.instances) == 1, (
        "one tie cell survives"
    )


def test_every_net_still_has_a_driver():
    """Folding a cell but leaving its readers"""
    result = normalise.normalise(tied())
    nl = result.netlist

    driven = set(nl.ports)
    for inst in nl.instances:
        cell = lookup(inst.cell)
        for pin in cell.outputs if cell else ():
            if pin in inst.connections:
                driven.add(inst.connections[pin])

    for inst in nl.instances:
        cell = lookup(inst.cell)
        outputs = set(cell.outputs) if cell else set()
        for pin, net in inst.connections.items():
            if pin not in outputs and net not in nl.power_nets:
                assert net in driven, f"{inst.name}/{pin} reads undriven {net}"


def test_dangling_cells_are_dropped():
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "y"}),
        Instance("inv_2", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "nobody"}),
    ]
    nl.ports = {"a": "input", "y": "output"}
    result = normalise.normalise(wire(nl))

    assert [name for name, _ in result.dangling] == ["inv_2"]
    assert {i.name for i in result.netlist.instances} == {"inv_1"}


def test_nothing_happens_when_asked_for_nothing():
    result = normalise.normalise(tied(), fold=False, clean=False)
    assert result.folded == []
    assert result.dangling == []


@needs_yosys
@pytest.mark.parametrize(
    "build", [buffered, double_inverted, tied], ids=lambda f: f.__name__
)
def test_normalising_preserves_behaviour(build, tmp_path):
    before = build()
    result = normalise.normalise(before)

    proof = verify.combinational_equivalence(
        to_generic_verilog(before),
        before.top,
        to_generic_verilog(result.netlist),
        result.netlist.top,
        tmp_path,
        tag=build.__name__,
    )
    assert proof.proven, proof.log
