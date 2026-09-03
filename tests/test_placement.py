from __future__ import annotations

from gdsx.physical import placement as geometry
from gdsx.report import placement as report_placement


def grid(spec: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    return spec


def test_rows_and_pitch():
    points = {f"c{i}": (float(i), 2.72 * (i % 3)) for i in range(9)}
    found = geometry.rows(points)

    assert [r.y for r in found] == [0.0, 2.72, 5.44]
    assert geometry.pitch(found) == 2.72


def test_bands_split_on_a_routing_channel():
    """Two clumps far apart become two bands, spacing inside them does not"""
    points = {}
    for i in range(5):
        points[f"left{i}"] = (float(i), 0.0)
    for i in range(5):
        points[f"right{i}"] = (100.0 + i, 0.0)

    found = geometry.bands(points, "x")
    assert len(found) == 2
    assert {len(b.members) for b in found} == {5}
    assert found[0].hi < found[1].lo


def test_a_single_clump_is_one_band():
    points = {f"c{i}": (float(i), 0.0) for i in range(10)}
    assert len(geometry.bands(points, "x")) == 1


def test_an_array_laid_out_in_order_is_detected():
    points = {f"e{i}": (0.0, 10.0 * i) for i in range(8)}
    indexed = {f"e{i}": i for i in range(8)}

    result = geometry.ordering("bank", indexed, points)
    assert result.axis == "y", "spread along y, so judged on y"
    assert result.tau == 1.0
    assert result.ordered


def test_reversed_order_still_counts_as_ordered():
    points = {f"e{i}": (0.0, -10.0 * i) for i in range(8)}
    result = geometry.ordering("bank", {f"e{i}": i for i in range(8)}, points)

    assert result.tau == -1.0
    assert result.ordered


def test_a_scrambled_array_is_not_ordered():
    order = [3, 7, 1, 5, 0, 6, 2, 4]
    points = {f"e{i}": (0.0, 10.0 * order[i]) for i in range(8)}
    result = geometry.ordering("bank", {f"e{i}": i for i in range(8)}, points)

    assert not result.ordered


def test_a_tiny_group_is_never_called_ordered():
    points = {"a": (0.0, 0.0), "b": (0.0, 5.0)}
    result = geometry.ordering("pair", {"a": 0, "b": 1}, points)

    assert result.tau == 1.0
    assert not result.ordered, "too few members for the agreement to mean anything"


def test_several_cells_per_entry_are_averaged():
    """Entries are wider than one cell, the entry's position is their mean"""
    points = {}
    indexed = {}
    for i in range(6):
        for k in range(3):
            points[f"e{i}_{k}"] = (float(k), 10.0 * i)
            indexed[f"e{i}_{k}"] = i

    result = geometry.ordering("bank", indexed, points)
    assert result.members == 6, "six entries, not eighteen cells"
    assert result.tau == 1.0


def test_compactness_prefers_a_tight_group():
    tight = {f"t{i}": (float(i), 0.0) for i in range(5)}
    spread = {f"s{i}": (100.0 * i, 0.0) for i in range(5)}
    points = tight | spread

    assert geometry.compactness(list(tight), points) < geometry.compactness(
        list(spread), points
    )


def test_report_breaks_bands_down_by_group():
    points = {"a1": (0.0, 0.0), "a2": (1.0, 0.0), "b1": (100.0, 0.0)}
    text = report_placement.report(points, {"alpha": ["a1", "a2"], "beta": ["b1"]}, "x")

    assert "alpha" in text and "beta" in text
    assert "2 cell rows" not in text  # all on one row
