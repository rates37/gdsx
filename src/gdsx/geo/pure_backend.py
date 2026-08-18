"""Pure-Python geometry backend.

The reader is `geo.gdsii` and the cluster engine is `geo.clusters`; this is
only the seam that presents them as a `geo.protocol.Backend`. Nothing here
imports klayout, so it is the backend Pyodide gets.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import clusters, gdsii
from .types import Shape


class PureBackend:
    name = "pure"

    def read_gds(self, data: bytes) -> gdsii.GdsLayout:
        return gdsii.read_gds(data)

    def read_file(self, path: Path) -> gdsii.GdsLayout:
        return gdsii.read_file(path)

    def merge_clusters(self, shapes: Iterable[Shape]) -> list[clusters.RectCluster]:
        return clusters.merge(shapes)
