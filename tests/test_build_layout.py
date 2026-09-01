"""The layout generator, end to end: place, route, write, extract, compare.

The build itself is the interesting test -- the extractor is a complete
oracle for whether the geometry is right, so most of what is asserted here
is "extract the file we just wrote and check it is the netlist we put in".
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import pytest

from gdsx import config, loader, netlist as N, synth
from gdsx.build import route as R
from gdsx.build.build import build
from gdsx.build.compare import compare
from gdsx.build.groups import by_cone, by_register, register_prefix
from gdsx.build import place as P
from gdsx.build.place import LayoutSpec, ROW_HEIGHT, SITE, measure_widths, place
from gdsx.build.spec import SpecError, load_spec
from gdsx.functions import is_sequential
from gdsx.netlist import Instance, Netlist

PUZZLE_DIR = Path("puzzles/1-warm-start")
REFERENCE = Path("samples/puzzle.gds")

SPEC_FILE = PUZZLE_DIR / "layout.json"


@pytest.fixture(scope="module")
def intended() -> Netlist:
    """Puzzle 1's netlist as synthesis produces it -- what the placer is given."""
    source = (PUZZLE_DIR / "warm_start.v").read_text()
    workdir = Path(tempfile.mkdtemp(prefix="warm_start_test_"))
    return synth.from_verilog(source, "warm_start", workdir)


@pytest.fixture(scope="module")
def spec(intended) -> LayoutSpec:
    """Puzzle 1's committed spec file, bound to its netlist -- the same path
    the CLI takes, so the tests cannot pass on a spec the build never uses.
    """
    return load_spec(SPEC_FILE, intended)


@pytest.fixture(scope="module")
def built(tmp_path_factory, intended, spec) -> Path:
    out = tmp_path_factory.mktemp("build") / "design.gds"
    workdir = Path(tempfile.mkdtemp(prefix="warm_start_build_"))
    build(
        (PUZZLE_DIR / "warm_start.v").read_text(),
        "warm_start",
        spec,
        out,
        workdir,
        REFERENCE,
    )
    return out


@pytest.fixture(scope="module")
def extracted(built) -> Netlist:
    return N.build(loader.load(built, config.load()))


# --- the acceptance test ----------------------------------------------------


def test_extracted_netlist_matches_the_intended_one(intended, extracted):
    """The whole comparison table at once: counts, cell types, floating,
    shorts, net count, connection isomorphism, ports.
    """
    result = compare(intended, extracted)
    assert result.ok, str(result)


def test_no_floating_pins_and_no_shorts(extracted):
    assert extracted.floating == []
    assert extracted.conflicts == []


def test_power_nets_are_one_each(extracted):
    """Rows alternate orientation, so all VPWR rails sit at odd multiples of
    the row height and all VGND rails at even ones. Without the straps
    `route.power` draws, each rail would extract as its own net.

    This is also the check that the met4/met5 mesh is wired correctly. A mesh
    that shorted the two nets, or whose drops missed the rails, both show up
    here: the first as one power net, the second as dozens.
    """
    assert extracted.power_nets == {"VPWR", "VGND"}


