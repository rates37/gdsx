"""`sim.sensitivity`: the temporal sensitivity sweep and the probe it absorbs.

The mechanics are checked against a small hand-built netlist where the
right answer can be worked out by hand: two flops fed directly from `I`
through an enable AND, with no feedback. A flop with `D = I & en` is never
"sticky" -- whatever it captures on one edge is gone by the next unless `I`
is held -- so the *final* state after a fixed number of cycles depends only
on the very last edge. That makes the reactive cycle for the enabled flop
exactly `cycles - 1`, and every earlier cycle silent, which is enough to
exercise both self-checks without needing a real address decoder.
"""

from __future__ import annotations

import pathlib
import re
import warnings

import pytest

from gdsx.core.graph import Graph
from gdsx.core.netlist import Instance, Netlist
from gdsx.sim import sensitivity

ROOT = pathlib.Path(__file__).resolve().parents[1]


def two_flop_netlist() -> Netlist:
    """flopA (`en`=1) and flopB (`en`=0), both `D = I & en`, no feedback."""
    nl = Netlist(top="two_flop", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("andA", "sky130_fd_sc_hd__and2_2", {"A": "I", "B": "enA", "X": "dA"}),
        Instance("andB", "sky130_fd_sc_hd__and2_2", {"A": "I", "B": "enB", "X": "dB"}),
        Instance(
            "flopA",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "dA", "RESET_B": "rst_n", "Q": "flopA_q"},
        ),
        Instance(
            "flopB",
            "sky130_fd_sc_hd__dfrtp_2",
            {"CLK": "clk", "D": "dB", "RESET_B": "rst_n", "Q": "flopB_q"},
        ),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {
        "clk": "input",
        "rst_n": "input",
        "I": "input",
        "enA": "input",
        "enB": "input",
    }
    return nl


BASELINE = {"clk": 0, "rst_n": 1, "I": 0, "enA": 1, "enB": 0}
CYCLES = 8


#! probe()


def test_probe_sees_no_diff_when_the_pulse_is_not_held_to_the_last_cycle():
    got = sensitivity.probe(
        two_flop_netlist(),
        BASELINE,
        {0: {"I": 1}},
        ["flopA", "flopB"],
        cycles=CYCLES,
    )
    assert got.changed == ()
    assert got.baseline == got.perturbed


def test_probe_sees_the_diff_when_the_pulse_lands_on_the_last_cycle():
    got = sensitivity.probe(
        two_flop_netlist(),
        BASELINE,
        {CYCLES - 1: {"I": 1}},
        ["flopA", "flopB"],
        cycles=CYCLES,
    )
    assert got.changed == ("flopA",)
    assert got.baseline == {"flopA": 0, "flopB": 0}
    assert got.perturbed == {"flopA": 1, "flopB": 0}


#! map()


def test_map_places_the_one_reactive_cycle_correctly():
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        got = sensitivity.map(
            two_flop_netlist(),
            cycles=CYCLES,
            baseline=BASELINE,
            perturb={"I": 1},
            watch=["flopA", "flopB"],
        )
    assert got.cycles_for("flopA") == [CYCLES - 1]
    assert got.cycles_for("flopB") == []
    assert got.elements_for(CYCLES - 1) == ["flopA"]
    for c in range(CYCLES - 1):
        assert got.elements_for(c) == []


def test_map_warns_when_a_watched_element_never_reacts():
    with pytest.warns(UserWarning, match="flopB"):
        got = sensitivity.map(
            two_flop_netlist(),
            cycles=CYCLES,
            baseline=BASELINE,
            perturb={"I": 1},
            watch=["flopA", "flopB"],
        )
    assert got.unreactive == ("flopB",)


def test_map_warns_when_cycles_touch_no_watched_element():
    with pytest.warns(UserWarning, match="cycle"):
        got = sensitivity.map(
            two_flop_netlist(),
            cycles=CYCLES,
            baseline=BASELINE,
            perturb={"I": 1},
            watch=["flopA", "flopB"],
        )
    assert got.silent_cycles == tuple(range(CYCLES - 1))


def sticky_flop_netlist() -> Netlist:
    """One flop that latches and holds: `D = Q | (I & en)`.

    Unlike the plain `D = I & en` flop above, a pulse anywhere is never
    overwritten by a later cycle, so every single-cycle perturbation changes
    the final state -- the case where neither self-check should have anything
    to report.
    """
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
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"clk": "input", "rst_n": "input", "I": "input", "en": "input"}
    return nl


