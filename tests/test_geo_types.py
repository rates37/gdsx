"""geo.types must agree with klayout exactly.

klayout is the oracle for the geometry layer, so these are parity tests rather
than assertions about what the types "should" do. Two of them guard things that
would silently corrupt extraction:

- `str(Trans)` is a placement sort key in `netlist.build`, so a differing repr
  renames every instance in the design.
- containment is inclusive, so an off-by-one here disconnects nets.
"""

from __future__ import annotations

import itertools

import klayout.db as db
import pytest

from gdsx.geo import types as g
from gdsx.geo.klayout_backend import from_trans, to_box, to_trans

ORIENTATIONS = list(itertools.product(range(4), (False, True)))
DISPLACEMENTS = [(0, 0), (100, 200), (-37, 41), (5, -9), (-1000, -1000)]


def _both(rot, mirror, dx, dy):
    return g.Trans(rot, mirror, dx, dy), db.ICplxTrans(db.Trans(rot, mirror, dx, dy))


@pytest.mark.parametrize("rot,mirror", ORIENTATIONS)
@pytest.mark.parametrize("dx,dy", DISPLACEMENTS)
def test_trans_str_matches_klayout(rot, mirror, dx, dy):
    mine, theirs = _both(rot, mirror, dx, dy)
    assert str(mine) == str(theirs)


@pytest.mark.parametrize("rot,mirror", ORIENTATIONS)
@pytest.mark.parametrize("dx,dy", DISPLACEMENTS)
def test_trans_applied_to_a_point_matches_klayout(rot, mirror, dx, dy):
    mine, theirs = _both(rot, mirror, dx, dy)
    for x, y in ((0, 0), (10, 3), (-7, 21), (1234, -5678)):
        got = mine * g.Point(x, y)
        want = theirs * db.Point(x, y)
        assert (got.x, got.y) == (want.x, want.y)


@pytest.mark.parametrize("rot,mirror", ORIENTATIONS)
def test_trans_applied_to_a_box_matches_klayout(rot, mirror):
    mine, theirs = _both(rot, mirror, 5, 7)
    box = g.Box(0, 0, 10, 20)
    assert str(mine * box) == str(theirs * db.Box(0, 0, 10, 20))


@pytest.mark.parametrize("a", ORIENTATIONS)
@pytest.mark.parametrize("b", ORIENTATIONS)
def test_trans_composition_matches_klayout(a, b):
    mine_a, theirs_a = _both(*a, 5, 7)
    mine_b, theirs_b = _both(*b, -3, 11)
    assert str(mine_a * mine_b) == str(theirs_a * theirs_b)
    # and composing then applying is the same as applying twice
    point = g.Point(13, -4)
    assert (mine_a * mine_b) * point == mine_a * (mine_b * point)


@pytest.mark.parametrize("rot,mirror", ORIENTATIONS)
def test_trans_round_trips_through_klayout(rot, mirror):
    mine = g.Trans(rot, mirror, 12, -34)
    assert to_trans(from_trans(mine)) == mine


def test_trans_rejects_magnification_and_odd_angles():
    with pytest.raises(ValueError, match="magnification"):
        to_trans(db.ICplxTrans(2.0, 0.0, False, 0, 0))
    with pytest.raises(ValueError, match="multiple of 90"):
        to_trans(db.ICplxTrans(1.0, 45.0, False, 0, 0))


@pytest.mark.parametrize(
    "bounds",
    [(0, 0, 10, 20), (0, 0, 11, 21), (-11, -21, 0, 0), (-5, -5, 6, 6), (3, 3, 4, 4)],
)
def test_box_matches_klayout(bounds):
    mine, theirs = g.Box(*bounds), db.Box(*bounds)
    assert str(mine) == str(theirs)
    assert (mine.width(), mine.height()) == (theirs.width(), theirs.height())
    assert mine.area() == theirs.area()
    assert (mine.center().x, mine.center().y) == (theirs.center().x, theirs.center().y)


def test_box_containment_is_inclusive():
    box = g.Box(0, 0, 10, 10)
    for x, y in ((0, 0), (10, 10), (0, 5), (5, 10), (5, 5)):
        assert box.contains(g.Point(x, y))
    for x, y in ((-1, 5), (11, 5), (5, -1), (5, 11)):
        assert not box.contains(g.Point(x, y))


def test_boxes_sharing_one_edge_overlap():
    # The rule the whole netlist rests on: abutting shapes are one net
    assert g.Box(0, 0, 100, 100).overlaps(g.Box(100, 0, 200, 100))
    assert not g.Box(0, 0, 100, 100).overlaps(g.Box(101, 0, 200, 100))


@pytest.mark.parametrize(
    "point",
    [(0, 0), (10, 10), (0, 5), (5, 0), (5, 5), (-1, 5), (11, 5), (5, 11), (10, 0)],
)
def test_polygon_inside_matches_klayout(point):
    mine = g.Polygon.from_box(g.Box(0, 0, 10, 10))
    theirs = db.Polygon(db.Box(0, 0, 10, 10))
    assert mine.inside(g.Point(*point)) == theirs.inside(db.Point(*point))


@pytest.mark.parametrize(
    "point",
    [(0, 0), (5, 5), (20, 5), (25, 25), (10, 10), (15, 15), (30, 30), (31, 5)],
)
def test_polygon_with_a_hole_matches_klayout(point):
    hull = [(0, 0), (30, 0), (30, 30), (0, 30)]
    hole = [(10, 10), (20, 10), (20, 20), (10, 20)]

    theirs = db.Polygon([db.Point(*p) for p in hull])
    theirs.insert_hole([db.Point(*p) for p in hole])
    mine = g.Polygon(
        tuple(g.Point(*p) for p in hull), (tuple(g.Point(*p) for p in hole),)
    )

    assert mine.inside(g.Point(*point)) == theirs.inside(db.Point(*point))


def test_l_shaped_polygon_matches_klayout():
    hull = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]
    theirs = db.Polygon([db.Point(*p) for p in hull])
    mine = g.Polygon(tuple(g.Point(*p) for p in hull))
    for x in range(-2, 23):
        for y in range(-2, 23):
            assert mine.inside(g.Point(x, y)) == theirs.inside(db.Point(x, y)), (x, y)


def test_to_box_round_trips():
    assert to_box(db.Box(3, 4, 5, 6)) == g.Box(3, 4, 5, 6)
