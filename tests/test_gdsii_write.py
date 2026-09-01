"""Acceptance test for gdsii_write.py.

Rebuild samples/puzzle.gds's top structure from copied cell structures plus
freshly-authored boundaries/srefs/texts, and check that extracting the
rebuilt file gives a byte-identical netlist to extracting the original.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gdsx.build import gdsii_write as gw
from gdsx.core.context import Design
from gdsx.geo import gdsii
from gdsx.geo.types import Trans

PUZZLE = Path("samples/puzzle.gds")


@pytest.mark.parametrize(
    "value,as_bytes",
    [
        (0.001, bytes.fromhex("3e4189374bc6a7f0")),
        (1e-9, bytes.fromhex("3944b82fa09b5a54")),
        (1.0, None),
        (0.5, None),
        (-2.5, None),
        (0.0, None),
        (16.0, None),
        (1.0 / 16.0, None),
        (200.0, None),
    ],
)
def test_write_real64_round_trips(value, as_bytes):
    encoded = gw.write_real64(value)
    assert gdsii.parse_real64(encoded) == value
    if as_bytes is not None:
        assert encoded == as_bytes


def test_units_1nm_matches_the_reference_file_exactly():
    """The GDSII UNITS record must match the reference file's bytes exactly:
    copy them verbatim, or write an encoder and test it round-trips. This is
    the encoder; here is the round-trip against the actual bytes
    samples/puzzle.gds ships with.
    """
    data = PUZZLE.read_bytes()
    pos = 0
    while True:
        length = int.from_bytes(data[pos : pos + 2], "big")
        rtype = data[pos + 2]
        if rtype == gdsii.UNITS:
            payload = data[pos + 4 : pos + length]
            break
        pos += length
    assert gw.UNITS_1NM == payload


def _rebuild(tmp_path: Path) -> Path:
    lay = gdsii.read_file(PUZZLE)
    top = lay._lib.structures["puzzle"]

    used_cells = {ref.sname for ref in top.srefs}
    placements = [gw.Cell(ref.sname, ref.trans) for ref in top.srefs]
    boundaries = [(layer, dt, list(poly.points)) for layer, dt, poly in top.boundaries]
    texts = [(layer, tt, t.string, t.x, t.y) for layer, tt, t in top.texts]
    paths = list(top.paths)

    out_path = tmp_path / "rebuilt.gds"
    gw.write_gds(
        out_path,
        "puzzle",
        boundaries,
        placements,
        texts,
        used_cells,
        PUZZLE,
        paths=paths,
    )
    return out_path


def _canonical_nets(nl) -> dict[frozenset[str], tuple[str, ...]]:
    """net (as its frozen set of "inst/pin" members) -> (name, direction|"")

    Instance names are assigned from placement geometry (see
    `netlist.build_with_net_ids`'s sort key), so they are identical between
    the original and the rebuild regardless of GDS structure order. Net
    *names* ("n<id>") are not: `_layer_index` numbers layers in file
    encounter order, so the id a net gets depends on where in the stream its
    layer's shapes first appear -- copying the same structures in a
    different stream order is still the same circuit; the same net can end
    up with a different auto-generated name. Comparing by the frozenset of
    refs each net carries sidesteps that renaming entirely.
    """
    out = {}
    for name, refs in nl.nets.items():
        out[frozenset(refs)] = (name, nl.ports.get(name, ""))
    return out


def test_round_trip_extracts_identically(tmp_path):
    """Same instances, same cell types, same connectivity partition, same
    ports (up to net renaming -- see `_canonical_nets`), same floating/
    conflicts. This is exactly the equivalence the build acceptance test
    defines: a bijection between the two netlists' nets under which every
    instance's connections agree, keyed on instance name since naming is
    deterministic.
    """
    rebuilt = _rebuild(tmp_path)

    original_nl = Design.open(PUZZLE).netlist
    rebuilt_nl = Design.open(rebuilt).netlist

    assert [(i.name, i.cell) for i in rebuilt_nl.instances] == [
        (i.name, i.cell) for i in original_nl.instances
    ]
    assert rebuilt_nl.floating == original_nl.floating
    assert rebuilt_nl.conflicts == original_nl.conflicts
    assert len(rebuilt_nl.nets) == len(original_nl.nets)
    assert rebuilt_nl.power_nets == original_nl.power_nets

    original_by_partition = _canonical_nets(original_nl)
    rebuilt_by_partition = _canonical_nets(rebuilt_nl)
    assert set(rebuilt_by_partition) == set(original_by_partition)
    # every net's direction (not its auto-generated name) must agree
    for partition, (_, direction) in original_by_partition.items():
        assert rebuilt_by_partition[partition][1] == direction

    # instance connections agree once nets are identified by partition, not name
    partition_of = {
        name: frozenset(refs) for name, refs in original_nl.nets.items()
    }
    rebuilt_partition_of = {
        name: frozenset(refs) for name, refs in rebuilt_nl.nets.items()
    }
    for orig_inst, rebuilt_inst in zip(original_nl.instances, rebuilt_nl.instances):
        for pin, net in orig_inst.connections.items():
            assert (
                rebuilt_partition_of[rebuilt_inst.connections[pin]]
                == partition_of[net]
            )


def test_rebuild_is_byte_identical_across_runs(tmp_path):
    """Rebuilding twice must produce identical bytes: no set iteration, no
    unseeded randomness."""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    a = _rebuild(dir_a)
    b = _rebuild(dir_b)
    assert a.read_bytes() == b.read_bytes()


def test_sref_mirror_bit_round_trips(tmp_path):
    out_path = tmp_path / "mirror.gds"
    used = {"sky130_fd_sc_hd__inv_2"}
    placements = [
        gw.Cell("sky130_fd_sc_hd__inv_2", Trans(0, False, 0, 0)),
        gw.Cell("sky130_fd_sc_hd__inv_2", Trans(0, True, 2000, 5440)),
    ]
    gw.write_gds(out_path, "top", [], placements, [], used, PUZZLE)

    lay = gdsii.read_gds(out_path.read_bytes())
    top = lay._lib.structures["top"]
    assert [(r.sname, r.trans.mirror, r.trans.dx, r.trans.dy) for r in top.srefs] == [
        ("sky130_fd_sc_hd__inv_2", False, 0, 0),
        ("sky130_fd_sc_hd__inv_2", True, 2000, 5440),
    ]