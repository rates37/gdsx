"""The render bundle: LOD reduction, tiling, and the net <-> shape indices"""

from __future__ import annotations

import struct

import pytest

from gdsx import render
from gdsx.netlist import build_with_net_ids, trace_design


@pytest.fixture(scope="session")
def sample_render(sample, tech):
    conn = trace_design(sample, {})
    _, net_names = build_with_net_ids(sample, conn, {})
    return render.build(sample, conn, net_names)


def _rects(
    bundle: render.RenderBundle, lod: int, layer: str
) -> list[tuple[int, int, int, int]]:
    entry = bundle.header["lods"][str(lod)][layer]["rects"]
    off, n = entry["offset"] * 4, entry["count"]
    vals = struct.unpack_from(f"<{n * 4}i", bundle.blob, off)
    return [tuple(vals[i : i + 4]) for i in range(0, len(vals), 4)]


def _shape_ids(bundle: render.RenderBundle, lod: int, layer: str) -> list[int]:
    entry = bundle.header["lods"][str(lod)][layer]["shape_ids"]
    off, n = entry["offset"] * 4, entry["count"]
    return list(struct.unpack_from(f"<{n}i", bundle.blob, off))


def _i32_array(bundle: render.RenderBundle, entry: dict) -> list[int]:
    off, n = entry["offset"] * 4, entry["count"] * entry["stride"]
    return list(struct.unpack_from(f"<{n}i", bundle.blob, off))


def test_dbu_is_the_only_float(sample_render, sample):
    assert sample_render.header["dbu"] == sample.dbu
    assert isinstance(sample_render.header["dbu"], float)


def test_pack_unpack_round_trips(sample_render):
    packed = sample_render.pack()
    restored = render.RenderBundle.unpack(packed)
    assert restored.header == sample_render.header
    assert restored.blob == sample_render.blob


def test_lod2_has_no_layer_geometry(sample_render):
    """At L2 the die is drawn as cells and nothing else -- except the
    substrate, which *is* the die: one rectangle, cheaper than the outline it
    replaces, and without it the zoomed-out view has nothing under the cells.
    """
    synthetic = set(sample_render.header["synthetic_layers"])
    for layer, entry in sample_render.header["lods"]["2"].items():
        expected = 1 if layer in synthetic else 0
        assert entry["rects"]["count"] == expected, layer
        assert entry["shape_ids"]["count"] == expected, layer


def test_lod1_never_has_more_rects_than_lod0(sample_render):
    for layer in sample_render.header["layers"]:
        n0 = sample_render.header["lods"]["0"][layer]["rects"]["count"]
        n1 = sample_render.header["lods"]["1"][layer]["rects"]["count"]
        assert n1 <= n0


def test_device_layers_are_present_and_carry_no_nets(sample_render):
    """The transistor level. Nothing is traced on it -- these layers exist so
    the die looks like a die rather than metal hovering over nothing -- so
    every shape on one must have net -1, or clicking a net would light up
    scenery.
    """
    h = sample_render.header
    kinds = h["layer_kind"]
    device = [n for n in h["layers"] if kinds[n] == "device"]
    assert device[:2] == ["substrate", "nwell"], device
    assert set(device) >= {"substrate", "nwell", "diff", "poly"}

    # bottom to top: every device layer sits below every routing layer
    routing = [n for n in h["layers"] if kinds[n] == "routing"]
    assert h["layers"] == device + routing

    net_of_shape = _i32_array(sample_render, h["net_of_shape"])
    for name in device:
        entry = h["lods"]["0"][name]
        for sid in _i32_array(sample_render, entry["shape_ids"]):
            assert net_of_shape[sid] == -1, name


def test_the_substrate_is_one_rect_covering_the_die(sample_render):
    h = sample_render.header
    entry = h["lods"]["0"]["substrate"]
    assert entry["rects"]["count"] == 1
    assert list(_i32_array(sample_render, entry["rects"])) == h["bbox"]
    assert "substrate" in h["synthetic_layers"]


