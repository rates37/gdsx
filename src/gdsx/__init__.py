"""gdsx: gate-level netlist extraction from standard-cell GDS."""

import importlib.util

__all__ = ["main", "capabilities"]


def __getattr__(name: str):
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def capabilities() -> dict[str, bool]:
    """What this environment can do. The browser greys out what it cannot"""
    from .external import graphviz, yosys

    return {
        "yosys": yosys.available(),
        "klayout": importlib.util.find_spec("klayout") is not None,
        "graphviz": graphviz.available(),
    }
