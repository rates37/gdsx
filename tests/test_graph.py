from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest
from gdsx import analyse, loader, netlist, xref
from gdsx.core.graph import CombinationalLoop, Graph, LeafKind
from gdsx.core.netlist import Instance, Netlist
from gdsx.sim import Simulator

PUZZLE = pathlib.Path(__file__).resolve().parents[1] / "samples" / "puzzle.gds"
CORE = pathlib.Path(__file__).resolve().parents[1] / "src" / "gdsx" / "core"


@pytest.fixture(scope="session")
def puzzle_netlist(tech):
    return netlist.build(loader.load(PUZZLE, tech))


@pytest.fixture(scope="session")
def both(sample_netlist, puzzle_netlist):
    return [sample_netlist, puzzle_netlist]


def probe_nets(nl: Netlist, count: int = 40) -> list[str]:
    """A deterministic spread of nets: every port, then an even slice of the rest."""
    ports = sorted(nl.ports)
    rest = sorted(set(nl.nets) - set(ports) - nl.power_nets)
    step = max(1, len(rest) // count)
    return ports + rest[::step]


def chain() -> Netlist:
    """a -> inv -> n1 -> nand2 <- b, nand2 -> y -> buf -> z"""
    nl = Netlist(top="chain", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_1", {"A": "a", "Y": "n1"}),
        Instance(
            "nand2_1", "sky130_fd_sc_hd__nand2_1", {"A": "n1", "B": "b", "Y": "y"}
        ),
        Instance("buf_1", "sky130_fd_sc_hd__buf_1", {"A": "y", "X": "z"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"a": "input", "b": "input", "z": "output"}
    return nl


#! the indices


def test_the_driver_index_names_one_pin_per_driven_net(both):
    for nl in both:
        graph = Graph(nl)
        for net, ref in graph.driver.items():
            inst = graph.by_name[ref.instance]
            assert inst.connections[ref.pin] == net
            assert ref.pin in graph.cell_of[ref.instance].functions
            assert ref.direction == "output"


def test_every_instance_is_indexed_including_ones_the_library_rejects(both):
    for nl in both:
        graph = Graph(nl)
        assert set(graph.by_name) == {i.name for i in nl.instances}
        assert set(graph.cell_of) == set(graph.by_name)
        assert graph.seq == frozenset(
            i.name for i in nl.instances if analyse.is_sequential(i.cell)
        )


def test_a_cell_without_behaviour_drives_and_reads_nothing():
    nl = chain()
    nl.instances.append(
        Instance("decap_1", "sky130_fd_sc_hd__decap_3", {"VPWR": "VPWR"})
    )
    nl.instances.append(Instance("fill_1", "sky130_fd_sc_hd__fill_1", {"A": "dangle"}))
    nl.nets.setdefault("dangle", []).append("fill_1/A")
    graph = Graph(nl)

    assert graph.cell_of["decap_1"] is None
    assert graph.cell_of["fill_1"] is None
    assert "decap_1" in graph.by_name and "fill_1" in graph.by_name
    assert not any(ref.instance == "fill_1" for ref in graph.driver.values())
    assert all(
        ref.instance != "fill_1" for refs in graph.readers.values() for ref in refs
    )
    # Nothing drives `dangle`, so it is a leaf, not a "keep walking" None.
    assert graph.leaf("dangle").kind is LeafKind.UNDRIVEN
    assert graph.driver_of("dangle") is None


#! classification


def test_leaf_kinds():
    graph = Graph(chain())
    assert graph.leaf("VGND").kind is LeafKind.CONST0
    assert graph.leaf("VPWR").kind is LeafKind.CONST1
    assert graph.leaf("a").kind is LeafKind.PRIMARY_IN
    assert graph.leaf("nowhere").kind is LeafKind.UNDRIVEN
    assert graph.leaf("y") is None  # combinational: keep walking
    assert not graph.is_leaf("y")
    assert graph.is_leaf("a")


def test_a_flop_output_is_a_leaf_carrying_its_instance_and_pin(sample_netlist):
    graph = Graph(sample_netlist)
    flop = sorted(graph.seq)[0]
    net = graph.by_name[flop].connections["Q"]
    found = graph.leaf(net)
    assert found.kind is LeafKind.FLOP_Q
    assert (found.instance, found.pin, found.net) == (flop, "Q", net)


def test_label_names_every_leaf_kind(sample_netlist):
    graph = Graph(chain())
    assert graph.label("VGND") == "0"
    assert graph.label("VPWR") == "1"
    assert graph.label("a") == "a"  # primary input: its own name
    assert graph.label("nowhere") == "nowhere"  # undriven
    assert graph.label("y") is None  # combinational: keep walking

    sequential = Graph(sample_netlist)
    flop = sorted(sequential.seq)[0]
    assert sequential.label(sequential.by_name[flop].connections["Q"]) == f"{flop}.Q"


def test_every_net_is_either_labelled_or_walkable(both):
    for nl in both:
        graph = Graph(nl)
        for net in probe_nets(nl):
            assert (graph.label(net) is None) is (graph.leaf(net) is None)


#! traversal equivalence


def test_support_matches_analyse_support(both):
    for nl in both:
        graph = Graph(nl)
        for net in probe_nets(nl):
            assert graph.support(net) == analyse.support(nl, net), net


def test_cone_nets_matches_analyse_cone_nets(both):
    for nl in both:
        graph = Graph(nl)
        for net in probe_nets(nl, 20):
            for stop in (set(), _a_stop_set(graph, net)):
                assert graph.cone({net}, stop=frozenset(stop)) == analyse.cone_nets(
                    nl, {net}, stop
                ), (net, stop)


def test_cone_instances_matches_analyse_cone_instances(both):
    for nl in both:
        graph = Graph(nl)
        for net in probe_nets(nl, 20):
            for stop in (set(), _a_stop_set(graph, net)):
                assert graph.cone(
                    {net}, stop=frozenset(stop), returns="instances"
                ) == analyse.cone_instances(nl, {net}, stop), (net, stop)


def _a_stop_set(graph: Graph, net: str) -> set[str]:
    """A stop set that actually bites: the immediate drivers of `net`."""
    levels = graph.fanin(net, depth=1)
    return set(levels[0]) if levels else set()


def test_a_stop_net_is_neither_returned_nor_expanded():
    graph = Graph(chain())
    assert graph.cone({"z"}) == {"z", "y", "n1"}
    # n1 is in stop: it is left out of the result, and `a` behind it is not walked
    assert graph.cone({"z"}, stop=frozenset({"n1"})) == {"z", "y"}
    assert graph.support("z", stop=frozenset({"n1"})) == {"b"}


def test_cone_rejects_an_unknown_returns_value():
    with pytest.raises(ValueError, match="returns"):
        Graph(chain()).cone({"z"}, returns="instance")


def test_fanin_and_fanout_match_xref(both):
    for nl in both:
        graph = Graph(nl)
        for net in probe_nets(nl, 20):
            for through in (False, True):
                for depth in (1, 3):
                    assert graph.fanin(net, depth, through) == xref.fanin(
                        nl, net, depth, through
                    ), (net, depth, through)
                    assert graph.fanout(net, depth, through) == xref.fanout(
                        nl, net, depth, through
                    ), (net, depth, through)


def test_fanin_tree_keeps_the_gate_by_gate_shape():
    graph = Graph(chain())
    tree = graph.fanin_tree("z")
    assert [(level, node.net) for level, node in tree.walk()] == [
        (0, "z"),
        (1, "y"),
        (2, "n1"),
        (3, "a"),
        (2, "b"),
    ]
    node = {n.net: n for _, n in tree.walk()}
    assert node["y"].driver.instance == "nand2_1"
    assert node["a"].leaf.kind is LeafKind.PRIMARY_IN
    for _, found in tree.walk():  # a node is a leaf or a gate. never both
        assert (found.leaf is None) != (found.driver is None)


def test_fanin_tree_honours_the_depth_and_expands_each_net_once(puzzle_netlist):
    graph = Graph(puzzle_netlist)
    net = graph.d_pin(sorted(graph.seq)[0])
    nodes = list(graph.fanin_tree(net, depth=8).walk())
    assert max(level for level, _ in nodes) <= 8
    expanded = [found.net for _, found in nodes if found.children]
    assert len(expanded) == len(set(expanded))


def test_between_matches_xref(both):
    for nl in both:
        graph = Graph(nl)
        sources = {p for p, d in nl.ports.items() if d == "input"}
        sinks = {p for p, d in nl.ports.items() if d == "output"}
        for through in (False, True):
            assert graph.between(sources, sinks, through_flops=through) == xref.between(
                nl, sources, sinks, through
            )


def test_subgraph_matches_xref_sub_netlist(both):
    for nl in both:
        graph = Graph(nl)
        sinks = {p for p, d in nl.ports.items() if d == "output"}
        chosen = graph.cone(sinks, returns="instances")
        assert (
            graph.subgraph(chosen).to_dict() == xref.sub_netlist(nl, chosen).to_dict()
        )
        assert graph.subgraph(chosen, "slice").top == "slice"


def test_d_support_matches_the_survey_primitive(both):
    for nl in both:
        graph = Graph(nl)
        for flop in sorted(graph.seq):
            cell = graph.cell_of[flop]
            want = set().union(
                *(
                    analyse.support(nl, net)
                    for net in analyse.data_nets(cell, graph.by_name[flop].connections)
                ),
                set(),
            )
            assert graph.d_support(flop) == want, flop
            assert graph.d_pin(flop) == graph.by_name[flop].connections.get("D")


#! topological order


def test_topo_order_is_byte_identical_to_the_simulator(both):
    for nl in both:
        graph = Graph(nl)
        assert [i.name for i in graph.topo()] == [
            inst.name for inst, _ in Simulator(nl).combinational
        ]


def test_topo_respects_an_instance_subset_and_explicit_sources():
    graph = Graph(chain())
    assert [i.name for i in graph.topo()] == ["inv_1", "nand2_1", "buf_1"]
    assert [i.name for i in graph.topo({"nand2_1", "buf_1"}, sources={"n1", "b"})] == [
        "nand2_1",
        "buf_1",
    ]


def test_a_combinational_loop_raises_and_is_still_a_value_error():
    nl = Netlist(top="loop", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_1", {"A": "n2", "Y": "n1"}),
        Instance("inv_2", "sky130_fd_sc_hd__inv_1", {"A": "n1", "Y": "n2"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")

    with pytest.raises(CombinationalLoop) as caught:
        Graph(nl).topo()
    assert isinstance(caught.value, ValueError)
    assert caught.value.instances == ["inv_1", "inv_2"]
    assert "combinational loop or undriven input near" in str(caught.value)


#! convenience


def test_function_of_and_driver_of():
    graph = Graph(chain())
    assert graph.function_of("y") == "(~A | ~B)"  # sky130 states nand2 as !A | !B
    assert graph.function_of("a") is None  # a primary input has no gate
    assert graph.driver_of("y").instance == "nand2_1"
    assert graph.driver_of("y").direction == "output"


def test_formula_substitutes_every_gate_down_to_the_leaves():
    graph = Graph(chain())
    assert graph.formula("n1") == "~(a)"
    assert graph.formula("y") == "(~(~(a)) | ~(b))"
    assert graph.formula("z") == "((~(~(a)) | ~(b)))"
    assert graph.formula("a") == "a"  # a leaf is its label
    assert graph.formula("VGND") == "0"


def test_formula_runs_out_of_depth_rather_than_forever():
    graph = Graph(chain())
    assert graph.formula("z", depth=0) == "z"
    assert graph.formula("z", depth=1) == "(y)"

    nl = Netlist(top="loop", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_1", {"A": "n2", "Y": "n1"}),
        Instance("inv_2", "sky130_fd_sc_hd__inv_1", {"A": "n1", "Y": "n2"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    assert Graph(nl).formula("n1", depth=3) == "~(~(~(n2)))"


def test_formula_never_matches_a_pin_name_inside_a_longer_one(both):
    """`A2` must not be substituted as `A` followed by a stray `2`"""
    for nl in both:
        graph = Graph(nl)
        wide = [
            net
            for net, ref in graph.driver.items()
            if any(len(p) > 1 for p in (graph.cell_of[ref.instance].inputs or ()))
        ]
        for net in wide[:20]:
            assert not re.search(r"\)\d", graph.formula(net, depth=2)), net


#! layering


def test_core_imports_nothing_heavy():
    """core/ may import stdlib, cells knowledge, and core/ — nothing else.

    A static check on purpose: `import gdsx.core.graph` runs the package
    __init__, which still pulls typer and klayout until R9 makes it lazy.
    """
    allowed = {"functions", "liberty", "netlist", "graph"}
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in sys.stdlib_module_names, (path.name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:  # absolute: must be stdlib
                    root = (node.module or "").split(".")[0]
                    assert root in sys.stdlib_module_names, (path.name, node.module)
                else:  # relative: must stay inside the allowlist
                    assert node.module in allowed, (path.name, node.module)