def test_contacts_are_dropped_from_the_zoomed_out_level(sample_render):
    """`licon1` is tens of thousands of sub-micron squares. They are real, and
    they are in L0, but at L1 they are far under a pixel and would roughly
    double the bundle to draw nothing -- so L1 drops them, exactly as it
    would a metal via.
    """
    h = sample_render.header
    assert h["lods"]["0"]["licon1"]["rects"]["count"] > 0
    assert h["lods"]["1"]["licon1"]["rects"]["count"] == 0


def test_tile_offsets_are_a_valid_csr(sample_render):
    cols = sample_render.header["tile_grid"]["cols"]
    rows = sample_render.header["tile_grid"]["rows"]
    assert (cols, rows) == (16, 16)
    for lod, layers in sample_render.header["lods"].items():
        for layer, entry in layers.items():
            offsets = _i32_array(sample_render, entry["tiles"])
            assert len(offsets) == cols * rows + 1
            assert offsets == sorted(offsets)
            assert offsets[-1] == entry["rects"]["count"]
            assert offsets[0] == 0


def test_every_rect_is_inside_the_die_bbox(sample_render):
    x0, y0, x1, y1 = sample_render.header["bbox"]
    for layer in sample_render.header["layers"]:
        for rect in _rects(sample_render, 0, layer):
            assert x0 <= rect[0] <= rect[2] <= x1
            assert y0 <= rect[1] <= rect[3] <= y1


def test_net_of_shape_covers_every_shape_id(sample_render):
    entry = sample_render.header["net_of_shape"]
    assert entry["count"] == sample_render.header["n_shapes"]
    n_nets = sample_render.header["n_nets"]
    for net_id in _i32_array(sample_render, entry):
        assert -1 <= net_id < n_nets


def test_net_shapes_is_the_exact_reverse_of_net_of_shape(sample_render):
    net_of_shape = _i32_array(sample_render, sample_render.header["net_of_shape"])
    offsets = _i32_array(sample_render, sample_render.header["net_shapes"]["offsets"])
    ids = _i32_array(sample_render, sample_render.header["net_shapes"]["ids"])

    expected: dict[int, set[int]] = {}
    for sid, net_id in enumerate(net_of_shape):
        if net_id != -1:
            expected.setdefault(net_id, set()).add(sid)

    n_nets = sample_render.header["n_nets"]
    assert len(offsets) == n_nets + 1
    for net_id in range(n_nets):
        got = set(ids[offsets[net_id] : offsets[net_id + 1]])
        assert got == expected.get(net_id, set())


def test_a_shapes_net_id_is_consistent_at_every_lod(sample_render):
    net_of_shape = _i32_array(sample_render, sample_render.header["net_of_shape"])
    for lod in "0", "1":
        for layer in sample_render.header["layers"]:
            for sid in _shape_ids(sample_render, int(lod), layer):
                assert 0 <= sid < len(net_of_shape)


def test_net_names_match_the_real_netlist(sample, sample_netlist):
    conn = trace_design(sample, {})
    nl, net_names = build_with_net_ids(sample, conn, {})
    assert nl.nets.keys() == sample_netlist.nets.keys()
    bundle = render.build(sample, conn, net_names)
    rendered_names = set(bundle.header["net_names"].values())
    for net in nl.nets:
        assert net in rendered_names


def test_unextracted_nets_are_exactly_the_ones_the_netlist_lacks(sample, sample_netlist):
    """The die view names metal the netlist never saw, and must say which

    Tracing finds every distinct piece of metal; extraction keeps only what
    lands on a logic cell's pin, so intra-cell li1 wiring (most of the die's
    nets, by count) gets an `n<id>` fallback name that looks exactly like a
    real net's. Right-clicking one used to offer "Open in Cone Walker", which
    could only answer `no such net`. This field is how the viewer tells them
    apart.
    """
    conn = trace_design(sample, {})
    nl, net_names = build_with_net_ids(sample, conn, {})
    bundle = render.build(sample, conn, net_names)
    header = bundle.header

    unextracted = header["unextracted_nets"]
    assert unextracted == sorted(unextracted)
    assert unextracted, "the sample has intra-cell wiring, so some net is unextracted"

    flagged = {header["net_names"][str(i)] for i in unextracted}
    assert flagged.isdisjoint(nl.nets)
    named = {header["net_names"][str(i)] for i in range(header["n_nets"])} - flagged
    assert named == set(nl.nets)
    assert sample_netlist.nets.keys() == nl.nets.keys()


