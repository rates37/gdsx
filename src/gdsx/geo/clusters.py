"""Connected components of layout geometry, without boolean union.

`connectivity` asks a cluster exactly two questions: what is its extent, and
does this pin point land inside it. Neither needs the union outline of the
merged metal, which is what a real boolean engine spends its time computing.
So everything here is built on axis-aligned rectangles:

1. decompose every shape into rectangles (`rects_of`),
2. union rectangles that touch or overlap (`merge`),
3. answer point lookups by scanning a cluster's own rectangles.

Two rules govern the whole module, and getting either wrong produces a netlist
that is 99% right, which is worse than one that is obviously broken:

- **Comparisons are inclusive.** Two rectangles sharing exactly one edge are
  one piece of metal. Routing is drawn as abutting segments, so an exclusive
  comparison silently splits nearly every net in the design.
- **Coordinates are `int` database units, always.** Never float, not even for
  a comparison. At a 5 nm grid, float rounding turns "touching" into "not
  touching" and reintroduces the bug above invisibly.

One deliberate approximation: a round-ended path (`PATHTYPE 1`) is treated as
square-ended. For connectivity that can only ever add contact, by at most half
a width past a butt end, and Manhattan routing does not use round ends.

The union-find here duplicates `connectivity.UnionFind`. That is the import
layering's price: `geo/` may import nothing from the package, so the shared
one is out of reach.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Iterator

from .types import Box, Path, Point, Polygon, Shape, Text

__all__ = ["RectCluster", "merge", "rects_of"]

#: A rectangle as (x0, y0, x1, y1), inclusive of its boundary. A plain tuple
#: rather than a `Box`, because the component sweep touches millions of these.
Rect = tuple[int, int, int, int]


def _touches(a: Rect, b: Rect) -> bool:
    """True if two rectangles touch or overlap.

    Inclusive on both axes: `a[2] == b[0]` (a's right edge is b's left edge)
    counts as connected, and so does meeting at a single corner.
    """
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


#! decomposition


def rects_of(shape: Shape) -> list[Rect]:
    """Every rectangle covering `shape`, exactly.

    The cover is exact but not minimal: adjacent pieces abut, and abutting
    pieces union, so splitting one shape more finely than necessary cannot
    change any component.
    """
    if isinstance(shape, Box):
        if shape.empty():
            return []
        return [(shape.left, shape.bottom, shape.right, shape.top)]
    if isinstance(shape, Text):
        return []  # a label carries no geometry
    if isinstance(shape, Path):
        return _path_rects(shape)
    if isinstance(shape, Polygon):
        return _polygon_rects(shape)
    raise TypeError(f"cannot decompose {type(shape).__name__} into rectangles")


def _path_rects(path: Path) -> list[Rect]:
    """A path as one rectangle per segment, widened and end-extended.

    Corners need no filling: consecutive segments both cover the corner
    vertex, so they union regardless, and exact corner area is never asked
    for.
    """
    pts = path.points
    if not pts or path.width <= 0:
        return []
    if path.width % 2:
        raise ValueError(
            f"path width {path.width} is odd; half-width would lose half a "
            "database unit, and layout arithmetic here is exact"
        )
    half = path.width // 2

    if path.pathtype == 0:  # butt
        bgn = end = 0
    elif path.pathtype in (1, 2):  # round (approximated square), square
        bgn = end = half
    elif path.pathtype == 4:  # custom
        bgn, end = path.bgnextn, path.endextn
    else:
        raise ValueError(f"unsupported PATHTYPE {path.pathtype}")

    if len(pts) == 1:
        p = pts[0]
        return [
            (p.x - half - bgn, p.y - half - bgn, p.x + half + end, p.y + half + end)
        ]

    out: list[Rect] = []
    last = len(pts) - 2
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        # Extensions apply at the two ends of the whole path, not per segment
        start = bgn if i == 0 else 0
        finish = end if i == last else 0

        if a.y == b.y:  # horizontal
            if a.x <= b.x:
                x0, x1 = a.x - start, b.x + finish
            else:
                x0, x1 = b.x - finish, a.x + start
            out.append((x0, a.y - half, x1, a.y + half))
        elif a.x == b.x:  # vertical
            if a.y <= b.y:
                y0, y1 = a.y - start, b.y + finish
            else:
                y0, y1 = b.y - finish, a.y + start
            out.append((a.x - half, y0, a.x + half, y1))
        else:
            raise ValueError(
                f"path segment ({a}) -> ({b}) is diagonal; routing here is "
                "Manhattan and a diagonal would need real polygon handling"
            )
    return out


def _is_rectangle(points: tuple[Point, ...]) -> Rect | None:
    """The rectangle for a 4-point axis-aligned polygon, or None"""
    if len(points) != 4:
        return None
    xs = sorted(p.x for p in points)
    ys = sorted(p.y for p in points)
    if xs[0] == xs[1] and xs[2] == xs[3] and ys[0] == ys[1] and ys[2] == ys[3]:
        return (xs[0], ys[0], xs[3], ys[3])
    return None


def _vertical_edges(ring: tuple[Point, ...]) -> Iterator[tuple[int, int, int, int]]:
    """(x, ylo, yhi, direction) for each vertical edge of a rectilinear ring.

    `direction` is +1 for an upward edge and -1 for a downward one, which is
    what makes the winding count below work. A hole ring winds the opposite
    way to the hull, so holes fall out of the count for free.
    """
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if a.x == b.x:
            if a.y != b.y:
                yield (a.x, min(a.y, b.y), max(a.y, b.y), 1 if b.y > a.y else -1)
        elif a.y != b.y:
            raise ValueError(
                f"polygon edge ({a}) -> ({b}) is not axis-aligned; a 45 degree "
                "routing layer would need real polygon handling"
            )


def _polygon_rects(poly: Polygon) -> list[Rect]:
    """A rectilinear polygon decomposed by a vertical sweep.

    Between each adjacent pair of edge x coordinates, a y-interval is interior
    when the signed count of vertical edges to its left is non-zero. Every
    comparison stays integer: no slab midpoint is ever taken, because a
    midpoint of two integers is not one.
    """
    if not poly.holes:
        rect = _is_rectangle(poly.points)
        if rect is not None:
            return [rect]

    edges = list(_vertical_edges(poly.points))
    for hole in poly.holes:
        edges.extend(_vertical_edges(hole))
    if not edges:
        return []

    xs = sorted({e[0] for e in edges})
    ys = sorted({y for e in edges for y in (e[1], e[2])})

    out: list[Rect] = []
    for i in range(len(xs) - 1):
        left, right = xs[i], xs[i + 1]
        # Only edges at or left of this slab can enclose it
        active = [e for e in edges if e[0] <= left]
        run: tuple[int, int] | None = None
        for j in range(len(ys) - 1):
            low, high = ys[j], ys[j + 1]
            winding = sum(d for _, y0, y1, d in active if y0 <= low and high <= y1)
            if winding:
                # Extend the current run rather than emitting a sliver per cell
                run = (low, high) if run is None else (run[0], high)
            elif run is not None:
                out.append((left, run[0], right, run[1]))
                run = None
        if run is not None:
            out.append((left, run[0], right, run[1]))
    return out


#! clusters


class RectCluster:
    """One connected piece of metal, as the rectangles that make it up.

    `shape_ids` records which of the shapes handed to `merge` contributed, in
    ascending order, so a renderer can highlight a net's original geometry.
    """

    __slots__ = ("_bbox", "_starts", "rects", "shape_ids")

    def __init__(self, rects: list[Rect], shape_ids: tuple[int, ...]):
        ordered = sorted(rects)
        self.rects = tuple(ordered)
        self.shape_ids = shape_ids
        self._starts = [r[0] for r in ordered]
        self._bbox = Box(
            min(r[0] for r in ordered),
            min(r[1] for r in ordered),
            max(r[2] for r in ordered),
            max(r[3] for r in ordered),
        )

    @property
    def bbox(self) -> Box:
        return self._bbox

    def contains(self, pt: Point) -> bool:
        """Inclusive: a point on the cluster's boundary is inside it"""
        if not self._bbox.contains(pt):
            return False
        # Rectangles are sorted by x0, so anything past pt.x cannot contain it
        for i in range(bisect_right(self._starts, pt.x) - 1, -1, -1):
            x0, y0, x1, y1 = self.rects[i]
            if x0 <= pt.x <= x1 and y0 <= pt.y <= y1:
                return True
        return False

    def __repr__(self) -> str:
        return f"RectCluster({self._bbox}, {len(self.rects)} rects)"


