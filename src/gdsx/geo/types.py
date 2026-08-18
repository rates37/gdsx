"""Geometry value types, independent of any geometry engine.

Every coordinate here is an ``int`` in database units. Layout arithmetic is
exact integer arithmetic and must stay that way: at a 5 nm grid, float rounding
turns "these two shapes touch" into "these two shapes don't", which silently
splits nets.

Containment is inclusive on every boundary. Two shapes that share exactly
one edge are connected, and a label sitting exactly on a shape's boundary is
inside it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.x},{self.y}"


@dataclass(frozen=True, slots=True)
class Vector:
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.x},{self.y}"


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned rectangle, inclusive of its boundary.

    An empty box is one whose bounds are inverted; `Box.empty_box()` matches
    the (1, 1, -1, -1) that KLayout uses.
    """

    left: int
    bottom: int
    right: int
    top: int

    @classmethod
    def empty_box(cls) -> Box:
        return cls(1, 1, -1, -1)

    @classmethod
    def around(cls, points) -> Box:
        """The bounding box of an iterable of points; empty if there are none"""
        pts = list(points)
        if not pts:
            return cls.empty_box()
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return cls(min(xs), min(ys), max(xs), max(ys))

    def empty(self) -> bool:
        return self.left > self.right or self.bottom > self.top

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.top - self.bottom

    def area(self) -> int:
        return 0 if self.empty() else self.width() * self.height()

    def center(self) -> Point:
        # Floor division, matching KLayout's rounding on odd spans
        return Point((self.left + self.right) // 2, (self.bottom + self.top) // 2)

    def contains(self, pt: Point) -> bool:
        # Inclusive on all four edges
        return self.left <= pt.x <= self.right and self.bottom <= pt.y <= self.top

    def overlaps(self, other: Box) -> bool:
        """True if the boxes touch or overlap. Inclusive: sharing one edge counts"""
        return (
            self.left <= other.right
            and other.left <= self.right
            and self.bottom <= other.top
            and other.bottom <= self.top
        )

    def __str__(self) -> str:
        return f"({self.left},{self.bottom};{self.right},{self.top})"


@dataclass(frozen=True, slots=True)
class Polygon:
    """A closed polygon, given as its hull and any holes.

    Vertices are not repeated; the last point joins back to the first.
    """

    points: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()

    @classmethod
    def from_box(cls, box: Box) -> Polygon:
        return cls(
            (
                Point(box.left, box.bottom),
                Point(box.right, box.bottom),
                Point(box.right, box.top),
                Point(box.left, box.top),
            )
        )

    def bbox(self) -> Box:
        return Box.around(self.points)

    def inside(self, pt: Point) -> bool:
        """Inclusive point-in-polygon: a point on an edge is inside"""
        if not _in_ring(self.points, pt):
            return False
        for hole in self.holes:
            # A point on a hole's edge is still on the polygon, so it stays in
            if _on_ring(hole, pt):
                return True
            if _in_ring(hole, pt):
                return False
        return True


def _on_ring(ring: tuple[Point, ...], pt: Point) -> bool:
    """True if `pt` lies exactly on one of the ring's edges"""
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        # Cross product zero means collinear; then check it is between a and b
        cross = (b.x - a.x) * (pt.y - a.y) - (b.y - a.y) * (pt.x - a.x)
        if cross != 0:
            continue
        if min(a.x, b.x) <= pt.x <= max(a.x, b.x) and min(a.y, b.y) <= pt.y <= max(
            a.y, b.y
        ):
            return True
    return False


def _in_ring(ring: tuple[Point, ...], pt: Point) -> bool:
    """Inclusive ray-cast containment against one closed ring"""
    if len(ring) < 3:
        return False
    if _on_ring(ring, pt):
        return True

    inside = False
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if (a.y > pt.y) != (b.y > pt.y):
            # x of the edge at pt.y, compared without dividing so it stays exact
            dy = b.y - a.y
            cross = (b.x - a.x) * (pt.y - a.y) - dy * (pt.x - a.x)
            if (cross > 0) == (dy > 0):
                inside = not inside
    return inside


@dataclass(frozen=True, slots=True)
class Path:
    """A centre-line with a width. `pathtype` follows GDSII: 0 butt, 1 round,
    2 square-extended by half the width, 4 custom via bgnextn/endextn.
    """

    points: tuple[Point, ...]
    width: int
    pathtype: int = 0
    bgnextn: int = 0
    endextn: int = 0

    def bbox(self) -> Box:
        box = Box.around(self.points)
        if box.empty():
            return box
        half = self.width // 2
        return Box(box.left - half, box.bottom - half, box.right + half, box.top + half)


@dataclass(frozen=True, slots=True)
class Text:
    string: str
    x: int
    y: int

    @property
    def point(self) -> Point:
        return Point(self.x, self.y)

    def bbox(self) -> Box:
        return Box(self.x, self.y, self.x, self.y)


#: Anything that can sit on a layer.
Shape = Box | Polygon | Path | Text


@dataclass(frozen=True, slots=True)
class Trans:
    """A rigid transform: mirror about the X axis, then rotate, then translate.

    That order is the one GDSII specifies for STRANS/ANGLE and the one KLayout's
    `ICplxTrans` implements. Composing in any other order silently mirrors cells
    about the wrong axis and produces plausible-looking layouts with wrong pin
    positions.

    Magnification is always 1: standard-cell layouts never scale, and silently
    rounding a scaled placement would be worse than failing.
    """

    rot: int = 0  # 0..3, quarter turns counter-clockwise
    mirror: bool = False  # about the X axis, applied before the rotation
    dx: int = 0
    dy: int = 0

    @classmethod
    def identity(cls) -> Trans:
        return cls()

    @property
    def disp(self) -> Vector:
        return Vector(self.dx, self.dy)

    def _linear(self, x: int, y: int) -> tuple[int, int]:
        """The rotation and mirror, without the translation"""
        if self.mirror:
            y = -y
        if self.rot == 0:
            return x, y
        if self.rot == 1:
            return -y, x
        if self.rot == 2:
            return -x, -y
        return y, -x

    def __mul__(self, other):
        if isinstance(other, Trans):
            # (a . b)(p) = Ra Ma (Rb Mb p + tb) + ta.  Mirroring about X flips
            # the sense of a rotation, so a mirrored `self` subtracts b's turns.
            rot = (
                (self.rot - other.rot) % 4
                if self.mirror
                else (self.rot + other.rot) % 4
            )
            dx, dy = self._linear(other.dx, other.dy)
            return Trans(rot, self.mirror != other.mirror, dx + self.dx, dy + self.dy)

        if isinstance(other, Point):
            x, y = self._linear(other.x, other.y)
            return Point(x + self.dx, y + self.dy)

        if isinstance(other, Vector):
            x, y = self._linear(other.x, other.y)
            return Vector(x + self.dx, y + self.dy)

        if isinstance(other, Box):
            if other.empty():
                return other
            corners = [
                self * Point(other.left, other.bottom),
                self * Point(other.right, other.bottom),
                self * Point(other.right, other.top),
                self * Point(other.left, other.top),
            ]
            return Box.around(corners)

        if isinstance(other, Polygon):
            return Polygon(
                tuple(self * p for p in other.points),
                tuple(tuple(self * p for p in hole) for hole in other.holes),
            )

        if isinstance(other, Path):
            return Path(
                tuple(self * p for p in other.points),
                other.width,
                other.pathtype,
                other.bgnextn,
                other.endextn,
            )

        if isinstance(other, Text):
            pt = self * other.point
            return Text(other.string, pt.x, pt.y)

        return NotImplemented

    def inverted(self) -> Trans:
        rot = self.rot if self.mirror else (-self.rot) % 4
        inv = Trans(rot, self.mirror)
        x, y = inv._linear(self.dx, self.dy)
        return Trans(rot, self.mirror, -x, -y)

    def __str__(self) -> str:
        # KLayout's ICplxTrans repr, which `netlist.build` and `physical.draw`
        # both use as a placement sort key. Changing it renames every instance.
        turn = f"m{self.rot * 45}" if self.mirror else f"r{self.rot * 90}"
        return f"{turn} *1 {self.dx},{self.dy}"
