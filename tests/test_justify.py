"""Backward justification, checked against the Main Puzzle solution.

The net names in those documents are stale, as extraction renumbered them,
so `lock_net` re-finds the net by its structure. The instance names did not
move, which is why the logged `dfrtp_2_NN.Q` labels still compare directly.

# todo: fix this, it's janky
"""

from __future__ import annotations

import pathlib
import re

import pytest
from gdsx.analysis import justify
from gdsx.core.graph import Graph
from gdsx.core.netlist import Instance, Netlist

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAVES_LOG = ROOT / "work" / "log-leaves-96-136.txt"

# The two flops the window terms pin down, on top of the 44 the comparator pins
# down. See test_the_lock_net_is_the_whole_lock.
WINDOW = {"dfrtp_2_82.Q": 0, "dfrtp_2_61.Q": 1}


def read_leaves_log(path: pathlib.Path) -> dict[str, dict[str, int]]:
    """Parse `work/leaves.py`'s output: `~name` means 0, bare `name` means 1."""
    sections: dict[str, dict[str, int]] = {}
    current: dict[str, int] | None = None
    for line in path.read_text().splitlines():
        header = re.match(r"=== (\S+) : (\d+) leaves ===", line)
        if header:
            current = sections.setdefault(header.group(1), {})
            continue
        name = line.strip()
        if name and current is not None:
            current[name.lstrip("~")] = 0 if name.startswith("~") else 1
    return sections


def lock_net(graph: Graph) -> str:
    """The net  `n147` (in the original puzzle solution), found by structure.

    Net names are positional and were renumbered by the canonical sorting, so the
    document's `n147` is not the same `n147`. Instance names did not move, and
    the shape did not either: `success` is driven by one flop, whose `D` is an
    `a32o` whose `A3` leg is the `and4b` carrying the comparator. Deriving the
    name that way is what the agent guide's "net names are stable only after
    R1" entry asks for.
    """
    flop = graph.driver_of("success")
    trigger = graph.driver_of(graph.d_pin(flop.instance))
    assert trigger.cell.endswith("a32o_2"), trigger.cell
    net = graph.by_name[trigger.instance].connections["A3"]
    assert graph.driver_of(net).cell.endswith("and4b_2")
    return net


def comparator_halves(graph: Graph) -> dict[str, str]:
    """The lock net's two 22-leaf AND trees: `n96` and `n136`, by structure."""
    conns = graph.by_name[graph.driver_of(lock_net(graph)).instance].connections
    return {"n96": conns["C"], "n136": conns["D"]}


def netlist_of(*instances: Instance, ports: dict[str, str]) -> Netlist:
    nl = Netlist(top="t", power_nets={"VGND", "VPWR"}, ports=dict(ports))
    nl.instances = list(instances)
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    return nl


def gate(name: str, cell: str, **conns: str) -> Instance:
    return Instance(name, f"sky130_fd_sc_hd__{cell}", dict(conns))


#! the acceptance case


@pytest.mark.slow
def test_the_lock_net_is_the_whole_lock(puzzle_netlist):
    """Justifying `n147` derives the flop state that unlocks the design,
    the finding that originally took a hand-written tree flattener and a
    careful read of polarity markers to reach.

    The net is `~n223 & n151 & n96 & n136`: the 44-flop comparator is
    `n96 & n136`, and `n223`/`n151` are two further flop outputs (`dfrtp_2_82`,
    `dfrtp_2_61`). `work/leaves.py` prints 46 for the same net.
    """
    graph = Graph.of(puzzle_netlist)
    got = justify.requirements(graph, lock_net(graph), 1)

    assert got.consistent
    assert got.choices == (), "this cone is a pure AND tree; nothing is a choice"
    assert len(got.leaves) == 46
    assert all(name.startswith("dfrtp_") for name in got.leaves)

    logged = read_leaves_log(LEAVES_LOG)
    comparator = {**logged["n96"], **logged["n136"]}
    assert len(comparator) == 44
    assert got.leaves == {**comparator, **WINDOW}


@pytest.mark.slow
def test_the_comparator_halves_match_the_recorded_log(puzzle_netlist):
    """22 leaves each, exactly as the original puzzle solution recorded them."""
    graph = Graph.of(puzzle_netlist)
    logged = read_leaves_log(LEAVES_LOG)

    for name, net in comparator_halves(graph).items():
        got = justify.requirements(graph, net, 1)
        assert got.choices == ()
        assert got.leaves == logged[name]
        assert len(got.leaves) == 22


