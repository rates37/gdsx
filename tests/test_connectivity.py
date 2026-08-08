import klayout.db as db
import pytest

from gdsx import config, connectivity, loader


def _layout_with(shapes, tmp_path, name="tiny"):
    """shapes: list of ((layer, datatype), Box)"""
    ly = db.Layout()
    ly.dbu = 0.001
    top = ly.create_cell(name)
    for ld, box in shapes:
        top.shapes(ly.layer(*ld)).insert(box)
    path = tmp_path / f"{name}.gds"
    ly.write(str(path))
    return loader.load(path, config.load())


def test_shapes_touching_on_an_edge_are_one_net(tmp_path):
    design = _layout_with(
        [((68, 20), db.Box(0, 0, 100, 100)), ((68, 20), db.Box(100, 0, 200, 100))],
        tmp_path,
    )
    conn = connectivity.trace(design)
    assert conn.n_clusters == 1


def test_shapes_with_a_gap_are_separate_nets(tmp_path):
    design = _layout_with(
        [
            ((68, 20), db.Box(0, 0, 100, 100)),
            ((101, 0), db.Box(0, 0, 1, 1)),
            ((68, 20), db.Box(101, 0, 200, 100)),
        ],
        tmp_path,
    )
    conn = connectivity.trace(design)
    assert conn.n_clusters == 2
    assert len(conn.nets) == 2


def test_via_stitches_two_layers(tmp_path):
    li1, met1 = db.Box(0, 0, 400, 400), db.Box(200, 200, 600, 600)
    via = db.Box(280, 280, 320, 320)
    with_via = _layout_with(
        [((67, 20), li1), ((68, 20), met1), ((67, 44), via)], tmp_path, "with_via"
    )
    without = _layout_with([((67, 20), li1), ((68, 20), met1)], tmp_path, "without_via")

    assert len(connectivity.trace(with_via).nets) == 1
    assert len(connectivity.trace(without).nets) == 2


def test_via_landing_on_nothing_is_reported(tmp_path):
    design = _layout_with(
        [
            ((67, 20), db.Box(0, 0, 400, 400)),
            ((67, 44), db.Box(9000, 9000, 9100, 9100)),
        ],
        tmp_path,
    )
    conn = connectivity.trace(design)
    assert len(conn.dangling_vias) == 1


@pytest.mark.parametrize("dx,dy", [(0, 0), (-50000, 30000), (12345, -67890)])
def test_index_handles_negative_coordinates(tmp_path, dx, dy):
    box = db.Box(dx, dy, dx + 100, dy + 100)
    design = _layout_with([((68, 20), box)], tmp_path, f"neg{dx}_{dy}")
    conn = connectivity.trace(design)
    assert conn.cluster_at("met1", db.Point(dx + 50, dy + 50)) is not None