def test_power_grid_is_a_perpendicular_mesh(intended, spec):
    """The mesh the reference design has and the pack used to lack: straps
    one way on one layer, the other way on the next, tied at every crossing
    of the same net and dropped to the met1 rails.
    """
    placed = place(intended, spec, REFERENCE)
    result = R.route(
        intended, placed.placements, placed.cells,
        R.build_pin_table(config.load(), REFERENCE),
        placed.core_width, placed.rows,
    )
    assert result.grid_drops > 0
    straps = {"v": [], "h": []}
    for layer, datatype, pts in result.boundaries:
        axis = {R.GRID_V: "v", R.GRID_H: "h"}.get((layer, datatype))
        if axis is not None:
            straps[axis].append(R._box(pts))
    assert straps["v"] and straps["h"]

    # every strap is the measured width, and runs the full extent of its axis
    for x0, y0, x1, y1 in straps["v"]:
        assert x1 - x0 == R.STRAP_WIDTH
        assert (y0, y1) == (0, placed.rows * ROW_HEIGHT)
    for x0, y0, x1, y1 in straps["h"]:
        assert y1 - y0 == R.STRAP_WIDTH
        assert (x0, x1) == (0, placed.core_width)

    # straps come in VPWR/VGND pairs at the measured spacing, on both axes
    for axis, boxes in straps.items():
        i = 0 if axis == "v" else 1
        centres = sorted((b[i] + b[i + 2]) // 2 for b in boxes)
        assert len(centres) % 2 == 0
        for lo, hi in zip(centres[::2], centres[1::2]):
            assert hi - lo == R.STRAP_PAIR


def test_channel_rows_clear_the_band_detector(intended):
    """`place.CHANNEL_ROWS` exists to be wide enough that
    `physical.placement.bands` splits on it. The two numbers live in different
    layers and `build/` may not import `physical/`, so this is what stops them
    drifting apart and silently un-banding every channelled puzzle.
    """
    from gdsx.physical.placement import BAND_GAP

    assert P.BAND_GAP == BAND_GAP
    assert P.CHANNEL_ROWS > BAND_GAP * P.ROWS_PER_ORIGIN_LINE


def test_die_shape_follows_the_spec_aspect(intended, spec):
    """A spec with no channels should get the aspect it asked for. `spread`
    used to divide the row capacity without widening the core, so the achieved
    ratio came out as `aspect / spread` and nothing reported it.
    """
    assert not spec.channels, "puzzle 1 is the channel-free fixture"
    for spread in (1.0, 2.25, 5.0):
        placed = place(intended, spec, REFERENCE, spread=spread)
        achieved = placed.core_width / (placed.rows * ROW_HEIGHT)
        assert abs(achieved - spec.aspect) / spec.aspect < 0.25, (
            f"spread {spread}: asked 1:{1 / spec.aspect:.2f}, "
            f"got 1:{1 / achieved:.2f}"
        )


def test_every_verilog_port_is_labelled(intended, extracted):
    assert extracted.ports == intended.ports


def test_ports_are_pins_on_the_die_edge(intended, spec):
    """Data inputs left, outputs right, clock and async reset along the
    bottom. Each side is one column (or row) of pins outside the core,
    evenly pitched and in name order, so the die reads as a chip rather than
    as a bag of interior labels.
    """
    placed = place(intended, spec, REFERENCE)
    pins = R.port_pins(intended, placed.core_width, placed.rows)
    assert set(pins) == set(intended.ports)

    left = {n: p for n, p in pins.items() if p.x < 0}
    right = {n: p for n, p in pins.items() if p.x > placed.core_width}
    bottom = {n: p for n, p in pins.items() if p.y < 0}
    assert set(bottom) == {"clk", "rst_n"}
    assert set(left) == {"enable"}
    assert set(right) == {f"O[{i}]" for i in range(8)} | {"success"}

    for side in (left, right):
        assert len({p.x for p in side.values()}) == 1
        assert all(0 < p.y < placed.rows * R.ROW_HEIGHT for p in side.values())
    assert len({p.y for p in bottom.values()}) == 1
    # Name order runs down the right-hand column, so O[0] sits above O[7]
    ordered = [right[f"O[{i}]"].y for i in range(8)]
    assert ordered == sorted(ordered, reverse=True)


def test_rebuild_is_byte_identical(tmp_path, spec, built):
    """Rebuilding from the same inputs must produce the same bytes. Any set
    iteration or unseeded randomness in the generator shows up here and
    nowhere else.
    """
    again = tmp_path / "again.gds"
    workdir = Path(tempfile.mkdtemp(prefix="warm_start_again_"))
    build(
        (PUZZLE_DIR / "warm_start.v").read_text(),
        "warm_start",
        spec,
        again,
        workdir,
        REFERENCE,
    )
    assert again.read_bytes() == built.read_bytes()


def test_committed_puzzle_gds_is_what_the_generator_produces(built):
    """The checked-in `design.gds` must be rebuildable from its own RTL, or
    the file and the recipe have drifted apart.
    """
    assert (PUZZLE_DIR / "design.gds").read_bytes() == built.read_bytes()


# --- the placer ------------------------------------------------------------


def test_measured_widths_are_whole_sites():
    widths = measure_widths(REFERENCE)
    assert widths
    assert all(w % SITE == 0 and w > 0 for w in widths.values())


def test_cells_in_a_row_never_overlap(intended, spec):
    placed = place(intended, spec, REFERENCE)
    widths = measure_widths(REFERENCE)
    # Keyed on (y, mirror), not y: a mirrored row's origin is at its top, so
    # it shares a y with the unmirrored row above it and the two would
    # otherwise be merged into one apparently-overlapping row.
    rows: dict[tuple[int, bool], list[tuple[int, int]]] = {}
    for name, p in placed.placements.items():
        rows.setdefault((p.y, p.mirror), []).append((p.x, widths[placed.cells[name]]))
    for members in rows.values():
        members.sort()
        for (x0, w0), (x1, _) in zip(members, members[1:]):
            assert x0 + w0 <= x1, f"cell at x={x0} (width {w0}) runs into x={x1}"


def test_rows_alternate_orientation(intended, spec):
    """A mirrored row's origin is at the top of the row, so that it shares a
    power rail with the row below.
    """
    placed = place(intended, spec, REFERENCE)
    for p in placed.placements.values():
        row = (p.y // ROW_HEIGHT) - (1 if p.mirror else 0)
        assert p.mirror == (row % 2 == 1)


def test_utilisation_and_aspect_follow_the_spec(intended, spec):
    """The spec's `fill` is utilisation and `aspect` is the core's
    width/height. A placer that abuts every row and pads only the tail gets
    the area right and the utilisation wrong, which the router then cannot
    cope with.
    """
    placed = place(intended, spec, REFERENCE)
    widths = measure_widths(REFERENCE)
    logic = sum(widths[i.cell] for i in intended.instances) * ROW_HEIGHT
    die = placed.core_width * placed.rows * ROW_HEIGHT
    assert spec.fill * 0.8 <= logic / die <= spec.fill * 1.2
    assert 0.8 <= placed.core_width / (placed.rows * ROW_HEIGHT) <= 1.2


def test_banded_mode_keeps_each_group_contiguous(intended, spec):
    """`banded` means a group's cells occupy consecutive rows, in `order`."""
    placed = place(intended, spec, REFERENCE)
    def row_of(name: str) -> int:
        p = placed.placements[name]
        return p.y // ROW_HEIGHT - (1 if p.mirror else 0)

    seen: list[str] = []
    for name in sorted(
        (n for n in placed.placements if n in spec.groups),
        key=lambda n: (row_of(n), placed.placements[n].x),
    ):
        group = spec.groups[name]
        if not seen or seen[-1] != group:
            seen.append(group)
    assert seen == list(spec.order), seen


# --- the router ------------------------------------------------------------


def test_occupancy_rejects_shapes_within_the_spacing_margin():
    occ = R._Occupancy()
    occ.add((0, 0, 100, 100), "a")
    assert not occ.free((100, 0, 200, 100), "b")  # touching
    assert not occ.free((100 + R.SPACING, 0, 200, 100), "b")  # exactly at margin
    assert occ.free((101 + R.SPACING, 0, 200, 100), "b")  # clear of it
    assert occ.free((100, 0, 200, 100), "a")  # same net may overlap freely


def test_verify_catches_a_short():
    shapes = [
        (R.MET2, (0, 0, 100, 100), "a"),
        (R.MET2, (100, 0, 200, 100), "b"),
    ]
    with pytest.raises(R.RouteError, match="short on layer 69/20"):
        R.verify(shapes)


def test_verify_allows_the_same_net_to_overlap_itself():
    R.verify([(R.MET2, (0, 0, 100, 100), "a"), (R.MET2, (50, 0, 200, 100), "a")])


def test_routed_geometry_has_no_shorts(intended, spec):
    """`route` verifies itself, so this asserts the verification is reached
    rather than skipped -- and that the shapes it checked are the ones
    emitted.
    """
    placed = place(intended, spec, REFERENCE)
    pin_table = R.build_pin_table(config.load(), REFERENCE)
    result = R.route(
        intended,
        placed.placements,
        placed.cells,
        pin_table,
        placed.core_width,
        placed.rows,
    )
    # One met4 line per *link*, not per net: a net's pins are joined in a
    # chain of neighbouring pairs, so a net with n pins uses n-1 lines. See
    # route.signal's docstring for why one line per net does not scale. A
    # port spends one more, joining its die-edge pin to the chain, and that
    # link replaces the stub a single-pin net would otherwise have needed.
    signal_nets = [n for n in intended.nets if n not in intended.power_nets]
    ports = R.port_pins(intended, placed.core_width, placed.rows)
    links = sum(
        max(1, len(intended.nets[n]) - 1 + (1 if n in ports else 0))
        for n in signal_nets
    )
    assert result.tracks_used == links
    assert result.tracks_used > len(signal_nets)

    layers = Counter(layer for layer, _, _ in result.boundaries)
    for layer, _ in (R.MET1, R.MET2, R.MET3, R.MET4, R.MCON, R.VIA, R.VIA2, R.VIA3):
        assert layers[layer] > 0


def test_span_never_produces_a_degenerate_rectangle():
    """A zero-area polygon is dropped by a region merge, which turns into a
    via landing on nothing and a pin extraction reports as dangling.
    """
    for a, b in ((0, 0), (0, 10), (10, 0), (-5, 5)):
        lo, hi = R._span(a, b, R.STUB)
        assert hi - lo >= 2 * R.STUB


# --- group derivation ------------------------------------------------------


def test_register_prefix():
    assert register_prefix("acc_11") == "acc"
    assert register_prefix("O_0") == "O"
    assert register_prefix("n147") is None
    assert register_prefix("clk") is None


def test_every_instance_gets_a_group(intended, spec):
    assert set(spec.groups) == {i.name for i in intended.instances}
    assert set(spec.groups.values()) <= {"accumulator", "phase", "cycle"}


def test_unrecognised_register_name_is_an_error(intended):
    with pytest.raises(ValueError, match="no recognised group prefix"):
        by_register(intended, {"acc": "accumulator"})


def test_cone_groups_cover_only_combinational_cells(intended):
    """A cone stops at flops, so a flop is never claimed by one."""
    labels = by_cone(intended, [("success", "lock")])
    flops = {i.name for i in intended.instances if is_sequential(i.cell)}
    assert labels
    assert set(labels.values()) == {"lock"}
    assert not set(labels) & flops


def test_a_cone_rooted_at_a_flop_walks_that_flop_s_d(intended):
    """`O[7]` is a flop's Q in puzzle 1: the cone is the logic feeding it."""
    labels = by_cone(intended, [("O[7]", "acc")])
    assert labels
    assert set(labels.values()) == {"acc"}


def test_the_first_cone_to_claim_a_cell_keeps_it(intended):
    first = by_cone(intended, [("success", "a"), ("O[7]", "b")])
    second = by_cone(intended, [("O[7]", "b"), ("success", "a")])
    shared = set(first) & set(second)
    assert shared, "the two cones are expected to overlap"
    assert any(first[n] != second[n] for n in shared)


def test_cone_groups_override_prefix_groups(tmp_path, intended):
    path = tmp_path / "spec.json"
    path.write_text(
        '{"fill": 0.3, "order": ["accumulator", "phase", "cycle", "lock"], '
        '"groups_by_prefix": {"acc": "accumulator", "O": "accumulator", '
        '"ph": "phase", "cyc": "cycle"}, '
        '"groups_by_cone": [{"root": "success", "label": "lock"}]}'
    )
    spec = load_spec(path, intended)
    assert set(spec.groups) == {i.name for i in intended.instances}
    assert "lock" in set(spec.groups.values())


def test_spec_file_rejects_an_unknown_cone_root(tmp_path, intended):
    path = tmp_path / "bad.json"
    path.write_text(
        '{"fill": 0.3, "groups_by_cone": [{"root": "nope", "label": "x"}]}'
    )
    with pytest.raises(SpecError, match="not a net"):
        load_spec(path, intended)


def test_spec_file_rejects_an_unknown_field(tmp_path, intended):
    path = tmp_path / "bad.json"
    path.write_text('{"fill": 0.3, "utilisation": 0.3}')
    with pytest.raises(SpecError, match="unknown field"):
        load_spec(path, intended)


def test_spec_file_rejects_an_order_missing_a_group(tmp_path, intended):
    path = tmp_path / "bad.json"
    path.write_text(
        '{"fill": 0.3, "order": ["accumulator"], "groups_by_prefix": '
        '{"acc": "accumulator", "O": "accumulator", "ph": "phase", '
        '"cyc": "cycle"}}'
    )
    with pytest.raises(SpecError, match="does not mention"):
        load_spec(path, intended)


# --- the comparison itself -------------------------------------------------


def _tiny() -> Netlist:
    nl = Netlist(top="t")
    nl.instances = [
        Instance("i0", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "m"}),
        Instance("i1", "sky130_fd_sc_hd__inv_2", {"A": "m", "Y": "z"}),
    ]
    nl.nets = {"a": ["i0/A"], "m": ["i0/Y", "i1/A"], "z": ["i1/Y"]}
    nl.ports = {"a": "input", "z": "output"}
    return nl


def test_compare_accepts_a_renamed_copy():
    """The internal net and the instances are renamed; the ports are not.
    That is exactly the freedom extraction has.
    """
    other = _tiny()
    other.instances = [
        Instance("inv_2_9", "sky130_fd_sc_hd__inv_2", {"A": "a", "Y": "n7"}),
        Instance("inv_2_4", "sky130_fd_sc_hd__inv_2", {"A": "n7", "Y": "z"}),
    ]
    other.nets = {"a": ["inv_2_9/A"], "n7": [], "z": ["inv_2_4/Y"]}
    result = compare(_tiny(), other)
    assert result.ok, str(result)
    assert result.nets["m"] == "n7"


def test_compare_rejects_a_swapped_connection():
    broken = _tiny()
    broken.instances[1].connections["A"] = "a"  # second inverter fed the input
    result = compare(_tiny(), broken)
    assert not result.ok


def test_compare_reports_floating_and_conflicts():
    broken = _tiny()
    broken.floating = ["i1/A"]
    broken.conflicts = ["i0/Y"]
    result = compare(_tiny(), broken)
    assert not result.ok
    assert any("floating" in p for p in result.problems)
    assert any("conflicting" in p for p in result.problems)