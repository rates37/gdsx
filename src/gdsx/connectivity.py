"""Flatten the routing, merge it per layer, stitch via vias

1. On one layer, anything that touches is one thing  -> ``Region.merged()``
   gives clusters on that layer.
2. A via connects the cluster under it to the cluster above it -> union-find
3. A cell pin is a point; whichever cluster contains that point is its net
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
import klayout.db as db

from .loader import Design

BUCKET = 5000  # grid size of the point-lookup index (5 um)


class UnionFind:
    def __init__(self) -> None:
        self.parent: list[int] = []
        self.size: list[int] = []

    def add(self) -> int:
        self.parent.append(len(self.parent))
        self.size.append(1)
        return len(self.parent) - 1

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]

        while self.parent[x] != root:  # path compress
            self.parent[x], x = root, self.parent[x]

        return root

    def union(self, a: int, b: int) -> None:
        # union by size
        ra, rb = self.find(a), self.find(b)

        if ra == rb:
            return

        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra

        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


class PointIndex:
    """Bucket grid over polygons"""

    def __init__(self, polygons: list[db.Polygon], ids: list[int]):
        self.polygons = polygons
        self.ids = ids
        self.buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, poly in enumerate(polygons):
            bb = poly.bbox()
            for bx in range(bb.left // BUCKET, bb.right // BUCKET + 1):
                for by in range(bb.bottom // BUCKET, bb.top // BUCKET + 1):
                    self.buckets[(bx, by)].append(i)

    def lookup(self, pt: db.Point) -> int | None:
        for i in self.buckets.get((pt.x // BUCKET, pt.y // BUCKET), ()):
            poly = self.polygons[i]
            if poly.bbox().contains(pt) and poly.inside(pt):
                return self.ids[i]
        return None


@dataclass
class Connectivity:
    """Result of tracing(clusters, their nets, and diagnostics)"""

    uf: UnionFind
    index: dict[str, PointIndex] = field(default_factory=dict)  # layer name -> index
    n_clusters: int = 0
    dangling_vias: list[tuple[str, db.Point]] = field(default_factory=list)

    def cluster_at(self, layer: str, pt: db.Point) -> int | None:
        idx = self.index.get(layer)
        return idx.lookup(pt) if idx else None

    def net_at(self, layer: str, pt: db.Point) -> int | None:
        cid = self.cluster_at(layer, pt)
        return None if cid is None else self.uf.find(cid)

    @property
    def nets(self) -> set[int]:
        return {self.uf.find(c) for c in range(self.n_clusters)}


def _region(design: Design, ld: tuple[int, int]) -> db.Region:
    idx = design.index_of(ld)
    if idx is None:
        return db.Region()
    return db.Region(design.top.begin_shapes_rec(idx))


def trace(design: Design, extra: dict[str, list] | None = None) -> Connectivity:
    """Trace connectivity. `extra` adds shapes per routing layer
    """
    uf = UnionFind()
    conn = Connectivity(uf=uf)
    extra = extra or {}

    # 1. Per-layer merge: Drawing and pin purposes go in together so that a pin
    # drawn only on the pin layer still fuses with the wire that touches it
    for rl in design.tech.routing:
        region = _region(design, rl.drawing) + _region(design, rl.pin)
        for box in extra.get(rl.name, ()):
            region.insert(box)
        region = region.merged()
        polys, ids = [], []
        for poly in region.each():
            polys.append(poly)
            ids.append(uf.add())
        conn.index[rl.name] = PointIndex(polys, ids)

    conn.n_clusters = len(uf.parent)

    # 2. Via stitching:
    for via in design.tech.vias:
        for poly in _region(design, via.layer).merged().each():
            pt = poly.bbox().center()
            lo = conn.cluster_at(via.below, pt)
            hi = conn.cluster_at(via.above, pt)
            if lo is None or hi is None:
                conn.dangling_vias.append((via.name, pt))
                continue
            uf.union(lo, hi)

    return conn