def merge(shapes: Iterable[Shape]) -> list[RectCluster]:
    """Group shapes into connected components under "touches or overlaps".

    The returned order is the order components were first encountered while
    sweeping, which is an implementation detail: `connectivity.trace` sorts
    clusters into a canonical order before it numbers them.
    """
    rects: list[Rect] = []
    owners: list[int] = []
    for shape_id, shape in enumerate(shapes):
        for rect in rects_of(shape):
            rects.append(rect)
            owners.append(shape_id)
    if not rects:
        return []

    parent = list(range(len(rects)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    # Sweep in x: for each rectangle, only those starting at or before its
    # right edge can touch it, so the scan stops as soon as one starts past it.
    # The test below is `_touches` with its x half already established -- the
    # sort gives `a[0] <= b[0]` and the break gives `b[0] <= a[2]` -- inlined
    # because this loop runs into the millions on a real layer.
    order = sorted(range(len(rects)), key=lambda i: rects[i][0])
    n = len(order)
    for pos in range(n):
        i = order[pos]
        a = rects[i]
        for k in range(pos + 1, n):
            j = order[k]
            b = rects[j]
            if b[0] > a[2]:
                break
            if a[1] <= b[3] and b[1] <= a[3]:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for i in range(len(rects)):
        groups.setdefault(find(i), []).append(i)

    return [
        RectCluster(
            [rects[i] for i in members],
            tuple(sorted({owners[i] for i in members})),
        )
        for members in groups.values()
    ]
