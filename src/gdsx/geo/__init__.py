"""Geometry backend selection

Pick with the `GDSX_GEO_BACKEND` environment variable or `geo.use(name)`.
With neither, klayout is used if it is importable and the pure backend
otherwise.

Backend modules are imported lazily, so importing `gdsx.geo` never pulls in
klayout by itself.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterable
from pathlib import Path

from .protocol import Backend, Cluster, Layout
from .types import Box, Point, Polygon, Shape, Text, Trans, Vector
from .types import Path as GeoPath

__all__ = [
    "Backend",
    "Box",
    "Cluster",
    "GeoPath",
    "Layout",
    "Point",
    "Polygon",
    "Shape",
    "Text",
    "Trans",
    "Vector",
    "backend",
    "backend_name",
    "merge_clusters",
    "read_file",
    "read_gds",
    "use",
]

BACKENDS = ("klayout", "pure")
ENV_VAR = "GDSX_GEO_BACKEND"

_backend: Backend | None = None
_chosen: str | None = None


def _default_name() -> str:
    name = os.environ.get(ENV_VAR)
    if name:
        if name not in BACKENDS:
            raise ValueError(f"{ENV_VAR}={name!r} is not one of {', '.join(BACKENDS)}")
        return name
    return "klayout" if importlib.util.find_spec("klayout") is not None else "pure"


def _build(name: str) -> Backend:
    if name == "klayout":
        from .klayout_backend import KLayoutBackend

        return KLayoutBackend()
    if name == "pure":
        from .pure_backend import PureBackend

        return PureBackend()
    raise ValueError(f"unknown geometry backend {name!r}")


def use(name: str) -> Backend:
    """Select a backend by name, for this process"""
    global _backend, _chosen
    if name not in BACKENDS:
        raise ValueError(f"unknown geometry backend {name!r}")
    _backend = _build(name)
    _chosen = name
    return _backend


def backend() -> Backend:
    """The active backend, chosen on first use"""
    global _backend, _chosen
    if _backend is None:
        _chosen = _default_name()
        _backend = _build(_chosen)
    return _backend


def backend_name() -> str:
    return backend().name


def read_gds(data: bytes) -> Layout:
    return backend().read_gds(data)


def read_file(path: Path | str) -> Layout:
    return backend().read_file(Path(path))


def merge_clusters(shapes: Iterable[Shape]) -> list[Cluster]:
    return backend().merge_clusters(shapes)