@pytest.mark.slow
def test_it_subsumes_work_leaves_py(puzzle_netlist):
    """An AND-tree flatten is the special case of backward justification where
    nothing is disjunctive.

    `work/leaves.py` recurses only through and*/inv cells and reads polarity off
    a `_N` pin-name suffix. Where that applies, this module must agree with it
    exactly.
    """
    graph = Graph.of(puzzle_netlist)
    logged = read_leaves_log(LEAVES_LOG)

    for name, net in comparator_halves(graph).items():
        got = justify.requirements(graph, net, 1)
        assert got.choices == (), f"{name} has residue leaves.py could not have seen"
        assert got.leaves == logged[name], name


#! forced and unresolved stay apart


def test_an_and_forces_every_input():
    nl = netlist_of(
        gate("g", "and2_1", A="a", B="b", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert got.leaves == {"a": 1, "b": 1}
    assert got.choices == ()


def test_an_or_forces_nothing_and_records_the_choice():
    """A target of 1 on an OR is not a requirement on either input, and
    must not be reported as one."""
    nl = netlist_of(
        gate("g", "or2_1", A="a", B="b", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)

    assert got.leaves == {}, "neither input is forced; either one would do"
    assert len(got.choices) == 1
    choice = got.choices[0]
    assert choice.net == "y" and choice.value == 1
    assert set(choice.options) == {frozenset({("a", 1)}), frozenset({("b", 1)})}


def test_polarity_comes_from_liberty_not_from_the_pin_name():
    """and4b's A_N is inverted. Nothing here knows what `_N` means; the truth
    table says so."""
    nl = netlist_of(
        gate("g", "and4b_1", A_N="a", B="b", C="c", D="d", X="y"),
        ports={n: "input" for n in "abcd"} | {"y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert got.leaves == {"a": 0, "b": 1, "c": 1, "d": 1}
    assert got.choices == ()


def test_a_mux_forces_the_selected_leg_and_leaves_the_other_alone():
    """The unselected data pin is a don't-care. A structural rule that forced it
    would be wrong; enumerating the cell gets it right without being told."""
    nl = netlist_of(
        gate("g", "mux2_1", A0="a0", A1="a1", S="s", X="y"),
        gate("tie", "conb_1", HI="s", LO="unused"),
        ports={"a0": "input", "a1": "input", "y": "output"},
    )
    graph = Graph.of(nl)
    # `s` is driven high by the tie cell, so only the A1 leg can matter.
    got = justify.requirements(graph, "y", 1)
    assert got.forced["a1"] == 1
    assert "a0" not in got.forced


def test_an_inverter_flips_the_target():
    nl = netlist_of(
        gate("g", "inv_1", A="a", Y="y"),
        ports={"a": "input", "y": "output"},
    )
    graph = Graph.of(nl)
    assert justify.requirements(graph, "y", 1).leaves == {"a": 0}
    assert justify.requirements(graph, "y", 0).leaves == {"a": 1}


def test_the_walk_stops_at_flops():
    nl = netlist_of(
        gate("ff", "dfrtp_1", CLK="clk", D="d", RESET_B="rst_n", Q="q"),
        gate("g", "and2_1", A="q", B="b", X="y"),
        ports={"clk": "input", "d": "input", "rst_n": "input", "b": "input"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert got.leaves == {"ff.Q": 1, "b": 1}, "past a flop is the previous cycle"
    assert "d" not in got.forced


#! contradictions are reported, not raised


def test_a_target_that_contradicts_a_constant_is_a_conflict():
    nl = netlist_of(
        gate("tie", "conb_1", HI="hi", LO="lo"),
        gate("g", "and2_1", A="hi", B="b", X="y"),
        ports={"b": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert got.consistent, "hi is 1 and the AND wants 1: no contradiction here"

    got = justify.requirements(Graph.of(nl), "lo", 1)
    assert not got.consistent
    assert got.conflicts == ("lo",)


def test_reconvergence_onto_opposite_values_is_a_conflict():
    """`y = a & ~a` cannot be 1. The walk reaches `a` needing 1 and needing 0."""
    nl = netlist_of(
        gate("inv", "inv_1", A="a", Y="na"),
        gate("g", "and2_1", A="a", B="na", X="y"),
        ports={"a": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert not got.consistent
    assert "a" in got.conflicts


def test_reconvergence_onto_the_same_value_is_reported_once():
    nl = netlist_of(
        gate("buf", "buf_1", A="a", X="ba"),
        gate("g", "and2_1", A="a", B="ba", X="y"),
        ports={"a": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)
    assert got.consistent
    assert got.leaves == {"a": 1}


#! unit propagation over the residue


def test_a_choice_contradicted_down_to_one_option_is_promoted():
    """`y = (a & b) | c` with `c` held low. Neither leg is forced by the OR on
    its own, but the tie kills one option, so the survivor becomes a
    derivation."""
    nl = netlist_of(
        gate("tie", "conb_1", HI="hi", LO="c"),
        gate("g", "a21o_1", A1="a", A2="b", B1="c", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)

    assert got.consistent
    assert got.choices == (), "the disjunction resolved; nothing is left unresolved"
    assert got.forced["a"] == 1 and got.forced["b"] == 1


def test_a_choice_already_satisfied_is_discharged():
    """`y = (a & b) | c` with `c` held high: the target is met whatever a and b
    do, so nothing may be reported as forced."""
    nl = netlist_of(
        gate("tie", "conb_1", HI="c", LO="lo"),
        gate("g", "a21o_1", A1="a", A2="b", B1="c", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 1)

    assert got.consistent
    assert got.choices == ()
    assert "a" not in got.forced and "b" not in got.forced


def test_choices_are_terms_not_minterms():
    """A 4-input AND asked for 0 has fifteen satisfying rows and four honest
    options. Reporting the rows would be correct and unreadable."""
    nl = netlist_of(
        gate("g", "and4_1", A="a", B="b", C="c", D="d", X="y"),
        ports={n: "input" for n in "abcd"} | {"y": "output"},
    )
    got = justify.requirements(Graph.of(nl), "y", 0)
    assert len(got.choices) == 1
    assert set(got.choices[0].options) == {frozenset({(n, 0)}) for n in "abcd"}


@pytest.mark.slow
def test_the_success_cone_reads_as_the_documented_expression(puzzle_netlist):
    """Success trigger is read as `D(success) = (n121 & n98 & n147) | (success & n124)`.
    That is a disjunction, so it must come back as one two-option choice with nothing
    forced, not as five literals.
    """
    graph = Graph.of(puzzle_netlist)
    flop = graph.driver_of("success")
    trigger = graph.d_pin(flop.instance)
    conns = graph.by_name[graph.driver_of(trigger).instance].connections

    got = justify.requirements(graph, trigger, 1)
    assert got.leaves == {}
    assert len(got.choices) == 1
    assert set(got.choices[0].options) == {
        frozenset({(conns["A1"], 1), (conns["A2"], 1), (conns["A3"], 1)}),
        frozenset({(conns["B1"], 1), (conns["B2"], 1)}),
    }


#! forward implication


def test_forced_by_propagates_a_controlling_value():
    """One input of an AND at 0 settles the output, with the other unknown."""
    nl = netlist_of(
        gate("g", "and2_1", A="a", B="b", X="y"),
        gate("h", "inv_1", A="y", Y="z"),
        ports={"a": "input", "b": "input", "z": "output"},
    )
    got = justify.forced_by(Graph.of(nl), {"a": 0})
    assert got["y"] == 0 and got["z"] == 1
    assert "b" not in got


def test_forced_by_leaves_the_undetermined_undetermined():
    nl = netlist_of(
        gate("g", "and2_1", A="a", B="b", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    assert justify.forced_by(Graph.of(nl), {"a": 1}) == {"a": 1}


def test_forced_by_does_not_propagate_through_a_flop():
    nl = netlist_of(
        gate("ff", "dfrtp_1", CLK="clk", D="d", RESET_B="rst_n", Q="q"),
        ports={"clk": "input", "d": "input", "rst_n": "input", "q": "output"},
    )
    assert "q" not in justify.forced_by(Graph.of(nl), {"d": 1, "rst_n": 1})


def test_conflict_names_a_self_contradictory_assignment():
    nl = netlist_of(
        gate("g", "and2_1", A="a", B="b", X="y"),
        ports={"a": "input", "b": "input", "y": "output"},
    )
    graph = Graph.of(nl)
    assert justify.conflict(graph, {"a": 0, "y": 1}) == ["y"]
    assert justify.conflict(graph, {"a": 1, "b": 1, "y": 1}) is None


@pytest.mark.slow
def test_the_derived_requirements_actually_produce_the_target(puzzle_netlist):
    """The round trip: feed the forced leaves back in forwards and the lock net
    must come out at 1. A justification that does not re-derive its own target
    is wrong, and this catches a polarity slip anywhere in the tree."""
    graph = Graph.of(puzzle_netlist)
    net = lock_net(graph)
    got = justify.requirements(graph, net, 1)

    assignments = {n: v for n, v in got.forced.items() if n in got.labels}
    assert justify.forced_by(graph, assignments)[net] == 1
    assert justify.conflict(graph, assignments) is None
