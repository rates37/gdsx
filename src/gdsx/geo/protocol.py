"""The geometry backend seam.

`gdsx` reads a GDS and answers three geometric questions: what shapes are on a
layer, where is every leaf cell placed, and which shapes form one connected
piece of metal.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import Box, Point, Shape, Trans


@runtime_checkable
class Cluster(Protocol):
    """One connected piece of metal on one layer.

    Produced by `Backend.merge_clusters`. Connectivity only ever asks a cluster
    for its extent and whether a pin point lands in it, so a backend is free to
    represent the merged geometry however it likes.
    """

    @property
    def bbox(self) -> Box:
        """The cluster's bounding box"""
        ...

    def contains(self, pt: Point) -> bool:
        """Inclusive: a point on the cluster's boundary is inside it"""
        ...


@runtime_checkable
class Layout(Protocol):
    """An opened GDS. Cells are addressed by name."""

    @property
    def dbu(self) -> float:
        """Database unit in microns"""
        ...

    def top_cells(self) -> list[str]:
        """Names of every cell that nothing else instantiates"""
        ...

    def has_cell(self, cell: str) -> bool: ...

    def cell_bbox(self, cell: str) -> Box:
        """The cell's extent over all layers, empty if it holds nothing"""
        ...

    def layers(self) -> list[tuple[int, int]]:
        """Every (layer, datatype) pair present in the file"""
        ...

    def layer_index(self, ld: tuple[int, int]) -> int | None:
        """The backend's handle for a (layer, datatype) pair, or None if absent"""
        ...

    def shapes(self, cell: str, layer_index: int) -> Iterator[Shape]:
        """Shapes drawn directly in `cell`, in that cell's own coordinates"""
        ...

    def shapes_rec(self, cell: str, layer_index: int) -> Iterator[Shape]:
        """Shapes in `cell` and every descendant, flattened into `cell`'s coordinates"""
        ...

    def leaf_instances(self, cell: str) -> Iterator[tuple[str, Trans]]:
        """(cell name, transform) for every leaf placement below `cell`

        Arrays are expanded and hierarchy is traversed recursively, so each
        yielded transform is already in `cell`'s coordinates.
        """
        ...


@runtime_checkable
class Backend(Protocol):
    name: str  # "klayout" | "pure"

    def read_gds(self, data: bytes) -> Layout:
        """Open a layout from raw bytes, so a browser can pass an ArrayBuffer"""
        ...

    def read_file(self, path: Path) -> Layout:
        """Open a layout from a file. Equivalent to `read_gds(path.read_bytes())`

        Backends that can read a file directly should, to avoid copying it.
        """
        ...

    def merge_clusters(self, shapes: Iterable[Shape]) -> list[Cluster]:
        """Group shapes into connected components under "touches or overlaps"

        Touching counts: two shapes that share exactly one edge are one cluster.

        The returned order is unspecified, and backends do differ: klayout
        emits its merge scanline's order, which nothing else can reproduce.
        `connectivity.trace` sorts clusters into a canonical order before it
        numbers them, so net names do not depend on which backend ran.
        """
        ...
