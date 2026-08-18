"""Pure-Python geometry backend. Not implemented yet."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .types import Shape

_TODO = (
    "the pure backend is not implemented yet"
)


class PureBackend:
    name = "pure"

    def read_gds(self, data: bytes):
        raise NotImplementedError(_TODO)

    def read_file(self, path: Path):
        raise NotImplementedError(_TODO)

    def merge_clusters(self, shapes: Iterable[Shape]):
        raise NotImplementedError(_TODO)
