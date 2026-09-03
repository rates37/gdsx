"""Backend selection and the klayout backend against the real samples."""

from __future__ import annotations

from pathlib import Path

import klayout.db as db
import pytest

from gdsx import config, geo
from gdsx.geo import types as g
from gdsx.geo.klayout_backend import KLayoutBackend, to_box
from gdsx.geo.pure_backend import PureBackend

SAMPLES = [Path("samples/sample.gds"), Path("samples/puzzle.gds")]
ROUTING = [(67, 20), (68, 20), (67, 44), (68, 44)]


@pytest.fixture(autouse=True)
def _restore_backend():
    """geo.use() is process-global, so put it back afterwards"""
    before = geo.backend()
    yield
    geo._backend = before


def test_backend_names():
    assert geo.use("klayout").name == "klayout"
    assert geo.use("pure").name == "pure"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown geometry backend"):
        geo.use("quantum")


@pytest.mark.parametrize(
    "sample",
    [SAMPLES[0], pytest.param(SAMPLES[1], marks=pytest.mark.slow)],
)
@pytest.mark.parametrize("ld", ROUTING)
def test_pure_clusters_are_the_same_clusters_klayout_finds(sample, ld):
    """The pure backend's components, against the oracle.

    Order is deliberately not compared: klayout emits merged polygons in its
    own scanline order, which is not reproducible and which
    `connectivity.trace` overrides with a canonical sort before numbering.
    What must match is the set of clusters.
    """
    theirs = KLayoutBackend()
    their_layout = theirs.read_file(sample)
    their_top = their_layout.top_cells()[0]
    their_clusters = theirs.merge_clusters(
        their_layout.shapes_rec(their_top, their_layout.layer_index(ld))
    )

    mine = PureBackend()
    my_layout = mine.read_file(sample)
    my_top = my_layout.top_cells()[0]
    my_clusters = mine.merge_clusters(
        my_layout.shapes_rec(my_top, my_layout.layer_index(ld))
    )

    assert len(my_clusters) == len(their_clusters)
    assert sorted(str(c.bbox) for c in my_clusters) == sorted(
        str(c.bbox) for c in their_clusters
    )


def test_reading_bytes_matches_reading_the_file():
    backend = KLayoutBackend()
    from_path = backend.read_file(SAMPLES[0])
    from_bytes = backend.read_gds(SAMPLES[0].read_bytes())
    assert from_bytes.top_cells() == from_path.top_cells()
    assert from_bytes.dbu == from_path.dbu
    assert sorted(from_bytes.layers()) == sorted(from_path.layers())


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("ld", ROUTING)
def test_clusters_match_klayout_region_in_count_and_order(sample, ld):
    """Cluster order fixes net numbering, so it is part of the contract"""
    backend = KLayoutBackend()
    layout = backend.read_file(sample)
    top = layout.top_cells()[0]

    mine = backend.merge_clusters(layout.shapes_rec(top, layout.layer_index(ld)))

    native = db.Layout()
    native.read(str(sample))
    region = db.Region(
        native.top_cells()[0].begin_shapes_rec(native.layer(*ld))
    ).merged()
    theirs = list(region.each())

    assert len(mine) == len(theirs)
    assert [c.bbox for c in mine] == [to_box(p.bbox()) for p in theirs]


@pytest.mark.parametrize("sample", SAMPLES)
def test_leaf_instances_match_the_klayout_hierarchy_walk(sample):
    """Placement order fixes instance naming in `netlist.build`"""
    backend = KLayoutBackend()
    layout = backend.read_file(sample)
    mine = list(layout.leaf_instances(layout.top_cells()[0]))

    native = db.Layout()
    native.read(str(sample))

    def walk(cell, trans):
        for inst in cell.each_inst():
            child = native.cell(inst.cell_index)
            base = inst.cplx_trans
            if inst.is_regular_array():
                a, b = inst.a, inst.b
                placements = [
                    db.ICplxTrans(db.Vector(a.x * i + b.x * j, a.y * i + b.y * j))
                    * base
                    for i in range(inst.na)
                    for j in range(inst.nb)
                ]
            else:
                placements = [base]
            for itrans in placements:
                here = trans * itrans
                if child.is_leaf():
                    yield child.name, here
                else:
                    yield from walk(child, here)

    theirs = list(walk(native.top_cells()[0], db.ICplxTrans()))

    assert len(mine) == len(theirs)
    assert [(n, str(t)) for n, t in mine] == [(n, str(t)) for n, t in theirs]


@pytest.mark.parametrize("sample", SAMPLES)
def test_every_top_level_label_resolves_into_a_cluster(sample):
    """The inclusive-boundary rule on real geometry.

    A top-level label names a port. If its point does not land inside the
    merged metal it sits on, that net silently loses its name and becomes an
    `n<id>`, so this has to hold for every label on every routing layer.
    """
    backend = KLayoutBackend()
    layout = backend.read_file(sample)
    top = layout.top_cells()[0]
    tech = config.load()

    seen = 0
    for rl in tech.routing:
        pin_index = layout.layer_index(rl.pin)
        if pin_index is None:
            continue
        labels = [s for s in layout.shapes(top, pin_index) if isinstance(s, g.Text)]
        if not labels:
            continue

        clusters = backend.merge_clusters(
            list(layout.shapes_rec(top, layout.layer_index(rl.drawing)))
            + list(layout.shapes_rec(top, pin_index))
        )
        for label in labels:
            assert any(c.contains(label.point) for c in clusters), (
                f"{label.string} on {rl.name}"
            )
        seen += len(labels)

    assert seen, "expected top-level pin labels"
