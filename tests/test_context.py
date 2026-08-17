from __future__ import annotations

import pathlib

import pytest
from gdsx import analyse, guards as guarding, interface as interfacing, netlist
from gdsx.core.context import Design, NoLayoutAvailable
from gdsx.core.graph import Graph

SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "samples"
PUZZLE = SAMPLES / "puzzle.gds"
SAMPLE = SAMPLES / "sample.gds"


@pytest.fixture
def counted_graphs(monkeypatch):
    """Every netlist a Graph was built over, in construction order"""
    built: list = []
    original = Graph.__post_init__

    def counting(self) -> None:
        built.append(self.netlist)
        original(self)

    monkeypatch.setattr(Graph, "__post_init__", counting)
    return built


def test_the_graph_is_built_exactly_once_for_a_whole_session(counted_graphs, tech):
    """R2's acceptance criterion, on the design it was written about

    Counted by identity against this design's netlist: `guards()` runs
    `normalise`, which legitimately builds graphs over the reduced netlists it
    produces, and those are different graphs of different netlists.
    """
    design = Design.open(PUZZLE, tech=tech)
    design.registers()
    design.interface()
    design.guards()

    mine = [nl for nl in counted_graphs if nl is design.netlist]
    assert len(mine) == 1, f"built {len(mine)} graphs over the design's netlist"


def test_the_graph_is_the_one_every_analysis_sees(sample_netlist):
    design = Design.from_netlist(sample_netlist)
    assert design.graph is Graph.of(sample_netlist)
    assert design.graph is design.graph


def test_a_design_from_a_netlist_has_no_layout(sample_netlist):
    design = Design.from_netlist(sample_netlist)
    assert design.netlist is sample_netlist
    assert design.graph.netlist is sample_netlist
    with pytest.raises(NoLayoutAvailable):
        design.layout


def test_a_netlist_json_source_loads_but_has_no_layout(tmp_path, sample_netlist):
    path = tmp_path / "sample.json"
    path.write_text(netlist.to_json(sample_netlist))

    design = Design.open(path)
    assert design.netlist.to_dict() == sample_netlist.to_dict()
    with pytest.raises(NoLayoutAvailable):
        design.layout


def test_bytes_are_accepted_in_place_of_a_path(tech):
    """The browser has an ArrayBuffer and no filesystem to put it on"""
    from_path = Design.open(SAMPLE, tech=tech)
    from_bytes = Design.open(SAMPLE.read_bytes(), tech=tech)
    assert from_bytes.netlist.to_dict() == from_path.netlist.to_dict()


def test_the_netlist_is_extracted_once(monkeypatch, tech):
    calls = []
    original = netlist.build

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(netlist, "build", counting)
    design = Design.open(SAMPLE, tech=tech)
    assert design.netlist is design.netlist
    assert design.graph.netlist is design.netlist
    assert len(calls) == 1


def test_interface_is_memoised_per_cycles_value(monkeypatch, sample_netlist):
    calls = []
    original = interfacing.describe

    def counting(nl, cycles=interfacing.CYCLES, facts=None):
        calls.append(cycles)
        return original(nl, cycles, facts)

    monkeypatch.setattr(interfacing, "describe", counting)
    design = Design.from_netlist(sample_netlist)

    first = design.interface(4)
    assert design.interface(4) is first
    assert calls == [4], "a repeat of the same cycles value must be a cache hit"

    other = design.interface(8)
    assert other is not first
    assert calls == [4, 8], "a different cycles value is a different question"

    assert design.interface() is design.interface(interfacing.CYCLES)
    assert calls == [4, 8, interfacing.CYCLES]


def test_guards_are_memoised_per_min_fanout(monkeypatch, sample_netlist):
    calls = []
    original = guarding.find

    def counting(nl, **kwargs):
        calls.append(kwargs["min_fanout"])
        return original(nl, **kwargs)

    monkeypatch.setattr(guarding, "find", counting)
    design = Design.from_netlist(sample_netlist)

    first = design.guards()
    assert design.guards() is first
    assert design.guards(min_fanout=2) is not first
    assert calls == [guarding.MIN_FANOUT, 2]


def test_ordered_registers_are_cached_apart_from_unordered(sample_netlist):
    design = Design.from_netlist(sample_netlist)
    found = design.registers()

    assert design.registers() is found
    assert [r.name for r in found] == [
        r.name for r in analyse.find_registers(sample_netlist)
    ]

    ordered = design.registers(ordered=True)
    assert design.registers(ordered=True) is ordered
    assert [r.name for r in ordered] == [
        r.name for r in analyse.resolve_bit_order(sample_netlist, found)
    ]


def test_invalidate_drops_what_was_derived_from_the_netlist(sample_netlist):
    design = Design.from_netlist(sample_netlist)
    graph, found, ports = design.graph, design.registers(), design.interface(4)

    design.invalidate("graph")
    assert design.registers() is not found
    assert design.interface(4) is not ports
    assert design.graph is not graph, "the netlist's own memo must go too"

    design.invalidate()
    assert design._cache == {}


def test_invalidate_names_cover_every_cycles_value(sample_netlist):
    design = Design.from_netlist(sample_netlist)
    ports = design.interface(4)
    design.invalidate("interface")
    assert design.interface(4) is not ports
