"""The klayout geometry backend

This is the only module in `gdsx` allowed to import `klayout`. It wraps the
calls the extraction path was making directly, without changing any of them:
the same reader, the same recursive shape iterator, the same `Region.merged()`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import klayout.db as db

from .types import Box, Point, Polygon, Shape, Text, Trans
from .types import Path as GeoPath


def to_trans(t: db.ICplxTrans) -> Trans:
    """A klayout transform as a geo one.

    Magnification and non-orthogonal rotation are rejected rather than rounded:
    standard-cell layouts never use them, and silently rounding a 45 degree
    placement is far worse than failing.
    """
    if t.mag != 1.0:
        raise ValueError(f"magnification {t.mag} is not supported (only 1.0)")
    angle = t.angle
    if angle % 90 != 0:
        raise ValueError(f"rotation {angle} is not a multiple of 90 degrees")
    return Trans(int(angle // 90) % 4, t.is_mirror(), t.disp.x, t.disp.y)


def from_trans(t: Trans) -> db.ICplxTrans:
    return db.ICplxTrans(db.Trans(t.rot, t.mirror, t.dx, t.dy))


def to_box(b: db.Box) -> Box:
    return Box(b.left, b.bottom, b.right, b.top)


def _to_geo(shape: db.Shape, trans: db.ICplxTrans | None = None) -> Shape:
    """One klayout shape as a geo one, in `trans`'s coordinates"""
    if shape.is_text():
        text = shape.text
        if trans is not None:
            text = text.transformed(trans)
        return Text(text.string, text.x, text.y)

    if shape.is_box():
        box = shape.box
        if trans is not None:
            box = box.transformed(trans)
        return to_box(box)

    if shape.is_path():
        path = shape.path
        if trans is not None:
            path = path.transformed(trans)
        return GeoPath(
            tuple(Point(p.x, p.y) for p in path.each_point()),
            path.width,
            1 if path.is_round() else 4,
            path.bgn_ext,
            path.end_ext,
        )

    if shape.is_polygon() or shape.is_simple_polygon():
        poly = shape.polygon
        if trans is not None:
            poly = poly.transformed(trans)
        return Polygon(
            tuple(Point(p.x, p.y) for p in poly.each_point_hull()),
            tuple(
                tuple(Point(p.x, p.y) for p in poly.each_point_hole(i))
                for i in range(poly.holes())
            ),
        )

    # Silent data loss here would be invisible until a net came out wrong
    raise ValueError(f"unsupported shape type: {shape}")


def _to_db(shape: Shape):
    """A geo shape as something `db.Region` will accept. Texts have no area"""
    if isinstance(shape, Box):
        return db.Box(shape.left, shape.bottom, shape.right, shape.top)

    if isinstance(shape, Polygon):
        poly = db.Polygon([db.Point(p.x, p.y) for p in shape.points])
        for hole in shape.holes:
            poly.insert_hole([db.Point(p.x, p.y) for p in hole])
        return poly

    if isinstance(shape, GeoPath):
        return db.Path(
            [db.Point(p.x, p.y) for p in shape.points],
            shape.width,
            shape.bgnextn,
            shape.endextn,
            shape.pathtype == 1,
        )

    return None  # Text carries no geometry


class KLayoutCluster:
    """One merged polygon from a `db.Region`"""

    __slots__ = ("_bbox", "_poly")

    def __init__(self, poly: db.Polygon):
        self._poly = poly
        self._bbox = to_box(poly.bbox())

    @property
    def bbox(self) -> Box:
        return self._bbox

    def contains(self, pt: Point) -> bool:
        # db.Polygon.inside is inclusive of the boundary, which is what
        # connectivity needs: a label sitting on an edge is on that net.
        return self._bbox.contains(pt) and self._poly.inside(db.Point(pt.x, pt.y))


class KLayoutLayout:
    def __init__(self, layout: db.Layout):
        self._layout = layout
        self._index: dict[tuple[int, int], int] = {}
        for idx in layout.layer_indexes():
            info = layout.get_info(idx)
            self._index[(info.layer, info.datatype)] = idx

    @property
    def dbu(self) -> float:
        return self._layout.dbu

    def top_cells(self) -> list[str]:
        return [c.name for c in self._layout.top_cells()]

    def has_cell(self, cell: str) -> bool:
        return self._layout.cell(cell) is not None

    def cell_bbox(self, cell: str) -> Box:
        c = self._layout.cell(cell)
        if c is None:
            return Box.empty_box()
        return to_box(c.bbox())

    def layers(self) -> list[tuple[int, int]]:
        return list(self._index)

    def layer_index(self, ld: tuple[int, int]) -> int | None:
        return self._index.get(ld)

    def shapes(self, cell: str, layer_index: int) -> Iterator[Shape]:
        c = self._layout.cell(cell)
        if c is None:
            return
        for shape in c.shapes(layer_index).each():
            yield _to_geo(shape)

    def shapes_rec(self, cell: str, layer_index: int) -> Iterator[Shape]:
        c = self._layout.cell(cell)
        if c is None:
            return
        it = c.begin_shapes_rec(layer_index)
        while not it.at_end():
            yield _to_geo(it.shape(), it.trans())
            it.next()

    def leaf_instances(self, cell: str) -> Iterator[tuple[str, Trans]]:
        c = self._layout.cell(cell)
        if c is None:
            return
        for name, trans in _walk(self._layout, c, db.ICplxTrans()):
            yield name, to_trans(trans)


def _array_trans(inst: db.Instance):
    """Every placement of an instance, expanding regular arrays"""
    base = inst.cplx_trans
    if not inst.is_regular_array():
        yield base
        return
    a, b = inst.a, inst.b
    for i in range(inst.na):
        for j in range(inst.nb):
            yield db.ICplxTrans(db.Vector(a.x * i + b.x * j, a.y * i + b.y * j)) * base


def _walk(layout: db.Layout, cell: db.Cell, trans: db.ICplxTrans):
    """Recursively traverse the cell hierarchy

    Yields:
        (cell_name, transform) for every leaf-cell instance
    """
    for inst in cell.each_inst():
        child = layout.cell(inst.cell_index)
        for itrans in _array_trans(inst):
            here = trans * itrans

            if child.is_leaf():
                yield child.name, here
            else:
                yield from _walk(layout, child, here)


class KLayoutBackend:
    name = "klayout"

    def read_file(self, path: Path) -> KLayoutLayout:
        layout = db.Layout()
        layout.read(str(path))
        return KLayoutLayout(layout)

    def read_gds(self, data: bytes) -> KLayoutLayout:
        # klayout's Python API reads from a filename only, so bytes go via a
        # temporary file. The pure backend reads the buffer directly.
        with tempfile.NamedTemporaryFile(suffix=".gds") as fh:
            fh.write(data)
            fh.flush()
            return self.read_file(Path(fh.name))

    def merge_clusters(self, shapes: Iterable[Shape]) -> list[KLayoutCluster]:
        region = db.Region()
        for shape in shapes:
            got = _to_db(shape)
            if got is not None:
                region.insert(got)
        return [KLayoutCluster(poly) for poly in region.merged().each()]
