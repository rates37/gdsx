"""A GDSII writer.

GDSII is a flat record stream -- see `geo/gdsii.py`'s module docstring for the
format. This module only emits the six record groups a standard-cell layout
needs (preamble, structure, boundary, sref, text, endlib); record-type
constants are imported from the reader rather than redefined.

Cell geometry is never re-serialised: `write_gds` splices the raw bytes of
each used structure straight out of a reference file (see
`geo.gdsii.structure_byte_ranges`), which is the only way to be sure a
polygon nobody looked at did not pick up a subtle bug in translation.

Everything else -- the top-level structure's boundaries, srefs and text
labels -- is authored fresh from integer database-unit coordinates. No
floats reach a GDS coordinate anywhere in this module.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ..geo import gdsii
from ..geo.gdsii import (
    ANGLE,
    BGNEXTN,
    BGNLIB,
    BGNSTR,
    BOUNDARY,
    DATATYPE,
    ENDEL,
    ENDEXTN,
    ENDLIB,
    ENDSTR,
    HEADER,
    LAYER,
    LIBNAME,
    PATH,
    PATHTYPE,
    SNAME,
    SREF,
    STRANS,
    STRING,
    STRNAME,
    TEXT,
    TEXTTYPE,
    UNITS,
    WIDTH,
    XY,
)
from ..geo.types import Path as GeoPath
from ..geo.types import Point, Trans

__all__ = [
    "Cell",
    "UNITS_1NM",
    "boundary",
    "path",
    "preamble",
    "sref",
    "structure",
    "text",
    "write_gds",
    "write_real64",
]

# GDSII data-type codes (see the module docstring in geo/gdsii.py)
_NODATA = 0x00
_BITARRAY = 0x01
_INT16 = 0x02
_INT32 = 0x03
_REAL64 = 0x05
_ASCII = 0x06


def write_real64(v: float) -> bytes:
    """Inverse of `geo.gdsii.parse_real64`: sign(1) exponent(7, excess-64,
    base 16) mantissa(56), 8 bytes total. Not IEEE 754 -- see that function's
    docstring. `tests/test_gdsii_write.py` round-trips this against it.
    """
    if v == 0.0:
        return b"\x00" * 8
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    exponent = 0
    while v >= 1.0:
        v /= 16.0
        exponent += 1
    while v < 1.0 / 16.0:
        v *= 16.0
        exponent -= 1
    mantissa = round(v * (1 << 56))
    if mantissa >= (1 << 56):  # rounding pushed it up to the next power of 16
        mantissa >>= 4
        exponent += 1
    return bytes([(exponent + 64) | sign]) + mantissa.to_bytes(7, "big")


def _record(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    if len(payload) % 2:
        payload += b"\x00"
    length = 4 + len(payload)
    return struct.pack(">HBB", length, rtype, dtype) + payload


def _int16(vals: list[int]) -> bytes:
    return struct.pack(f">{len(vals)}h", *vals)


def _uint16(vals: list[int]) -> bytes:
    return struct.pack(f">{len(vals)}H", *vals)


def _int32(vals: list[int]) -> bytes:
    return struct.pack(f">{len(vals)}i", *vals)


def _ascii(s: str) -> bytes:
    return s.encode("ascii")


# 1 database unit = 1 nm, 1 user unit = 1 micron -- verbatim match to
# samples/puzzle.gds's own UNITS record (measured, see layout-guide.md §7.1:
# "either read the reference file's raw UNITS bytes ... or write an encoder
# and test it round-trips"; this is the encoder, tested against those bytes).
UNITS_1NM = write_real64(0.001) + write_real64(1e-9)

# A fixed, all-zero BGNLIB/BGNSTR timestamp. GDSII's 12-int16 date fields are
# mod-time and access-time; using real timestamps here would make every
# rebuild differ, breaking the byte-identical rebuild requirement (layout
# guide.md §10 point 8).
_NO_TIMESTAMP = _int16([0] * 12)


def preamble(libname: str = "GDSX", units: bytes = UNITS_1NM) -> bytes:
    out = _record(HEADER, _INT16, _int16([600]))
    out += _record(BGNLIB, _INT16, _NO_TIMESTAMP)
    out += _record(LIBNAME, _ASCII, _ascii(libname))
    out += _record(UNITS, _REAL64, units)
    return out


def boundary(layer: int, datatype: int, points: list[Point]) -> bytes:
    """A closed polygon. `points` is the hull, open (first point not repeated)."""
    pts = [*points, points[0]]
    xy: list[int] = []
    for p in pts:
        xy += [p.x, p.y]
    out = _record(BOUNDARY, _NODATA)
    out += _record(LAYER, _INT16, _int16([layer]))
    out += _record(DATATYPE, _INT16, _int16([datatype]))
    out += _record(XY, _INT32, _int32(xy))
    out += _record(ENDEL, _NODATA)
    return out


def path(layer: int, datatype: int, p: GeoPath) -> bytes:
    """A routed wire: a centre-line with a width, per `geo.types.Path`."""
    out = _record(PATH, _NODATA)
    out += _record(LAYER, _INT16, _int16([layer]))
    out += _record(DATATYPE, _INT16, _int16([datatype]))
    if p.pathtype:
        out += _record(PATHTYPE, _INT16, _int16([p.pathtype]))
    out += _record(WIDTH, _INT32, _int32([p.width]))
    if p.pathtype == 4:
        out += _record(BGNEXTN, _INT32, _int32([p.bgnextn]))
        out += _record(ENDEXTN, _INT32, _int32([p.endextn]))
    xy: list[int] = []
    for pt in p.points:
        xy += [pt.x, pt.y]
    out += _record(XY, _INT32, _int32(xy))
    out += _record(ENDEL, _NODATA)
    return out


def sref(sname: str, trans: Trans) -> bytes:
    """A cell placement. Only axis mirror and quarter-turn rotation are
    representable, same restriction `geo.gdsii.compose` enforces on read.
    """
    out = _record(SREF, _NODATA)
    out += _record(SNAME, _ASCII, _ascii(sname))
    bits = 0x8000 if trans.mirror else 0
    out += _record(STRANS, _BITARRAY, _uint16([bits]))
    if trans.rot:
        out += _record(ANGLE, _REAL64, write_real64(float(trans.rot * 90)))
    out += _record(XY, _INT32, _int32([trans.dx, trans.dy]))
    out += _record(ENDEL, _NODATA)
    return out


def text(layer: int, texttype: int, string: str, x: int, y: int) -> bytes:
    out = _record(TEXT, _NODATA)
    out += _record(LAYER, _INT16, _int16([layer]))
    out += _record(TEXTTYPE, _INT16, _int16([texttype]))
    out += _record(XY, _INT32, _int32([x, y]))
    out += _record(STRING, _ASCII, _ascii(string))
    out += _record(ENDEL, _NODATA)
    return out


def structure(name: str, body: bytes) -> bytes:
    out = _record(BGNSTR, _INT16, _NO_TIMESTAMP)
    out += _record(STRNAME, _ASCII, _ascii(name))
    out += body
    out += _record(ENDSTR, _NODATA)
    return out


@dataclass(frozen=True)
class Cell:
    """One placed instance of a copied library structure."""

    name: str
    trans: Trans


def write_gds(
    out_path: Path,
    top_name: str,
    boundaries: list[tuple[int, int, list[Point]]],
    placements: list[Cell],
    texts: list[tuple[int, int, str, int, int]],
    used_cells: set[str],
    reference_path: Path,
    libname: str = "GDSX",
    paths: list[tuple[int, int, GeoPath]] = (),
) -> None:
    """Write a GDSII file: one authored top structure, plus every structure
    in `used_cells` copied byte-for-byte out of `reference_path`.

    `boundaries` is `(layer, datatype, points)`; `texts` is
    `(layer, texttype, string, x, y)`; `paths` is `(layer, datatype,
    geo.types.Path)`. All three are top-level shapes (power rails, routing,
    port labels) -- routed wires in samples/puzzle.gds itself are drawn as
    PATH elements, not BOUNDARY rectangles, so both element kinds have to
    round-trip for a rebuild to extract the same netlist. `placements` are
    `Cell` srefs into the copied structures. Structures are copied in sorted
    order, so the output is deterministic across runs for the same inputs --
    required by layout-guide.md §10 point 8.
    """
    data = Path(reference_path).read_bytes()
    ranges = gdsii.structure_byte_ranges(data)
    missing = used_cells - ranges.keys()
    if missing:
        raise KeyError(f"{reference_path} has no structures named {sorted(missing)}")

    body = b""
    for layer, datatype, points in boundaries:
        body += boundary(layer, datatype, points)
    for layer, datatype, p in paths:
        body += path(layer, datatype, p)
    for cell in placements:
        body += sref(cell.name, cell.trans)
    for layer, texttype, string, x, y in texts:
        body += text(layer, texttype, string, x, y)

    out = preamble(libname)
    out += structure(top_name, body)
    for name in sorted(used_cells):
        s, e = ranges[name]
        out += data[s:e]
    out += _record(ENDLIB, _NODATA)

    Path(out_path).write_bytes(out)