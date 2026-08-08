"""What each sky130 cell actually does

The Boolean expressions follow the sky130_fd_sc_hd definitions. The
naming convention is regular enough to read off the cell name, e.g.,
`a21bo` = 2-input AND into an OR with the B input inverted, `o21bai` = the same
shape with OR/NAND swapped, trailing `i` = inverting output (`Y` not `X`).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CellFunction:
    """A combinational cell"""

    generic: str  # technology-independent name, for generic view
    inputs: tuple[str, ...]
    output: str
    fn: Callable[..., int]

    def evaluate(self, values: dict[str, int]) -> int:
        return self.fn(*(values[p] for p in self.inputs))


@dataclass(frozen=True)
class FlopFunction:
    """A rising-edge triggered D flip-flop, optional async active-low reset"""

    generic: str
    data: str = "D"
    clock: str = "CLK"
    output: str = "Q"
    reset_n: str | None = None
    inverted_output: bool = False


def _comb(generic, inputs, output, fn):
    return CellFunction(generic, tuple(inputs), output, fn)


# note: not exhaustive of sky130_fd_sc_hd. list: https://sky130-unofficial.readthedocs.io/en/latest/contents/libraries/sky130_fd_sc_hd/README.html
COMBINATIONAL: dict[str, CellFunction] = {
    "and2": _comb("AND2", "AB", "X", lambda a, b: a & b),
    "and3": _comb("AND3", "ABC", "X", lambda a, b, c: a & b & c),
    "and4": _comb("AND4", "ABCD", "X", lambda a, b, c, d: a & b & c & d),
    "and4bb": _comb(
        "AND4BB",
        ("A_N", "B_N", "C", "D"),
        "X",
        lambda a, b, c, d: (1 - a) & (1 - b) & c & d,
    ),
    "or2": _comb("OR2", "AB", "X", lambda a, b: a | b),
    "or3": _comb("OR3", "ABC", "X", lambda a, b, c: a | b | c),
    "nand2": _comb("NAND2", "AB", "Y", lambda a, b: 1 - (a & b)),
    "nand3": _comb("NAND3", "ABC", "Y", lambda a, b, c: 1 - (a & b & c)),
    "nor2": _comb("NOR2", "AB", "Y", lambda a, b: 1 - (a | b)),
    "nor3": _comb("NOR3", "ABC", "Y", lambda a, b, c: 1 - (a | b | c)),
    "xor2": _comb("XOR2", "AB", "X", lambda a, b: a ^ b),
    "xnor2": _comb("XNOR2", "AB", "Y", lambda a, b: 1 - (a ^ b)),
    "inv": _comb("INV", "A", "Y", lambda a: 1 - a),
    "buf": _comb("BUF", "A", "X", lambda a: a),
    "clkbuf": _comb("BUF", "A", "X", lambda a: a),
    "clkinv": _comb("INV", "A", "Y", lambda a: 1 - a),
    "mux2": _comb("MUX2", ("A0", "A1", "S"), "X", lambda a0, a1, s: a1 if s else a0),
    "mux2i": _comb(
        "MUX2I", ("A0", "A1", "S"), "Y", lambda a0, a1, s: 1 - (a1 if s else a0)
    ),
    # AND-OR / OR-AND families: aXY[b][i] (see module docstring)
    "a21o": _comb("AO21", ("A1", "A2", "B1"), "X", lambda a1, a2, b1: (a1 & a2) | b1),
    "a21oi": _comb(
        "AOI21", ("A1", "A2", "B1"), "Y", lambda a1, a2, b1: 1 - ((a1 & a2) | b1)
    ),
    "a21bo": _comb(
        "AO21B", ("A1", "A2", "B1_N"), "X", lambda a1, a2, b: (a1 & a2) | (1 - b)
    ),
    "a21boi": _comb(
        "AOI21B", ("A1", "A2", "B1_N"), "Y", lambda a1, a2, b: 1 - ((a1 & a2) | (1 - b))
    ),
    "a31o": _comb(
        "AO31",
        ("A1", "A2", "A3", "B1"),
        "X",
        lambda a1, a2, a3, b1: (a1 & a2 & a3) | b1,
    ),
    "a31oi": _comb(
        "AOI31",
        ("A1", "A2", "A3", "B1"),
        "Y",
        lambda a1, a2, a3, b1: 1 - ((a1 & a2 & a3) | b1),
    ),
    "a22o": _comb(
        "AO22",
        ("A1", "A2", "B1", "B2"),
        "X",
        lambda a1, a2, b1, b2: (a1 & a2) | (b1 & b2),
    ),
    "a22oi": _comb(
        "AOI22",
        ("A1", "A2", "B1", "B2"),
        "Y",
        lambda a1, a2, b1, b2: 1 - ((a1 & a2) | (b1 & b2)),
    ),
    "o21a": _comb("OA21", ("A1", "A2", "B1"), "X", lambda a1, a2, b1: (a1 | a2) & b1),
    "o21ai": _comb(
        "OAI21", ("A1", "A2", "B1"), "Y", lambda a1, a2, b1: 1 - ((a1 | a2) & b1)
    ),
    "o21bai": _comb(
        "OAI21B", ("A1", "A2", "B1_N"), "Y", lambda a1, a2, b: 1 - ((a1 | a2) & (1 - b))
    ),
    "o31ai": _comb(
        "OAI31",
        ("A1", "A2", "A3", "B1"),
        "Y",
        lambda a1, a2, a3, b1: 1 - ((a1 | a2 | a3) & b1),
    ),
    "o22ai": _comb(
        "OAI22",
        ("A1", "A2", "B1", "B2"),
        "Y",
        lambda a1, a2, b1, b2: 1 - ((a1 | a2) & (b1 | b2)),
    ),
}

SEQUENTIAL: dict[str, FlopFunction] = {
    "dfxtp": FlopFunction("DFF"),
    "dfrtp": FlopFunction("DFFR", reset_n="RESET_B"),
    "dfrtn": FlopFunction("DFFR", clock="CLK_N", reset_n="RESET_B"),
    "dfstp": FlopFunction("DFFS", reset_n=None),
    "dfbbn": FlopFunction("DFFSR", reset_n="RESET_B"),
    "dlxtp": FlopFunction("DLATCH"),
}


def base_name(cell: str) -> str:
    """`sky130_fd_sc_hd__nand2_2` -> `nand2` (strips library prefix and drive)."""
    name = cell.split("__", 1)[-1]
    head, _, tail = name.rpartition("_")
    return head if head and tail.isdigit() else name


def lookup(cell: str) -> CellFunction | FlopFunction | None:
    base = base_name(cell)
    return COMBINATIONAL.get(base) or SEQUENTIAL.get(base)


def is_sequential(cell: str) -> bool:
    return base_name(cell) in SEQUENTIAL
