"""geo.gdsii, the pure-Python GDSII reader, against the klayout backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdsx.geo import gdsii
from gdsx.geo import types as g
from gdsx.geo.klayout_backend import KLayoutBackend

SAMPLES = [Path("samples/sample.gds"), Path("samples/puzzle.gds")]


def _both(sample: Path):
    mine = gdsii.read_file(sample)
    theirs = KLayoutBackend().read_file(sample)
    return mine, theirs


@pytest.mark.parametrize("sample", SAMPLES)
def test_dbu_and_top_cells_match(sample):
    mine, theirs = _both(sample)
    assert mine.dbu == theirs.dbu
    assert sorted(mine.top_cells()) == sorted(theirs.top_cells())


@pytest.mark.parametrize("sample", SAMPLES)
def test_layer_set_matches(sample):
    mine, theirs = _both(sample)
    assert sorted(mine.layers()) == sorted(theirs.layers())


@pytest.mark.parametrize("sample", SAMPLES)
def test_shape_counts_and_bboxes_match_per_layer(sample):
    mine, theirs = _both(sample)
    mine_top = mine.top_cells()[0]
    their_top = theirs.top_cells()[0]

    layers = sorted(mine.layers())
    assert layers, "expected at least one layer"

    for ld in layers:
        mine_idx = mine.layer_index(ld)
        their_idx = theirs.layer_index(ld)
        assert mine_idx is not None and their_idx is not None

        mine_shapes = list(mine.shapes_rec(mine_top, mine_idx))
        their_shapes = list(theirs.shapes_rec(their_top, their_idx))
        assert len(mine_shapes) == len(their_shapes), f"layer {ld}"

        mine_bbox = g.Box.empty_box()
        for shape in mine_shapes:
            mine_bbox = _union(mine_bbox, _bbox_of(shape))
        their_bbox = g.Box.empty_box()
        for shape in their_shapes:
            their_bbox = _union(their_bbox, _bbox_of(shape))
        assert mine_bbox == their_bbox, f"layer {ld}"


@pytest.mark.parametrize("sample", SAMPLES)
def test_leaf_instance_placements_match(sample):
    """Placement order fixes instance naming in `netlist.build`"""
    mine, theirs = _both(sample)
    mine_top = mine.top_cells()[0]
    their_top = theirs.top_cells()[0]

    mine_placements = [(n, str(t)) for n, t in mine.leaf_instances(mine_top)]
    their_placements = [(n, str(t)) for n, t in theirs.leaf_instances(their_top)]

    assert len(mine_placements) == len(their_placements)
    assert mine_placements == their_placements


def _bbox_of(shape: g.Shape) -> g.Box:
    return shape if isinstance(shape, g.Box) else shape.bbox()


def _union(a: g.Box, b: g.Box) -> g.Box:
    if a.empty():
        return b
    if b.empty():
        return a
    return g.Box(
        min(a.left, b.left),
        min(a.bottom, b.bottom),
        max(a.right, b.right),
        max(a.top, b.top),
    )
