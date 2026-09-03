"""geo.clusters: the boundary rule, decomposition, and path ends."""

from __future__ import annotations

import pytest

from gdsx.geo import clusters
from gdsx.geo import types as g


def box(x0: int, y0: int, x1: int, y1: int) -> g.Box:
    return g.Box(x0, y0, x1, y1)


def poly(*xy: tuple[int, int]) -> g.Polygon:
    return g.Polygon(tuple(g.Point(x, y) for x, y in xy))


def bboxes(cs) -> list[tuple[int, int, int, int]]:
    return sorted((c.bbox.left, c.bbox.bottom, c.bbox.right, c.bbox.top) for c in cs)


#! the boundary rule


@pytest.mark.parametrize(
    ("a", "b", "connected"),
    [
        ((0, 0, 10, 10), (10, 0, 20, 10), True),  # shares the right edge
        ((0, 0, 10, 10), (0, 10, 10, 20), True),  # shares the top edge
        ((0, 0, 10, 10), (10, 10, 20, 20), True),  # shares one corner
        ((0, 0, 10, 10), (5, 5, 15, 15), True),  # overlaps
        ((0, 0, 10, 10), (0, 0, 10, 10), True),  # identical
        ((0, 0, 10, 10), (11, 0, 20, 10), False),  # one unit of clearance
        ((0, 0, 10, 10), (11, 11, 20, 20), False),  # diagonal clearance
        ((0, 0, 10, 10), (0, 11, 10, 20), False),  # clearance above
    ],
)
def test_the_touches_predicate_is_inclusive_and_symmetric(a, b, connected):
    """The rule itself, stated once, in both directions"""
    assert clusters._touches(a, b) is connected
    assert clusters._touches(b, a) is connected


def test_rectangles_sharing_one_edge_are_one_cluster():
    """The single most important detail in the module.

    Routing is drawn as abutting segments; an exclusive comparison here splits
    nearly every net in the design.
    """
    got = clusters.merge([box(0, 0, 10, 10), box(10, 0, 20, 10)])
    assert len(got) == 1
    assert got[0].bbox == g.Box(0, 0, 20, 10)


def test_rectangles_meeting_at_one_corner_are_one_cluster():
    got = clusters.merge([box(0, 0, 10, 10), box(10, 10, 20, 20)])
    assert len(got) == 1


def test_a_one_unit_gap_stays_two_clusters():
    """The other half of the rule: inclusive must not mean "nearly touching\"."""
    got = clusters.merge([box(0, 0, 10, 10), box(11, 0, 20, 10)])
    assert len(got) == 2


def test_overlapping_rectangles_are_one_cluster():
    got = clusters.merge([box(0, 0, 10, 10), box(5, 5, 15, 15)])
    assert len(got) == 1
    assert got[0].bbox == g.Box(0, 0, 15, 15)


def test_a_chain_unions_transitively():
    boxes = [box(i * 10, 0, i * 10 + 10, 10) for i in range(6)]
    got = clusters.merge(boxes)
    assert len(got) == 1
    assert got[0].bbox == g.Box(0, 0, 60, 10)


def test_separate_pieces_stay_separate():
    got = clusters.merge([box(0, 0, 10, 10), box(100, 100, 110, 110)])
    assert bboxes(got) == [(0, 0, 10, 10), (100, 100, 110, 110)]


#! point lookup


def test_a_point_exactly_on_the_boundary_is_inside():
    """A label sitting on a shape's edge names that net, not nothing"""
    cluster = clusters.merge([box(0, 0, 10, 10)])[0]
    for pt in [g.Point(0, 0), g.Point(10, 10), g.Point(0, 5), g.Point(5, 10)]:
        assert cluster.contains(pt), pt


def test_a_point_just_outside_is_outside():
    cluster = clusters.merge([box(0, 0, 10, 10)])[0]
    assert not cluster.contains(g.Point(11, 5))
    assert not cluster.contains(g.Point(-1, 5))


def test_lookup_inside_the_bbox_but_off_the_metal_misses():
    """An L shape's bounding box covers ground the metal does not"""
    cluster = clusters.merge([box(0, 0, 10, 100), box(0, 0, 100, 10)])[0]
    assert cluster.bbox == g.Box(0, 0, 100, 100)
    assert cluster.contains(g.Point(5, 90))
    assert cluster.contains(g.Point(90, 5))
    assert not cluster.contains(g.Point(90, 90))


#! decomposition


def test_a_four_point_rectangle_decomposes_to_itself():
    assert clusters.rects_of(poly((0, 0), (10, 0), (10, 20), (0, 20))) == [
        (0, 0, 10, 20)
    ]


