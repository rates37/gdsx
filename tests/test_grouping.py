from __future__ import annotations

import pytest

from gdsx.analysis import grouping
from gdsx.core.graph import Graph

N96 = [
    "dfrtp_2_22",
    "dfrtp_2_23",
    "dfrtp_2_19",
    "dfrtp_2_24",
    "dfrtp_2_31",
    "dfrtp_2_30",
    "dfrtp_2_28",
    "dfrtp_2_29",
    "dfrtp_2_11",
    "dfrtp_2_12",
    "dfrtp_2_7",
    "dfrtp_2_8",
    "dfrtp_2_13",
    "dfrtp_2_15",
    "dfrtp_2_16",
    "dfrtp_2_14",
    "dfrtp_2_39",
    "dfrtp_2_36",
    "dfrtp_2_32",
    "dfrtp_2_35",
    "dfrtp_2_45",
    "dfrtp_2_46",
]

N136 = [
    "dfrtp_2_70",
    "dfrtp_2_71",
    "dfrtp_2_68",
    "dfrtp_2_69",
    "dfrtp_2_74",
    "dfrtp_2_75",
    "dfrtp_2_73",
    "dfrtp_2_72",
    "dfrtp_2_63",
    "dfrtp_2_62",
    "dfrtp_2_57",
    "dfrtp_2_56",
    "dfrtp_2_67",
    "dfrtp_2_66",
    "dfrtp_2_64",
    "dfrtp_2_65",
    "dfrtp_2_78",
    "dfrtp_2_76",
    "dfrtp_2_79",
    "dfrtp_2_80",
    "dfrtp_2_81",
    "dfrtp_2_84",
]

ALL_FLOPS = N96 + N136


# work/pair_sensitivity.py's pair_up(), reproduced here only as the oracle this
# test checks `grouping.mutual` against, not as the thing under test.
def _pair_up(support: dict[str, set[str]], flops: list[str]) -> list[tuple[str, ...]]:
    remaining = set(flops)
    pairs = []
    for f in flops:
        if f not in remaining:
            continue
        partner = next(
            g for g in remaining if g != f and g in support[f] and f in support[g]
        )
        pairs.append(tuple(sorted((f, partner))))
        remaining -= {f, partner}
    return pairs


@pytest.mark.slow
def test_mutual_reproduces_pair_up_on_both_halves(puzzle_netlist):
    graph = Graph.of(puzzle_netlist)
    support = {f: graph.d_support(f) for f in ALL_FLOPS}

    for half in (N96, N136):
        expected = sorted(_pair_up(support, half))
        got = sorted(grouping.mutual(graph, half))
        assert got == expected
        assert all(len(group) == 2 for group in got), "each half is 11 clean pairs"


@pytest.mark.slow
def test_mutual_finds_22_pairs_across_all_44_flops(puzzle_netlist):
    """The two halves don't cross-reference each other, so grouping the full
    44-flop comparator at once still yields the same 22 pairs as grouping
    each half separately."""
    graph = Graph.of(puzzle_netlist)
    groups = grouping.mutual(graph, ALL_FLOPS)

    assert len(groups) == 22
    assert all(len(group) == 2 for group in groups)
    assert {f for group in groups for f in group} == set(ALL_FLOPS)
