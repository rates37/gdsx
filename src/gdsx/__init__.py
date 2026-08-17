"""gdsx: gate-level netlist extraction from standard-cell GDS."""

import importlib.util

from .cli import main

__all__ = ["main", "capabilities"]


def capabilities() -> dict[str, bool]:
    """What this environment can do. The browser greys out what it cannot"""
    from .external import graphviz, yosys

    return {
        "yosys": yosys.available(),
        "klayout": importlib.util.find_spec("klayout") is not None,
        "graphviz": graphviz.available(),
    }
