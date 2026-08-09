"""What each library cell does

liberty.py does the parsing. this module is the layer the rest of the library talks to.
It maps a placed cell name (e.g., `sky130_fd_sc_hd__nand2_2`) to its behaviour, names
the technology-independent equivalent
"""

from __future__ import annotations

from .liberty import Cell, library, variables

# Readable names for the common families. Anything not listed keeps its
# uppercased base name, which is still library-independent and drive-independent.
GENERIC_ALIASES = {
    "buf": "BUF",
    "clkbuf": "BUF",
    "inv": "INV",
    "clkinv": "INV",
    "a21o": "AO21",
    "a21oi": "AOI21",
    "a21bo": "AO21B",
    "a21boi": "AOI21B",
    "a22o": "AO22",
    "a22oi": "AOI22",
    "a31o": "AO31",
    "a31oi": "AOI31",
    "a32o": "AO32",
    "a32oi": "AOI32",
    "o21a": "OA21",
    "o21ai": "OAI21",
    "o21ba": "OA21B",
    "o21bai": "OAI21B",
    "o22a": "OA22",
    "o22ai": "OAI22",
    "o31a": "OA31",
    "o31ai": "OAI31",
    "fa": "FULLADDER",
    "ha": "HALFADDER",
    "dfxtp": "DFF",
    "dfrtp": "DFFR",
    "dfstp": "DFFS",
    "dfbbn": "DFFSR",
    "dlxtp": "DLATCH",
    "conb": "TIE",
}


class UnknownCell(KeyError):
    pass


def base_name(cell: str) -> str:
    """`sky130_fd_sc_hd__nand2_2` -> `nand2` (prefix and drive stripped)"""
    name = cell.split("__", 1)[-1]
    head, _, tail = name.rpartition("_")
    return head if head and tail.isdigit() else name


def lookup(cell: str) -> Cell | None:
    """The cell's behaviour or None if the library doesn't describe it"""
    found = library().get(base_name(cell))
    return found if found and found.has_behaviour else None


def is_sequential(cell: str) -> bool:
    found = library().get(base_name(cell))
    return bool(found and found.is_sequential)


def generic_name(cell: str) -> str:
    base = base_name(cell)
    return GENERIC_ALIASES.get(base, base.upper())


# structural queries about a placed sequential cell


def _nets_of(cell: Cell, expr, connections: dict[str, str]) -> set[str]:
    if expr is None:
        return set()
    return {connections[pin] for pin in variables(expr) if pin in connections}


def state_output_pin(cell: Cell) -> str | None:
    if not cell.is_sequential:
        return None
    positive = ("var", cell.sequential.state_var)
    return next((pin for pin, expr in cell.functions.items() if expr == positive), None)


def output_net(cell: Cell, connections: dict[str, str]) -> str | None:
    pin = state_output_pin(cell)
    return connections.get(pin) if pin else None


def data_nets(cell: Cell, connections: dict[str, str]) -> set[str]:
    return _nets_of(cell, cell.sequential.next_state, connections)


def clock_nets(cell: Cell, connections: dict[str, str]) -> set[str]:
    return _nets_of(cell, cell.sequential.clocked_on, connections)


def async_nets(cell: Cell, connections: dict[str, str]) -> set[str]:
    seq = cell.sequential
    return _nets_of(cell, seq.clear, connections) | _nets_of(
        cell, seq.preset, connections
    )