def test_an_l_shape_decomposes_exactly():
    shape = poly((0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30))
    rects = sorted(clusters.rects_of(shape))
    assert rects == [(0, 0, 10, 30), (10, 0, 30, 10)]
    assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects) == 30 * 10 + 20 * 10


def test_a_u_shape_decomposes_into_three_slabs():
    shape = poly(
        (0, 0), (30, 0), (30, 30), (20, 30), (20, 10), (10, 10), (10, 30), (0, 30)
    )
    rects = sorted(clusters.rects_of(shape))
    assert rects == [(0, 0, 10, 30), (10, 0, 20, 10), (20, 0, 30, 30)]


def test_a_u_shape_is_one_cluster_and_its_notch_is_not_inside():
    shape = poly(
        (0, 0), (30, 0), (30, 30), (20, 30), (20, 10), (10, 10), (10, 30), (0, 30)
    )
    got = clusters.merge([shape])
    assert len(got) == 1
    assert got[0].contains(g.Point(15, 5))
    assert not got[0].contains(g.Point(15, 25))


def test_a_hole_is_not_inside_the_cluster():
    ring = (
        g.Point(0, 0),
        g.Point(100, 0),
        g.Point(100, 100),
        g.Point(0, 100),
    )
    hole = (
        g.Point(40, 60),
        g.Point(60, 60),
        g.Point(60, 40),
        g.Point(40, 40),
    )
    got = clusters.merge([g.Polygon(ring, (hole,))])
    assert len(got) == 1
    assert got[0].contains(g.Point(10, 10))
    assert not got[0].contains(g.Point(50, 50))


def test_a_diagonal_edge_raises_rather_than_being_approximated():
    with pytest.raises(ValueError, match="not axis-aligned"):
        clusters.rects_of(poly((0, 0), (10, 5), (10, 20), (0, 20)))


def test_text_carries_no_geometry():
    assert clusters.rects_of(g.Text("VPWR", 10, 20)) == []
    assert clusters.merge([g.Text("VPWR", 10, 20)]) == []


def test_an_empty_box_contributes_nothing():
    assert clusters.rects_of(g.Box.empty_box()) == []


#! paths


def _hpath(pathtype: int, **kw) -> g.Path:
    return g.Path((g.Point(0, 0), g.Point(100, 0)), 20, pathtype, **kw)


def test_butt_path_does_not_extend():
    assert clusters.rects_of(_hpath(0)) == [(0, -10, 100, 10)]


def test_square_path_extends_by_half_the_width():
    assert clusters.rects_of(_hpath(2)) == [(-10, -10, 110, 10)]


def test_round_path_is_approximated_as_square():
    """A deliberate approximation, documented in the module docstring"""
    assert clusters.rects_of(_hpath(1)) == clusters.rects_of(_hpath(2))


def test_custom_path_uses_its_own_extensions():
    assert clusters.rects_of(_hpath(4, bgnextn=5, endextn=30)) == [(-5, -10, 130, 10)]


def test_extensions_apply_only_at_the_two_ends_of_the_path():
    path = g.Path((g.Point(0, 0), g.Point(100, 0), g.Point(100, 100)), 20, 2)
    assert sorted(clusters.rects_of(path)) == [
        (-10, -10, 100, 10),  # extended at the start, butt at the corner
        (90, 0, 110, 110),  # butt at the corner, extended at the end
    ]


def test_a_path_running_backwards_extends_the_right_way():
    path = g.Path((g.Point(100, 0), g.Point(0, 0)), 20, 2)
    assert clusters.rects_of(path) == [(-10, -10, 110, 10)]


def test_a_corner_joins_without_any_corner_fill():
    path = g.Path((g.Point(0, 0), g.Point(100, 0), g.Point(100, 100)), 20, 0)
    got = clusters.merge([path])
    assert len(got) == 1


def test_a_diagonal_path_segment_raises():
    with pytest.raises(ValueError, match="diagonal"):
        clusters.rects_of(g.Path((g.Point(0, 0), g.Point(10, 10)), 20, 0))


def test_an_odd_path_width_raises_rather_than_rounding():
    with pytest.raises(ValueError, match="odd"):
        clusters.rects_of(g.Path((g.Point(0, 0), g.Point(100, 0)), 21, 0))


def test_an_unknown_pathtype_raises():
    with pytest.raises(ValueError, match="PATHTYPE"):
        clusters.rects_of(_hpath(3))


#! provenance


def test_a_cluster_reports_the_shapes_it_came_from():
    got = clusters.merge(
        [box(0, 0, 10, 10), box(500, 500, 510, 510), box(10, 0, 20, 10)]
    )
    by_bbox = {c.bbox.left: c for c in got}
    assert by_bbox[0].shape_ids == (0, 2)
    assert by_bbox[500].shape_ids == (1,)
