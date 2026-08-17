"""Deterministic serialisation for result dataclasses.

One place that knows how to turn a `set` into a sorted list, an `Enum` into
its value and a `Path` into a string, so every result type round-trips
through `json.dumps` with no custom encoder.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from pathlib import Path
from typing import Any


def to_dict(obj: Any) -> Any:
    """`obj`, and everything it contains, as plain `dict`/`list`/primitives"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        items = [to_dict(item) for item in obj]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=repr)
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    return obj
