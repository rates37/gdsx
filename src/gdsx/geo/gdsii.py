"""Pure-Python GDSII stream reader.

GDSII is a flat record stream with no compression and no back-references:
big-endian ``uint16 total_length`` (including the 4-byte header),
``uint8 record_type``, ``uint8 data_type``, then payload. Odd-length payloads
are padded with a trailing zero byte, already counted in `total_length`.

Everything not in `_HANDLED` is skipped by length, with one warning per
unknown record type so silent data loss is impossible.

Two traps, both load-bearing:

- `UNITS`/`MAG`/`ANGLE` use an 8-byte **excess-64, base-16** float, not IEEE
  754. See `parse_real64`.
- `STRANS`/`ANGLE`/`MAG` compose onto a placement as mirror-about-X, then
  rotate, then scale, then translate — see `compose`. Any other order
  silently mirrors cells about the wrong axis.

Coordinates stay `int` database units throughout; nothing here ever converts
to float.
"""

from __future__ import annotations

import struct
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path as FsPath

from .types import Box, Point, Polygon, Shape, Text, Trans, Vector
from .types import Path as GeoPath

__all__ = [
    "GdsARef",
    "GdsLayout",
    "GdsLibrary",
    "GdsSRef",
    "Structure",
    "compose",
    "parse",
    "parse_real64",
    "read_file",
    "read_gds",
]

#! record types (a subset of the GDSII stream format; see module docstring)

HEADER = 0x00
BGNLIB = 0x01
LIBNAME = 0x02
UNITS = 0x03
ENDLIB = 0x04
BGNSTR = 0x05
STRNAME = 0x06
ENDSTR = 0x07
BOUNDARY = 0x08
PATH = 0x09
SREF = 0x0A
AREF = 0x0B
TEXT = 0x0C
LAYER = 0x0D
DATATYPE = 0x0E
WIDTH = 0x0F
XY = 0x10
ENDEL = 0x11
SNAME = 0x12
COLROW = 0x13
TEXTTYPE = 0x16
PRESENTATION = 0x17
STRING = 0x19
STRANS = 0x1A
MAG = 0x1B
ANGLE = 0x1C
PATHTYPE = 0x21
BGNEXTN = 0x30
ENDEXTN = 0x31

_ELEMENT_START = (BOUNDARY, PATH, SREF, AREF, TEXT)

_HANDLED = frozenset(
    {
        HEADER,
        BGNLIB,
        LIBNAME,
        UNITS,
        ENDLIB,
        BGNSTR,
        STRNAME,
        ENDSTR,
        BOUNDARY,
        PATH,
        SREF,
        AREF,
        TEXT,
        LAYER,
        DATATYPE,
        WIDTH,
        XY,
        ENDEL,
        SNAME,
        COLROW,
        TEXTTYPE,
        PRESENTATION,
        STRING,
        STRANS,
        MAG,
        ANGLE,
        PATHTYPE,
        BGNEXTN,
        ENDEXTN,
    }
)


#! excess-64 real, and the record stream


def parse_real64(b: bytes) -> float:
    """GDSII 8-byte real: sign(1) exponent(7, excess-64, base 16) mantissa(56).

    This is not IEEE 754. The base is 16, and the mantissa is a pure fraction
    with no implicit leading 1. Getting this wrong scales the whole layout.
    """
    sign = -1.0 if b[0] & 0x80 else 1.0
    exponent = (b[0] & 0x7F) - 64
    mantissa = int.from_bytes(b[1:8], "big") / (1 << 56)
    return sign * mantissa * (16.0**exponent)


def _records(data: bytes) -> Iterator[tuple[int, int, memoryview]]:
    view = memoryview(data)
    pos = 0
    n = len(view)
    while pos + 4 <= n:
        length = int.from_bytes(view[pos : pos + 2], "big")
        if length == 0:
            raise ValueError(f"zero-length GDSII record at offset {pos}")
        rtype = view[pos + 2]
        dtype = view[pos + 3]
        end = pos + length
        if end > n:
            raise ValueError(f"GDSII record at offset {pos} overruns the buffer")
        yield rtype, dtype, view[pos + 4 : end]
        pos = end


def _int16(b: memoryview) -> list[int]:
    return list(struct.unpack(f">{len(b) // 2}h", b))


def _int32(b: memoryview) -> list[int]:
    return list(struct.unpack(f">{len(b) // 4}i", b))


def _real64_array(b: memoryview) -> list[float]:
    return [parse_real64(b[i : i + 8]) for i in range(0, len(b), 8)]


