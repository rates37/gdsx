from gdsx.pins import OUTPUT_PINS


def net_of(nl, ref):
    inst, pin = ref.split("/")
    return next(i for i in nl.instances if i.name == inst).connections[pin]


def partition(nl):
    """Nets as a set of frozensets of pin refs"""
    return {frozenset(refs) for refs in nl.nets.values()}


def test_two_gates_exact(build, make):
    nl = build(make.two_gates)
    assert partition(nl) == {
        frozenset({"nand2_2_1/Y", "nor2_2_1/A"}),
        frozenset({"nand2_2_1/VPWR", "nor2_2_1/VPWR"}),
        frozenset({"nand2_2_1/VGND", "nor2_2_1/VGND"}),
        frozenset({"nand2_2_1/A"}),
        frozenset({"nand2_2_1/B"}),
        frozenset({"nor2_2_1/B"}),
        frozenset({"nor2_2_1/Y"}),
    }
    assert not nl.floating and not nl.conflicts


def test_labels_become_named_ports(build, make):
    nl = build(make.two_gates)
    assert nl.ports == {"in_a": "input", "out": "output"}
    assert net_of(nl, "nand2_2_1/A") == "in_a"


def test_mirrored_cell_pins_land_correctly(build, make):
    # The mirrored nor2 exposes B, not A, to the wire from the nand
    nl = build(make.mirrored)
    assert net_of(nl, "nand2_2_1/Y") == net_of(nl, "nor2_2_1/B")
    assert net_of(nl, "nor2_2_1/A") != net_of(nl, "nand2_2_1/Y")


def test_chain_is_a_chain(build, make):
    nl = build(make.chain)
    assert len(nl.instances) == 5
    for i in range(1, 5):
        assert net_of(nl, f"nand2_2_{i}/Y") == net_of(nl, f"nand2_2_{i + 1}/A")
    assert net_of(nl, "nand2_2_1/A") == "head"
    assert net_of(nl, "nand2_2_5/Y") == "tail"
    # 5 power-sharing cells still have distinct signal nets
    assert len(nl.nets) == 13


#! the real (warmup) design


def test_sample_instances_and_ports(sample_netlist):
    nl = sample_netlist
    assert len(nl.instances) == 79
    assert set(nl.ports) == {"A", "B", "S", "clk", "en", "rst_n"}
    assert nl.ports["S"] == "output"
    assert all(nl.ports[p] == "input" for p in ("A", "B", "clk", "en", "rst_n"))


def test_sample_has_no_loose_ends(sample_netlist):
    assert sample_netlist.floating == []
    assert sample_netlist.conflicts == []


def test_sample_every_net_has_exactly_one_driver(sample_netlist):
    nl = sample_netlist
    for net, refs in nl.nets.items():
        if net in nl.power_nets:
            continue
        drivers = [r for r in refs if r.split("/")[1] in OUTPUT_PINS]
        if net in nl.ports and nl.ports[net] == "input":
            assert not drivers, f"input port {net} is driven by {drivers}"
        else:
            assert len(drivers) == 1, f"net {net} has drivers {drivers}"


def test_sample_power_reaches_every_cell(sample_netlist):
    nl = sample_netlist
    assert nl.power_nets == {"VPWR", "VGND"}
    for rail in nl.power_nets:
        assert len(nl.nets[rail]) == len(nl.instances)
