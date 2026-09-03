"""Flatten the routing, merge it per layer, stitch via vias

1. On one layer, anything that touches is one thing -> ``geo.merge_clusters``
   gives clusters on that layer.
2. A via connects the cluster under it to the cluster above it -> union-find
3. A cell pin is a point; whichever cluster contains that point is its net
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import chain
from typing import Callable

from . import geo
from .geo import types as g
from .loader import Design

BUCKET = 5000  # grid size of the point-lookup index (5 um)

Progress = Callable[[str, int, int], None]


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
    """Bucket grid over clusters"""

    def __init__(self, clusters: list[geo.Cluster], ids: list[int]):
        self.clusters = clusters
        self.ids = ids
        self.buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, cluster in enumerate(clusters):
            bb = cluster.bbox
            for bx in range(bb.left // BUCKET, bb.right // BUCKET + 1):
                for by in range(bb.bottom // BUCKET, bb.top // BUCKET + 1):
                    self.buckets[(bx, by)].append(i)

    def lookup(self, pt: g.Point) -> int | None:
        for i in self.buckets.get((pt.x // BUCKET, pt.y // BUCKET), ()):
            if self.clusters[i].contains(pt):
                return self.ids[i]
        return None


@dataclass
class Connectivity:
    """Result of tracing(clusters, their nets, and diagnostics)"""

    uf: UnionFind
    index: dict[str, PointIndex] = field(default_factory=dict)  # layer name -> index
    n_clusters: int = 0
    dangling_vias: list[tuple[str, g.Point]] = field(default_factory=list)

    def cluster_at(self, layer: str, pt: g.Point) -> int | None:
        idx = self.index.get(layer)
        return idx.lookup(pt) if idx else None

    def net_at(self, layer: str, pt: g.Point) -> int | None:
        cid = self.cluster_at(layer, pt)
        return None if cid is None else self.uf.find(cid)

    @property
    def nets(self) -> set[int]:
        return {self.uf.find(c) for c in range(self.n_clusters)}


def _shapes(design: Design, ld: tuple[int, int]):
    """Every shape on a (layer, datatype), flattened into top-cell coordinates"""
    idx = design.index_of(ld)
    if idx is None:
        return iter(())
    return design.layout.shapes_rec(design.top, idx)


def _order(cluster: geo.Cluster) -> tuple[int, int, int, int]:
    """Sort key for a cluster: its bounding box, bottom-left first.

    The bounding box is the only thing every backend agrees on. A cluster's
    rectangle decomposition differs between them, so a key counting or
    comparing pieces would reintroduce exactly the divergence this removes.
    """
    bb = cluster.bbox
    return (bb.bottom, bb.left, bb.top, bb.right)


def _canonical(clusters: list[geo.Cluster], layer: str) -> list[geo.Cluster]:
    """Routing-layer clusters in a backend-independent order.

    Cluster order is net numbering: the first cluster on the first routing
    layer is `n0`. Each geometry backend finds the same clusters but emits
    them in its own order (klayout's is its merge scanline's, which is not
    reproducible in pure Python) so the order is imposed here instead, at
    the one place where the numbering is decided.
    """
    ordered = sorted(clusters, key=_order)
    for i in range(1, len(ordered)):
        if _order(ordered[i]) == _order(ordered[i - 1]):
            # Falling back on the backend's own order here would make net
            # names depend on the backend, which is the whole thing this
            # exists to prevent. Fail loudly instead.
            raise ValueError(
                f"two clusters on layer {layer} share the bounding box "
                f"{ordered[i].bbox}, so cluster order is ambiguous and net "
                "names would depend on the geometry backend"
            )
    return ordered


def trace(
    design: Design,
    extra: dict[str, list] | None = None,
    *,
    progress: Progress | None = None,
) -> Connectivity:
    """Trace connectivity. `extra` adds shapes per routing layer

    `progress`, if given, is called as `(stage, done, total)`: once per
    routing layer during clustering (stage "trace"), then once per via layer
    during stitching (stage "vias").
    """
    uf = UnionFind()
    conn = Connectivity(uf=uf)
    extra = extra or {}

    # 1. Per-layer merge: Drawing and pin purposes go in together so that a pin
    # drawn only on the pin layer still fuses with the wire that touches it
    routing = design.tech.routing
    for i, rl in enumerate(routing):
        clusters = geo.merge_clusters(
            chain(
                _shapes(design, rl.drawing),
                _shapes(design, rl.pin),
                extra.get(rl.name, ()),
            )
        )
        clusters = _canonical(clusters, rl.name)
        ids = [uf.add() for _ in clusters]
        conn.index[rl.name] = PointIndex(clusters, ids)
        if progress is not None:
            progress("trace", i + 1, len(routing))

    conn.n_clusters = len(uf.parent)

    # 2. Via stitching: also in canonical order, because the union sequence
    # decides which cluster id ends up the root of each net, and the root is
    # the net's number. Ties need no resolving here -- two vias with the same
    # extent stitch the same pair of clusters, in either order.
    vias = design.tech.vias
    for i, via in enumerate(vias):
        for cluster in sorted(
            geo.merge_clusters(_shapes(design, via.layer)), key=_order
        ):
            pt = cluster.bbox.center()
            lo = conn.cluster_at(via.below, pt)
            hi = conn.cluster_at(via.above, pt)
            if lo is None or hi is None:
                conn.dangling_vias.append((via.name, pt))
                continue
            uf.union(lo, hi)
        if progress is not None:
            progress("vias", i + 1, len(vias))

    return conn
