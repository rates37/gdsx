from __future__ import annotations

import pytest
from gdsx import region
from gdsx.netlist import Instance, Netlist


def chain() -> tuple[Netlist, dict[str, tuple[float, float]]]:
    """Two clumps of logic far apart, joined by a single net

    left:  a -> inv1 -> n1 -> inv2 -> mid
    right: mid -> inv3 -> n3 -> inv4 -> y
    """
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv1", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "n1"}),
        Instance("inv2", "sky130_fd_sc_hd__inv_2", {"A": "n1", "Y": "mid"}),
        Instance("inv3", "sky130_fd_sc_hd__inv_2", {"A": "mid", "Y": "n3"}),
        Instance("inv4", "sky130_fd_sc_hd__inv_2", {"A": "n3", "Y": "y"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"a": "input", "y": "output"}
    points = {
        "inv1": (0.0, 0.0),
        "inv2": (5.0, 0.0),
        "inv3": (100.0, 0.0),
        "inv4": (105.0, 0.0),
    }
    return nl, points


def test_a_box_selects_what_is_inside_it():
    nl, points = chain()
    got = region.within(points, region.Box(-1, -1, 50, 1))
    assert got == ["inv1", "inv2"]


def test_a_cell_belongs_to_the_block_its_centre_is_in():
    points = {"wide": (9.0, 0.0)}
    extents = {"wide": (10.0, 2.72)}
    left = region.Box(0, -1, 10, 5)
    right = region.Box(10, -1, 30, 5)

    assert region.within(points, left, extents) == []
    assert region.within(points, right, extents) == ["wide"], (
        "centre is at 14, so it is right"
    )


def test_crossing_nets_become_ports():
    nl, points = chain()
    carved = region.extract(nl, points, region.Box(-1, -1, 50, 1))

    assert sorted(carved.inside) == ["inv1", "inv2"]
    assert "a" in carved.inputs
    assert "mid" in carved.outputs, "read outside, so it leaves the block"
    assert carved.internal == ["n1"], "n1 never leaves"


def test_the_far_half_reads_the_shared_net_as_an_input():
    nl, points = chain()
    carved = region.extract(nl, points, region.Box(50, -1, 200, 1))

    assert sorted(carved.inside) == ["inv3", "inv4"]
    assert carved.inputs == ["mid"]
    assert carved.outputs == ["y"]


def test_a_clean_cut_scores_low_and_a_bad_one_high():
    nl, points = chain()
    clean = region.extract(nl, points, region.Box(-1, -1, 50, 1))
    everything = region.extract(nl, points, region.Box(-1, -1, 200, 1))

    assert clean.cut_ratio < 0.7
    # The whole design has only its two ports crossing.
    assert everything.cut_ratio < clean.cut_ratio
    assert "block" in everything.verdict


def test_a_region_through_the_middle_is_called_a_fragment():
    nl, points = chain()
    middle = region.extract(nl, points, region.Box(4, -1, 101, 1))

    assert sorted(middle.inside) == ["inv2", "inv3"]
    assert middle.cut_ratio > 0.6
    assert "fragment" in middle.verdict


def test_an_empty_region_says_so():
    nl, points = chain()
    nothing = region.extract(nl, points, region.Box(200, 200, 300, 300))

    assert nothing.inside == []
    assert nothing.verdict == "empty"


def test_the_carved_netlist_is_a_netlist_like_any_other():
    nl, points = chain()
    carved = region.extract(nl, points, region.Box(-1, -1, 50, 1), name="half")

    assert carved.netlist.top == "half"
    assert {i.name for i in carved.netlist.instances} == {"inv1", "inv2"}
    for inst in carved.netlist.instances:
        for net in inst.connections.values():
            assert net in carved.netlist.nets or net in carved.netlist.power_nets


def test_bounding_box_covers_its_members():
    nl, points = chain()
    box = region.bounding_box(points, ["inv1", "inv2"], pad=1.0)

    assert box.x0 == pytest.approx(-1.0)
    assert box.x1 == pytest.approx(6.0)
    assert region.within(points, box) == ["inv1", "inv2"]


def test_bounding_box_needs_something_to_bound():
    nl, points = chain()
    with pytest.raises(ValueError):
        region.bounding_box(points, ["nowhere"])
