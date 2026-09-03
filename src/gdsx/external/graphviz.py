"""Detect the graphviz `dot` binary, for `gdsx.capabilities()`"""

from __future__ import annotations

import shutil


def available() -> bool:
    return shutil.which("dot") is not None