def test_stack_has_the_real_sky130_numbers(sample_render):
    stack = {s["name"]: s for s in sample_render.header["stack"]}
    assert stack["li1"] == {"name": "li1", "z": 0.936, "thickness": 0.1, "via": False}
    assert stack["mcon"] == {
        "name": "mcon",
        "z": 1.036,
        "thickness": 0.33,
        "via": True,
    }
    assert stack["met1"]["z"] == 1.366
    assert stack["met1"]["thickness"] == 0.36

    ordered = sample_render.header["stack"]
    assert [s["name"] for s in ordered] == sorted(
        (s["name"] for s in ordered), key=lambda n: stack[n]["z"]
    ), "the stack must be listed bottom to top"

    # The *interconnect* is contiguous: every metal's top is the bottom of the
    # via tier above it, and that tier's top is the bottom of the next metal.
    # The 3D view depends on this -- it fills each metal down to the layer
    # below rather than drawing the via tiers, and a gap would show.
    interconnect = [s for s in ordered if s["z"] >= stack["li1"]["z"]]
    for a, b in zip(interconnect, interconnect[1:]):
        assert round(a["z"] + a["thickness"], 6) == b["z"], (a["name"], b["name"])

    # The device level is *not* contiguous in that sense and must not be
    # forced to be. `diff` and `tap` are the same physical tier -- n-tap and
    # p-tap diffusion -- so they share a z; and `licon1` runs from diffusion
    # all the way up to li1, passing the poly tier rather than stacking on it,
    # because a contact lands on both diffusion and poly. What must hold is
    # that the device level reaches li1 without leaving a hole under it.
    device = [s for s in ordered if s["z"] < stack["li1"]["z"]]
    assert device, "the device level is what stops the metal floating"
    top = max(round(s["z"] + s["thickness"], 6) for s in device)
    assert top == stack["li1"]["z"]


def test_instances_cover_every_cell_name_referenced(sample_render):
    entry = sample_render.header["instances"]
    flat = _i32_array(sample_render, entry)
    assert entry["stride"] == 6
    cell_names = sample_render.header["cell_names"]
    for i in range(0, len(flat), 6):
        x0, y0, x1, y1, orient, cell_id = flat[i : i + 6]
        assert x0 <= x1 and y0 <= y1
        assert 0 <= orient <= 7
        assert 0 <= cell_id < len(cell_names)


def test_build_is_deterministic(sample):
    conn = trace_design(sample, {})
    _, net_names = build_with_net_ids(sample, conn, {})
    a = render.build(sample, conn, net_names)
    b = render.build(sample, conn, net_names)
    assert a.header == b.header
    assert a.blob == b.blob


@pytest.mark.slow
def test_puzzle_lod1_reduces_shape_count(tech):
    from pathlib import Path

    from gdsx import loader

    puzzle = loader.load(
        Path(__file__).resolve().parents[1] / "samples" / "puzzle.gds", tech
    )
    conn = trace_design(puzzle, {})
    _, net_names = build_with_net_ids(puzzle, conn, {})
    bundle = render.build(puzzle, conn, net_names, lod1_min_area=1_000_000)
    total0 = sum(
        bundle.header["lods"]["0"][layer]["rects"]["count"]
        for layer in bundle.header["layers"]
    )
    total1 = sum(
        bundle.header["lods"]["1"][layer]["rects"]["count"]
        for layer in bundle.header["layers"]
    )
    assert total1 < total0