def test_map_raises_no_warning_when_every_cycle_is_reactive():
    baseline = {"clk": 0, "rst_n": 1, "I": 0, "en": 1}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = sensitivity.map(
            sticky_flop_netlist(),
            cycles=CYCLES,
            baseline=baseline,
            perturb={"I": 1},
            watch=["flop"],
        )
    assert got.cycles_for("flop") == list(range(CYCLES))
    assert got.unreactive == ()
    assert got.silent_cycles == ()


def lock_net(graph: Graph) -> str:
    """The whole-lock net, found by structure, see `tests/test_justify.py`.

    Net names are positional and were renumbered by canonical sorting, so this
    does not hardcode the stale `n147` the puzzle documents use.
    """
    flop = graph.driver_of("success")
    trigger = graph.driver_of(graph.d_pin(flop.instance))
    assert trigger.cell.endswith("a32o_2"), trigger.cell
    net = graph.by_name[trigger.instance].connections["A3"]
    assert graph.driver_of(net).cell.endswith("and4b_2")
    return net


def comparator_halves(graph: Graph) -> dict[str, str]:
    """The lock net's two 22-leaf AND trees, by structure, not by stale name."""
    conns = graph.by_name[graph.driver_of(lock_net(graph)).instance].connections
    return {"a": conns["C"], "b": conns["D"]}


def pair_up(flops: set[str], graph: Graph) -> dict[str, tuple[str, str]]:
    """flop -> its (flop, flop) pair, by mutual D-cone dependency."""
    sup = {f: graph.d_support(f) for f in flops}
    pair: dict[str, tuple[str, str]] = {}
    for f in flops:
        g = next(x for x in flops if x != f and x in sup[f] and f in sup[x])
        pair[f] = tuple(sorted((f, g), key=lambda s: int(s.rsplit("_", 1)[1])))
    return pair


HIGH_NIBBLE = {"dfrtp_2_17", "dfrtp_2_20", "dfrtp_2_25", "dfrtp_2_26"}


def parse_slotmap_log(path: pathlib.Path) -> list[tuple[list[int], list[int]]]:
    """`cycle NNN  A=[...]  B=[...]` -> [(A indices, B indices), ...]"""
    rows = []
    for line in path.read_text().splitlines():
        m = re.match(r"cycle\s+(\d+)\s+A=(\[[^\]]*\])\s+B=(\[[^\]]*\])", line)
        if m:
            rows.append((eval(m.group(2)), eval(m.group(3))))
    return rows


@pytest.mark.slow
def test_map_reproduces_the_logged_slotmap(puzzle_netlist):
    from gdsx.analysis import justify

    graph = Graph.of(puzzle_netlist)
    halves = comparator_halves(graph)

    req: set[str] = set()
    for net in halves.values():
        leaves = justify.requirements(graph, net, 1).leaves
        req |= {label.split(".")[0] for label in leaves}
    assert len(req) == 44

    pair = pair_up(req, graph)
    sup = {f: graph.d_support(f) for f in req}
    groups = {"A": set(), "B": set()}
    for f, p in pair.items():
        groups["A" if sup[f] & HIGH_NIBBLE else "B"].add(p)
    ga = sorted(groups["A"], key=lambda p: int(p[0].rsplit("_", 1)[1]))
    gb = sorted(groups["B"], key=lambda p: int(p[0].rsplit("_", 1)[1]))
    assert len(ga) == 11
    assert len(gb) == 11

    cycles = 130
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        smap = sensitivity.map(
            puzzle_netlist,
            cycles=cycles,
            baseline={"clk": 0, "rst_n": 1, "enable": 1, "I": 0},
            perturb={"I": 1},
            watch=sorted(req),
            reset={"clk": 0, "rst_n": 0, "enable": 1, "I": 0},
        )

    index_a = {p: i for i, p in enumerate(ga)}
    index_b = {p: i for i, p in enumerate(gb)}

    got_rows = []
    for c in range(cycles):
        touched = set(smap.elements_for(c))
        a = sorted({index_a[pair[f]] for f in touched if pair[f] in index_a})
        b = sorted({index_b[pair[f]] for f in touched if pair[f] in index_b})
        got_rows.append((a, b))

    want_rows = parse_slotmap_log(ROOT / "work" / "log-slotmap.txt")
    assert got_rows == want_rows