def _ascii(b: memoryview) -> str:
    return bytes(b).rstrip(b"\x00").decode("ascii")


def _mirror_bit(b: memoryview) -> bool:
    """STRANS bit 15 (the MSB of the first word) is mirror-about-X"""
    return bool(int.from_bytes(b[0:2], "big") & 0x8000)


#! transform composition


def compose(mirror_x: bool, angle_deg: float, mag: float, dx: int, dy: int) -> Trans:
    """STRANS/ANGLE/MAG/XY as one transform.

    Applied to a point p as translate(rotate(scale(mirror(p)))): mirror about
    X first, then rotate counter-clockwise by `angle_deg`, then scale by
    `mag`, then translate by (dx, dy). Standard-cell layouts never scale or
    rotate off-axis, so both are asserted rather than silently rounded.
    """
    if mag != 1.0:
        raise ValueError(f"magnification {mag} is not supported (only 1.0)")
    if angle_deg % 90 != 0:
        raise ValueError(f"rotation {angle_deg} is not a multiple of 90 degrees")
    return Trans(int(angle_deg // 90) % 4, mirror_x, dx, dy)


def _divide(v: Vector, n: int) -> Vector:
    """An AREF lattice vector, divided by its element count.

    The XY record gives the array's far corners, not its step vectors —
    forgetting to divide places every element `n` times too far apart.
    """
    if v.x % n != 0 or v.y % n != 0:
        raise ValueError(f"AREF lattice vector {v} does not divide evenly by {n}")
    return Vector(v.x // n, v.y // n)


#! parsed structure


@dataclass(frozen=True, slots=True)
class GdsSRef:
    sname: str
    trans: Trans


@dataclass(frozen=True, slots=True)
class GdsARef:
    sname: str
    base: Trans  # transform of the (0, 0) element; no array offset yet
    ncols: int
    nrows: int
    col_step: Vector
    row_step: Vector

    def instances(self) -> Iterator[Trans]:
        for i in range(self.ncols):
            for j in range(self.nrows):
                dx = self.col_step.x * i + self.row_step.x * j
                dy = self.col_step.y * i + self.row_step.y * j
                yield Trans(0, False, dx, dy) * self.base


@dataclass
class Structure:
    name: str
    boundaries: list[tuple[int, int, Polygon]] = field(default_factory=list)
    paths: list[tuple[int, int, GeoPath]] = field(default_factory=list)
    texts: list[tuple[int, int, Text]] = field(default_factory=list)
    srefs: list[GdsSRef] = field(default_factory=list)
    arefs: list[GdsARef] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.srefs and not self.arefs


@dataclass
class GdsLibrary:
    dbu: float  # microns per database unit
    structures: dict[str, Structure]


#! the parser


class _Element:
    """Accumulator for the record group between an element-start and ENDEL"""

    __slots__ = (
        "kind",
        "layer",
        "datatype",
        "texttype",
        "width",
        "pathtype",
        "bgnextn",
        "endextn",
        "xy",
        "sname",
        "mirror",
        "angle",
        "mag",
        "colrow",
        "string",
    )

    def __init__(self, kind: int):
        self.kind = kind
        self.layer: int | None = None
        self.datatype: int | None = None
        self.texttype: int | None = None
        self.width = 0
        self.pathtype = 0
        self.bgnextn = 0
        self.endextn = 0
        self.xy: list[Point] = []
        self.sname: str | None = None
        self.mirror = False
        self.angle = 0.0
        self.mag = 1.0
        self.colrow: tuple[int, int] | None = None
        self.string: str | None = None


def parse(data: bytes) -> GdsLibrary:
    """Parse a GDSII stream from bytes or a memoryview into a `GdsLibrary`"""
    structures: dict[str, Structure] = {}
    dbu: float | None = None
    cur: Structure | None = None
    elem: _Element | None = None
    warned: set[int] = set()

    for rtype, dtype, payload in _records(data):
        if rtype == UNITS:
            dbu = _real64_array(payload)[1] * 1e6
        elif rtype == BGNSTR:
            cur = Structure(name="")
        elif rtype == STRNAME:
            assert cur is not None
            cur.name = _ascii(payload)
            structures[cur.name] = cur
        elif rtype == ENDSTR:
            cur = None
        elif rtype in _ELEMENT_START:
            elem = _Element(rtype)
        elif rtype == LAYER:
            assert elem is not None
            elem.layer = _int16(payload)[0]
        elif rtype == DATATYPE:
            assert elem is not None
            elem.datatype = _int16(payload)[0]
        elif rtype == TEXTTYPE:
            assert elem is not None
            elem.texttype = _int16(payload)[0]
        elif rtype == WIDTH:
            assert elem is not None
            elem.width = _int32(payload)[0]
        elif rtype == XY:
            assert elem is not None
            ints = _int32(payload)
            elem.xy = [Point(ints[i], ints[i + 1]) for i in range(0, len(ints), 2)]
        elif rtype == SNAME:
            assert elem is not None
            elem.sname = _ascii(payload)
        elif rtype == COLROW:
            assert elem is not None
            vals = _int16(payload)
            elem.colrow = (vals[0], vals[1])
        elif rtype == STRANS:
            assert elem is not None
            elem.mirror = _mirror_bit(payload)
        elif rtype == MAG:
            assert elem is not None
            elem.mag = _real64_array(payload)[0]
        elif rtype == ANGLE:
            assert elem is not None
            elem.angle = _real64_array(payload)[0]
        elif rtype == PATHTYPE:
            assert elem is not None
            elem.pathtype = _int16(payload)[0]
        elif rtype == BGNEXTN:
            assert elem is not None
            elem.bgnextn = _int32(payload)[0]
        elif rtype == ENDEXTN:
            assert elem is not None
            elem.endextn = _int32(payload)[0]
        elif rtype == STRING:
            assert elem is not None
            elem.string = _ascii(payload)
        elif rtype == ENDEL:
            assert cur is not None and elem is not None
            _finish_element(cur, elem)
            elem = None
        elif rtype == ENDLIB:
            break
        elif rtype in (HEADER, BGNLIB, LIBNAME, PRESENTATION):
            pass  # recognised, and nothing here is needed downstream
        elif rtype not in warned:
            warned.add(rtype)
            warnings.warn(
                f"gdsx.geo.gdsii: skipping unknown record type 0x{rtype:02x}",
                stacklevel=2,
            )

    if dbu is None:
        raise ValueError("GDSII stream has no UNITS record")
    return GdsLibrary(dbu=dbu, structures=structures)


def _finish_element(cur: Structure, elem: _Element) -> None:
    if elem.kind == BOUNDARY:
        pts = elem.xy
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        assert elem.layer is not None and elem.datatype is not None
        cur.boundaries.append((elem.layer, elem.datatype, Polygon(tuple(pts))))

    elif elem.kind == PATH:
        assert elem.layer is not None and elem.datatype is not None
        cur.paths.append(
            (
                elem.layer,
                elem.datatype,
                GeoPath(
                    tuple(elem.xy),
                    elem.width,
                    elem.pathtype,
                    elem.bgnextn,
                    elem.endextn,
                ),
            )
        )

    elif elem.kind == TEXT:
        if len(elem.xy) != 1:
            raise ValueError(f"TEXT element has {len(elem.xy)} points, expected 1")
        assert (
            elem.layer is not None
            and elem.texttype is not None
            and elem.string is not None
        )
        pt = elem.xy[0]
        cur.texts.append((elem.layer, elem.texttype, Text(elem.string, pt.x, pt.y)))

    elif elem.kind == SREF:
        if len(elem.xy) != 1:
            raise ValueError(f"SREF element has {len(elem.xy)} points, expected 1")
        assert elem.sname is not None
        pt = elem.xy[0]
        trans = compose(elem.mirror, elem.angle, elem.mag, pt.x, pt.y)
        cur.srefs.append(GdsSRef(elem.sname, trans))

    elif elem.kind == AREF:
        if len(elem.xy) != 3:
            raise ValueError(f"AREF element has {len(elem.xy)} points, expected 3")
        if elem.colrow is None:
            raise ValueError("AREF element has no COLROW record")
        assert elem.sname is not None
        p0, p1, p2 = elem.xy
        ncols, nrows = elem.colrow
        base = compose(elem.mirror, elem.angle, elem.mag, p0.x, p0.y)
        col_step = _divide(Vector(p1.x - p0.x, p1.y - p0.y), ncols)
        row_step = _divide(Vector(p2.x - p0.x, p2.y - p0.y), nrows)
        cur.arefs.append(GdsARef(elem.sname, base, ncols, nrows, col_step, row_step))


#! Layout protocol implementation


def _union(a: Box, b: Box) -> Box:
    if a.empty():
        return b
    if b.empty():
        return a
    return Box(
        min(a.left, b.left),
        min(a.bottom, b.bottom),
        max(a.right, b.right),
        max(a.top, b.top),
    )


class GdsLayout:
    """A parsed GDSII library, exposing the `geo.protocol.Layout` surface"""

    def __init__(self, library: GdsLibrary):
        self._lib = library
        self._layer_index: dict[tuple[int, int], int] = {}
        self._referenced: set[str] = set()
        self._bbox_cache: dict[str, Box] = {}
        for s in library.structures.values():
            for layer, datatype, _ in s.boundaries:
                self._layer_index.setdefault((layer, datatype), len(self._layer_index))
            for layer, datatype, _ in s.paths:
                self._layer_index.setdefault((layer, datatype), len(self._layer_index))
            for layer, texttype, _ in s.texts:
                self._layer_index.setdefault((layer, texttype), len(self._layer_index))
            for ref in s.srefs:
                self._referenced.add(ref.sname)
            for aref in s.arefs:
                self._referenced.add(aref.sname)

    @property
    def dbu(self) -> float:
        return self._lib.dbu

    def top_cells(self) -> list[str]:
        return [name for name in self._lib.structures if name not in self._referenced]

    def has_cell(self, cell: str) -> bool:
        return cell in self._lib.structures

    def cell_bbox(self, cell: str) -> Box:
        return self._bbox(cell)

    def layers(self) -> list[tuple[int, int]]:
        return list(self._layer_index)

    def layer_index(self, ld: tuple[int, int]) -> int | None:
        return self._layer_index.get(ld)

    def shapes(self, cell: str, layer_index: int) -> Iterator[Shape]:
        s = self._lib.structures.get(cell)
        if s is None:
            return
        yield from self._own_shapes(s, layer_index)

    def shapes_rec(self, cell: str, layer_index: int) -> Iterator[Shape]:
        yield from self._shapes_rec(cell, layer_index, Trans.identity())

    def leaf_instances(self, cell: str) -> Iterator[tuple[str, Trans]]:
        yield from self._leaf_instances(cell, Trans.identity())

    # -- internals --

    def _own_shapes(self, s: Structure, layer_index: int) -> Iterator[Shape]:
        for layer, datatype, poly in s.boundaries:
            if self._layer_index.get((layer, datatype)) == layer_index:
                yield poly
        for layer, datatype, path in s.paths:
            if self._layer_index.get((layer, datatype)) == layer_index:
                yield path
        for layer, texttype, text in s.texts:
            if self._layer_index.get((layer, texttype)) == layer_index:
                yield text

    def _shapes_rec(self, name: str, layer_index: int, trans: Trans) -> Iterator[Shape]:
        s = self._lib.structures.get(name)
        if s is None:
            return
        for shape in self._own_shapes(s, layer_index):
            yield trans * shape
        for ref in s.srefs:
            yield from self._shapes_rec(ref.sname, layer_index, trans * ref.trans)
        for aref in s.arefs:
            for itrans in aref.instances():
                yield from self._shapes_rec(aref.sname, layer_index, trans * itrans)

    def _leaf_instances(self, name: str, trans: Trans) -> Iterator[tuple[str, Trans]]:
        s = self._lib.structures.get(name)
        if s is None:
            return
        for ref in s.srefs:
            yield from self._place(ref.sname, trans * ref.trans)
        for aref in s.arefs:
            for itrans in aref.instances():
                yield from self._place(aref.sname, trans * itrans)

    def _place(self, name: str, here: Trans) -> Iterator[tuple[str, Trans]]:
        child = self._lib.structures.get(name)
        if child is not None and not child.is_leaf():
            yield from self._leaf_instances(name, here)
        else:
            yield name, here

    def _bbox(self, name: str) -> Box:
        cached = self._bbox_cache.get(name)
        if cached is not None:
            return cached
        s = self._lib.structures.get(name)
        box = Box.empty_box()
        if s is not None:
            for _, _, poly in s.boundaries:
                box = _union(box, poly.bbox())
            for _, _, path in s.paths:
                box = _union(box, path.bbox())
            for _, _, text in s.texts:
                box = _union(box, text.bbox())
            for ref in s.srefs:
                box = _union(box, ref.trans * self._bbox(ref.sname))
            for aref in s.arefs:
                child_box = self._bbox(aref.sname)
                for itrans in aref.instances():
                    box = _union(box, itrans * child_box)
        self._bbox_cache[name] = box
        return box


def read_gds(data: bytes) -> GdsLayout:
    """Open a layout from raw bytes, so a browser can pass an ArrayBuffer"""
    return GdsLayout(parse(data))


def read_file(path: FsPath) -> GdsLayout:
    return read_gds(FsPath(path).read_bytes())
