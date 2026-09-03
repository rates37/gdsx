"""The gate tape: one compiler, two executors

`Simulator` re-walks a parsed Liberty `Expr` tree per gate per cycle. This
module moves that interpretation to compile time and leaves behind a flat
`int32` opcode stream that a tight indexed loop can run -- in Python (see
`execute.py`, the ground truth) and, later, in TypeScript over the same bytes.

Three rules keep the two executors from ever disagreeing, and they are not
negotiable:

* **Values are 0 or 1 only.** Never rely on Python or JavaScript truthiness;
  compare explicitly. `-1` in an operand slot means "unused", and only ever
  appears in slots the opcode does not read.
* **A Liberty function maps to opcodes exactly or not at all.** If neither the
  single-opcode matcher nor a checked decomposition reproduces the function's
  truth table, the compiler raises `UnsupportedFunction`. It never approximates.
* **Opcode numbers are never reused or renumbered.** A tape is a versioned
  binary artifact. New opcodes go on the end of the table and `TAPE_VERSION` is
  bumped, which invalidates cached tapes.

The op stream is emitted in an order that makes one forward pass correct:

1. the flop-output prologue, writing each flop's `Q` (and any `Q_N`) from state;
2. the combinational gates, in `Simulator.combinational` order, which is
   `Graph.topo()` order and is frozen by `tests/golden/simorder-*.txt`;
3. the flop-support ops, computing each flop's `d`/`clk`/`rst`/`set` net where
   Liberty describes it as an expression rather than a bare pin.

The compiler reads its instance partition and its ordering off a `Simulator`
rather than re-deriving them. That is deliberate: the two engines cannot drift
in gate order if only one of them decides it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from functools import lru_cache
from itertools import permutations

from ..core.netlist import Instance, Netlist
from ..functions import base_name
from ..liberty import Cell, Expr, evaluate, to_verilog, variables
from .simulator import Simulator

#: Bumped whenever the opcode table changes. Cached tapes key on it.
TAPE_VERSION = 1

#: Every op is exactly this many int32: [opcode, out, in0, in1, in2, in3].
#: A fixed stride is what lets both executors be an indexed loop with no
#: allocation and no branching on operand count.
STRIDE = 6

#: An operand slot the opcode does not read.
UNUSED = -1


class Op(IntEnum):
    """The opcode table, fixed and shared by both executors"""

    CONST0 = 0
    CONST1 = 1
    BUF = 2
    NOT = 3
    AND2 = 4
    OR2 = 5
    XOR2 = 6
    NAND2 = 7
    NOR2 = 8
    XNOR2 = 9
    AND3 = 10
    OR3 = 11
    AND4 = 12
    OR4 = 13
    MUX2 = 14
    AOI21 = 15
    OAI21 = 16
    AO21 = 17
    OA21 = 18
    AOI22 = 19
    OAI22 = 20
    AO22 = 21
    OA22 = 22


#: How many operand slots each opcode reads. The rest are UNUSED.
ARITY: dict[Op, int] = {
    Op.CONST0: 0,
    Op.CONST1: 0,
    Op.BUF: 1,
    Op.NOT: 1,
    Op.AND2: 2,
    Op.OR2: 2,
    Op.XOR2: 2,
    Op.NAND2: 2,
    Op.NOR2: 2,
    Op.XNOR2: 2,
    Op.AND3: 3,
    Op.OR3: 3,
    Op.AND4: 4,
    Op.OR4: 4,
    Op.MUX2: 3,
    Op.AOI21: 3,
    Op.OAI21: 3,
    Op.AO21: 3,
    Op.OA21: 3,
    Op.AOI22: 4,
    Op.OAI22: 4,
    Op.AO22: 4,
    Op.OA22: 4,
}

#: What each opcode computes, as a function of the value array and its four
#: operand slots. The single definition of the table's semantics: the matcher
#: searches with it, the recipe verifier checks against it, and the executor
#: dispatches through it. Writing the semantics twice is how the two arms drift.
SEMANTICS = {
    Op.CONST0: lambda v, a, b, c, d: 0,
    Op.CONST1: lambda v, a, b, c, d: 1,
    Op.BUF: lambda v, a, b, c, d: v[a],
    Op.NOT: lambda v, a, b, c, d: 1 - v[a],
    Op.AND2: lambda v, a, b, c, d: v[a] & v[b],
    Op.OR2: lambda v, a, b, c, d: v[a] | v[b],
    Op.XOR2: lambda v, a, b, c, d: v[a] ^ v[b],
    Op.NAND2: lambda v, a, b, c, d: 1 - (v[a] & v[b]),
    Op.NOR2: lambda v, a, b, c, d: 1 - (v[a] | v[b]),
    Op.XNOR2: lambda v, a, b, c, d: 1 - (v[a] ^ v[b]),
    Op.AND3: lambda v, a, b, c, d: v[a] & v[b] & v[c],
    Op.OR3: lambda v, a, b, c, d: v[a] | v[b] | v[c],
    Op.AND4: lambda v, a, b, c, d: v[a] & v[b] & v[c] & v[d],
    Op.OR4: lambda v, a, b, c, d: v[a] | v[b] | v[c] | v[d],
    # `s ? b : a`, with s in slot 2. An explicit comparison, not truthiness.
    Op.MUX2: lambda v, a, b, c, d: v[b] if v[c] == 1 else v[a],
    Op.AOI21: lambda v, a, b, c, d: 1 - ((v[a] & v[b]) | v[c]),
    Op.OAI21: lambda v, a, b, c, d: 1 - ((v[a] | v[b]) & v[c]),
    Op.AO21: lambda v, a, b, c, d: (v[a] & v[b]) | v[c],
    Op.OA21: lambda v, a, b, c, d: (v[a] | v[b]) & v[c],
    Op.AOI22: lambda v, a, b, c, d: 1 - ((v[a] & v[b]) | (v[c] & v[d])),
    Op.OAI22: lambda v, a, b, c, d: 1 - ((v[a] | v[b]) & (v[c] | v[d])),
    Op.AO22: lambda v, a, b, c, d: (v[a] & v[b]) | (v[c] & v[d]),
    Op.OA22: lambda v, a, b, c, d: (v[a] | v[b]) & (v[c] | v[d]),
}

#: Opcodes indexed by number, for the executor's dispatch.
DISPATCH = tuple(SEMANTICS[Op(i)] for i in range(len(Op)))


class FlopKind(IntEnum):
    """Which async pins a sequential cell has

    Informational only. `Simulator.step` clocks every sequential element
    unconditionally and applies clear/preset as an override, so neither
    executor branches on this -- it is recorded for the UI and for a future
    edge-sensitive executor.
    """

    DFF = 0
    DFFR = 1  # async clear
    DFFS = 2  # async preset
    DFFSR = 3  # both
    DLATCH = 4


class UnsupportedFunction(Exception):
    """A Liberty function that no opcode and no decomposition reproduces

    Raised rather than approximated. The fix is to add an opcode at the END of
    the table (never renumbering an existing one) or a verified recipe, not to
    find something close enough.
    """

    def __init__(self, cell: str, pin: str, expr: Expr) -> None:
        self.cell = cell
        self.pin = pin
        self.expr = expr
        super().__init__(
            f"no opcode reproduces {cell}.{pin} = {to_verilog(expr)}; "
            f"add an opcode at the end of the table or a verified recipe"
        )


#! the tape


@dataclass(frozen=True)
class Flop:
    """One sequential element, as net ids into the value array

    `rst` and `set` are **active high** and `clk` is **active high** whatever
    the cell's own polarity: `dfrtp`'s Liberty clear is `~RESET_B`, and the
    compiler emits the inversion as a `NOT` op rather than recording a polarity
    flag. Keeping polarity in the op stream is what lets both executors stay
    branch-free here. `UNUSED` means the cell has no such pin.

    `clk` is recorded but is not read by `step()`, which clocks every element
    unconditionally exactly as `Simulator.step` does. It is there for a future
    edge-sensitive executor.
    """

    d: int
    q: int
    clk: int
    rst: int
    set: int
    kind: int


@dataclass(frozen=True)
class GateTape:
    """A compiled design: a flat op stream plus the state elements

    `ops` is `STRIDE`-wide records, topologically ordered, so one forward pass
    settles the design. `names` and `flop_names` are for the UI and the tests;
    neither is needed to execute.
    """

    tape_version: int
    n_nets: int
    n_flops: int
    inputs: tuple[int, ...]  # primary input net ids, in net-name order
    ops: tuple[int, ...]  # flat, stride 6: [opcode, out, in0, in1, in2, in3]
    flops: tuple[Flop, ...]
    consts: tuple[tuple[int, int], ...]  # (net id, 0|1) seeded before the ops
    names: dict[str, int] = field(default_factory=dict)  # net name -> net id
    flop_names: tuple[str, ...] = ()  # instance name per entry in `flops`

    @property
    def n_ops(self) -> int:
        return len(self.ops) // STRIDE

    def to_bytes(self) -> bytes:
        """The op stream as little-endian int32, for the TypeScript executor"""
        return struct.pack(f"<{len(self.ops)}i", *self.ops)

    def to_dict(self) -> dict:
        return {
            "tape_version": self.tape_version,
            "n_nets": self.n_nets,
            "n_flops": self.n_flops,
            "n_ops": self.n_ops,
            "inputs": list(self.inputs),
            "ops": list(self.ops),
            "flops": [
                {
                    "d": f.d,
                    "q": f.q,
                    "clk": f.clk,
                    "rst": f.rst,
                    "set": f.set,
                    "kind": f.kind,
                }
                for f in self.flops
            ],
            "consts": [list(pair) for pair in self.consts],
            "names": dict(self.names),
            "flop_names": list(self.flop_names),
        }


#! truth tables, and matching one against the opcode table


def truth_table(expr: Expr, order: tuple[str, ...]) -> tuple[int, ...]:
    """`expr` evaluated over every assignment of `order`, bit i of the row
    index being `order[i]`"""
    return tuple(
        evaluate(expr, {name: (row >> i) & 1 for i, name in enumerate(order)})
        for row in range(1 << len(order))
    )


def op_table(op: Op, arity: int) -> tuple[int, ...]:
    """The opcode's own truth table over `arity` inputs, same row convention"""
    run = SEMANTICS[op]
    rows = []
    for row in range(1 << arity):
        values = [(row >> i) & 1 for i in range(4)]
        rows.append(run(values, 0, 1, 2, 3))
    return tuple(rows)


@dataclass(frozen=True)
class Match:
    """A function reproduced by one opcode, modulo input order and inversion

    `sources[j]` is the index into the canonical variable order feeding operand
    slot j, and bit j of `inverted` says that operand is fed through a `NOT`.
    """

    op: Op
    sources: tuple[int, ...]
    inverted: int


@lru_cache(maxsize=None)
def match(expr: Expr, order: tuple[str, ...]) -> Match | None:
    """The single opcode reproducing `expr`, or None

    Searched by truth table, never structurally: Liberty stores these cells in
    flattened sum-of-products form, so `a21oi` reads `(~A1 & ~B1) | (~A2 & ~B1)`
    and matching the parsed tree against `!((A1 & A2) | B1)` finds nothing.

    The search order is fixed -- ascending inversion mask, then opcode number,
    then `permutations` order -- so the encoding chosen for a given function is
    deterministic and a recorded tape stays reproducible. Mask first, so an
    encoding needing no inverters is always preferred to one that does.
    """
    width = len(order)
    if width > 4:  # no opcode is wider; those cells need a recipe
        return None
    wanted = truth_table(expr, order)
    for inverted in range(1 << width):
        for op in Op:
            if ARITY[op] != width:
                continue
            native = op_table(op, width)
            for sources in permutations(range(width)):
                if all(
                    native[
                        sum(
                            (((row >> sources[j]) & 1) ^ ((inverted >> j) & 1)) << j
                            for j in range(width)
                        )
                    ]
                    == wanted[row]
                    for row in range(1 << width)
                ):
                    return Match(op, sources, inverted)
    return None


class UnconnectedPin(Exception):
    """A cell function reads a pin the layout did not connect

    `Simulator` cannot evaluate such a cell either -- `liberty.evaluate` raises
    `KeyError` on the missing pin -- so this is an extraction problem, not a
    gap in the opcode table.
    """


#! decomposition recipes
#
# The 35 cells (39 outputs) that no single opcode reproduces. Each step is
# (opcode, destination, operands), where a name is either an input pin of the
# cell, a temporary introduced by an earlier step, or -- on the LAST step --
# the output pin itself. Every recipe is checked against the cell's Liberty
# truth table before it is ever emitted (see `_recipe_plan`), so a wrong recipe
# cannot reach a tape.

Step = tuple[Op, str, tuple[str, ...]]

RECIPES: dict[str, dict[str, tuple[Step, ...]]] = {
    # AND-OR family: an AND term, then the OR
    "a211o": {"X": ((Op.AO21, "t", ("A1", "A2", "B1")), (Op.OR2, "X", ("t", "C1")))},
    "a211oi": {"Y": ((Op.AO21, "t", ("A1", "A2", "B1")), (Op.NOR2, "Y", ("t", "C1")))},
    "a2111o": {
        "X": ((Op.AO21, "t", ("A1", "A2", "B1")), (Op.OR3, "X", ("t", "C1", "D1")))
    },
    "a2111oi": {
        "Y": (
            (Op.AO21, "t", ("A1", "A2", "B1")),
            (Op.OR2, "u", ("C1", "D1")),
            (Op.NOR2, "Y", ("t", "u")),
        )
    },
    "a221o": {
        "X": ((Op.AO22, "t", ("A1", "A2", "B1", "B2")), (Op.OR2, "X", ("t", "C1")))
    },
    "a221oi": {
        "Y": ((Op.AO22, "t", ("A1", "A2", "B1", "B2")), (Op.NOR2, "Y", ("t", "C1")))
    },
    "a31o": {"X": ((Op.AND3, "t", ("A1", "A2", "A3")), (Op.OR2, "X", ("t", "B1")))},
    "a31oi": {"Y": ((Op.AND3, "t", ("A1", "A2", "A3")), (Op.NOR2, "Y", ("t", "B1")))},
    "a311o": {
        "X": ((Op.AND3, "t", ("A1", "A2", "A3")), (Op.OR3, "X", ("t", "B1", "C1")))
    },
    "a311oi": {
        "Y": (
            (Op.AND3, "t", ("A1", "A2", "A3")),
            (Op.OR2, "u", ("B1", "C1")),
            (Op.NOR2, "Y", ("t", "u")),
        )
    },
    "a32o": {
        "X": ((Op.AND3, "t", ("A1", "A2", "A3")), (Op.AO21, "X", ("B1", "B2", "t")))
    },
    "a32oi": {
        "Y": ((Op.AND3, "t", ("A1", "A2", "A3")), (Op.AOI21, "Y", ("B1", "B2", "t")))
    },
    "a222oi": {
        "Y": (
            (Op.AO22, "t", ("A1", "A2", "B1", "B2")),
            (Op.AOI21, "Y", ("C1", "C2", "t")),
        )
    },
    "a41o": {
        "X": ((Op.AND4, "t", ("A1", "A2", "A3", "A4")), (Op.OR2, "X", ("t", "B1")))
    },
    "a41oi": {
        "Y": ((Op.AND4, "t", ("A1", "A2", "A3", "A4")), (Op.NOR2, "Y", ("t", "B1")))
    },
    # OR-AND family: an OR term, then the AND
    "o211a": {"X": ((Op.OR2, "t", ("A1", "A2")), (Op.AND3, "X", ("t", "B1", "C1")))},
    "o211ai": {"Y": ((Op.AND2, "t", ("B1", "C1")), (Op.OAI21, "Y", ("A1", "A2", "t")))},
    "o2111a": {
        "X": ((Op.OR2, "t", ("A1", "A2")), (Op.AND4, "X", ("t", "B1", "C1", "D1")))
    },
    "o2111ai": {
        "Y": (
            (Op.AND3, "t", ("B1", "C1", "D1")),
            (Op.OAI21, "Y", ("A1", "A2", "t")),
        )
    },
    "o221a": {
        "X": ((Op.OA22, "t", ("A1", "A2", "B1", "B2")), (Op.AND2, "X", ("t", "C1")))
    },
    "o221ai": {
        "Y": ((Op.OA22, "t", ("A1", "A2", "B1", "B2")), (Op.NAND2, "Y", ("t", "C1")))
    },
    "o31a": {"X": ((Op.OR3, "t", ("A1", "A2", "A3")), (Op.AND2, "X", ("t", "B1")))},
    "o31ai": {"Y": ((Op.OR3, "t", ("A1", "A2", "A3")), (Op.NAND2, "Y", ("t", "B1")))},
    "o311a": {
        "X": ((Op.OR3, "t", ("A1", "A2", "A3")), (Op.AND3, "X", ("t", "B1", "C1")))
    },
    "o311ai": {
        "Y": (
            (Op.OR3, "t", ("A1", "A2", "A3")),
            (Op.AND2, "u", ("B1", "C1")),
            (Op.NAND2, "Y", ("t", "u")),
        )
    },
    "o32a": {
        "X": ((Op.OR3, "t", ("A1", "A2", "A3")), (Op.OA21, "X", ("B1", "B2", "t")))
    },
    "o32ai": {
        "Y": ((Op.OR3, "t", ("A1", "A2", "A3")), (Op.OAI21, "Y", ("B1", "B2", "t")))
    },
    "o41a": {
        "X": ((Op.OR4, "t", ("A1", "A2", "A3", "A4")), (Op.AND2, "X", ("t", "B1")))
    },
    "o41ai": {
        "Y": ((Op.OR4, "t", ("A1", "A2", "A3", "A4")), (Op.NAND2, "Y", ("t", "B1")))
    },
    # a wider mux is a tree of the two-input one
    "mux4": {
        "X": (
            (Op.MUX2, "t", ("A0", "A1", "S0")),
            (Op.MUX2, "u", ("A2", "A3", "S0")),
            (Op.MUX2, "X", ("t", "u", "S1")),
        )
    },
    # adders and the three-input cells: a majority and an XOR chain
    "maj3": {
        "X": ((Op.AO22, "t", ("A", "B", "A", "C")), (Op.AO21, "X", ("B", "C", "t")))
    },
    "xor3": {"X": ((Op.XOR2, "t", ("A", "B")), (Op.XOR2, "X", ("t", "C")))},
    "xnor3": {"X": ((Op.XOR2, "t", ("A", "B")), (Op.XNOR2, "X", ("t", "C")))},
    "fa": {
        "COUT": (
            (Op.AO22, "t", ("A", "B", "A", "CIN")),
            (Op.AO21, "COUT", ("B", "CIN", "t")),
        ),
        "SUM": ((Op.XOR2, "t", ("A", "B")), (Op.XOR2, "SUM", ("t", "CIN"))),
    },
    "fah": {
        "COUT": (
            (Op.AO22, "t", ("A", "B", "A", "CI")),
            (Op.AO21, "COUT", ("B", "CI", "t")),
        ),
        "SUM": ((Op.XOR2, "t", ("A", "B")), (Op.XOR2, "SUM", ("t", "CI"))),
    },
    "fahcin": {
        "COUT": (
            (Op.NOT, "n", ("CIN",)),
            (Op.AO22, "t", ("A", "B", "A", "n")),
            (Op.AO21, "COUT", ("B", "n", "t")),
        ),
        "SUM": ((Op.XOR2, "t", ("A", "B")), (Op.XNOR2, "SUM", ("t", "CIN"))),
    },
    "fahcon": {
        "COUT_N": (
            (Op.AO22, "t", ("A", "B", "A", "CI")),
            (Op.AOI21, "COUT_N", ("B", "CI", "t")),
        ),
        "SUM": ((Op.XOR2, "t", ("A", "B")), (Op.XOR2, "SUM", ("t", "CI"))),
    },
}


def run_recipe(steps: tuple[Step, ...], values: dict[str, int]) -> int:
    """The recipe's result for one assignment of the cell's input pins

    Used to verify a recipe against Liberty, and by nothing else -- the tape
    itself runs the emitted ops, not this.
    """
    env = dict(values)
    slots = [0, 0, 0, 0]
    result = 0
    for op, out, operands in steps:
        for i in range(4):
            slots[i] = env[operands[i]] if i < len(operands) else 0
        result = env[out] = SEMANTICS[op](slots, 0, 1, 2, 3)
    return result


@lru_cache(maxsize=None)
def _recipe_plan(cell: str, pin: str, expr: Expr, order: tuple[str, ...]):
    """The verified recipe for `cell.pin`, or None if there is not one

    The verification is exhaustive rather than trusted: cells have at most six
    inputs, so all 64 rows are checked against the Liberty function the first
    time a recipe is used. A recipe that is wrong raises here instead of quietly
    producing a tape that simulates something else.
    """
    steps = RECIPES.get(cell, {}).get(pin)
    if steps is None:
        return None
    if steps[-1][1] != pin:
        raise ValueError(f"recipe for {cell}.{pin} does not end by writing {pin}")
    wanted = truth_table(expr, order)
    for row in range(1 << len(order)):
        bound = {name: (row >> i) & 1 for i, name in enumerate(order)}
        if run_recipe(steps, bound) != wanted[row]:
            raise ValueError(
                f"recipe for {cell}.{pin} disagrees with Liberty at "
                f"{bound}: recipe says {run_recipe(steps, bound)}, "
                f"Liberty says {wanted[row]}"
            )
    return steps


#! the compiler


class _Compiler:
    """Builds one `GateTape`. One use per instance."""

    def __init__(self, netlist: Netlist) -> None:
        self.netlist = netlist
        # The partition and the gate order both come from here, so the two
        # engines cannot disagree about them.
        self.simulator = Simulator(netlist)
        # Net ids are assigned in net-name order, not extraction order, so the
        # numbering is canonical and survives a change to shape iteration.
        self.ids: dict[str, int] = {
            name: i for i, name in enumerate(sorted(netlist.nets))
        }
        self.next_id = len(self.ids)
        self.ops: list[int] = []
        self.inverters: dict[int, int] = {}  # net id -> net id holding its NOT

    #! net allocation

    def net(self, name: str) -> int:
        got = self.ids.get(name)
        if got is None:
            got = self.ids[name] = self.next_id
            self.next_id += 1
        return got

    def temp(self) -> int:
        got = self.next_id
        self.next_id += 1
        return got

    #! emission

    def emit(self, op: Op, out: int, operands: tuple[int, ...]) -> int:
        slots = list(operands) + [UNUSED] * (4 - len(operands))
        self.ops.extend([int(op), out, *slots])
        return out

    def inverted(self, net: int) -> int:
        """A net holding `!net`, emitted once and shared

        Safe to share because ops are emitted in topological order, so every
        later reader of the inverter comes after it in the stream.
        """
        got = self.inverters.get(net)
        if got is None:
            got = self.inverters[net] = self.emit(Op.NOT, self.temp(), (net,))
        return got

    #! functions

    def _resolve(self, inst: Instance, pin: str, cell: Cell) -> int:
        net = inst.connections.get(pin)
        if net is None:
            raise UnconnectedPin(
                f"{inst.name} ({inst.cell}) has no net on {pin}, "
                f"which its Liberty behaviour reads"
            )
        return self.net(net)

    def function(self, inst: Instance, cell: Cell, pin: str, expr: Expr) -> int:
        """Emit the ops for one output pin, writing the net that pin drives"""
        target = self._resolve(inst, pin, cell)
        self.expression(inst, cell, expr, target=target, pin=pin)
        return target

    def expression(
        self,
        inst: Instance,
        cell: Cell,
        expr: Expr,
        *,
        target: int | None = None,
        pin: str | None = None,
    ) -> int:
        """Emit the ops computing `expr` over `inst`'s pins; return its net

        With `target`, the last op writes that net; otherwise a temporary. `pin`
        names the output being compiled, for the recipe lookup and for the error
        message when nothing matches.
        """
        base = base_name(inst.cell)

        if expr[0] == "var":
            net = self._resolve(inst, expr[1], cell)
            return net if target is None else self.emit(Op.BUF, target, (net,))
        if expr[0] == "const":
            op = Op.CONST1 if expr[1] == 1 else Op.CONST0
            return self.emit(op, self.temp() if target is None else target, ())

        order = tuple(sorted(variables(expr)))
        nets = [self._resolve(inst, name, cell) for name in order]

        steps = _recipe_plan(base, pin, expr, order) if pin is not None else None
        if steps is not None:
            return self._emit_recipe(steps, order, nets, pin, target)

        found = match(expr, order)
        if found is None:
            raise UnsupportedFunction(base, pin or "?", expr)
        operands = tuple(
            self.inverted(nets[src]) if (found.inverted >> j) & 1 else nets[src]
            for j, src in enumerate(found.sources)
        )
        out = self.temp() if target is None else target
        return self.emit(found.op, out, operands)

    def _emit_recipe(self, steps, order, nets, pin, target) -> int:
        env = dict(zip(order, nets))
        env[pin] = self.temp() if target is None else target
        for op, out, operands in steps:
            if out not in env:
                env[out] = self.temp()
            self.emit(op, env[out], tuple(env[name] for name in operands))
        return env[pin]

    #! the three sections of the stream

    def prologue(self) -> list[Flop]:
        """Write each flop's outputs from its state, before any gate runs

        A flop's state lives in one net (`Flop.q`). Any second output -- `Q_N`
        on a `dfbbn` -- is a `NOT` of it, classified by evaluating the pin's
        Liberty function against both settings of the state variable rather than
        by matching the expression's shape.
        """
        flops: list[Flop] = []
        for inst, cell in self.simulator.flops:
            seq = cell.sequential
            polarity: dict[str, tuple[int, int]] = {}
            for pin, expr in cell.functions.items():
                polarity[pin] = tuple(
                    evaluate(expr, self._state_binding(seq.state_vars, held))
                    for held in (0, 1)
                )

            positive = [
                pin
                for pin, table in polarity.items()
                if table == (0, 1) and pin in inst.connections
            ]
            q = (
                self.net(inst.connections[positive[0]])
                if positive
                else self.temp()  # nothing reads Q directly; state still needs a net
            )

            for pin, table in polarity.items():
                net = inst.connections.get(pin)
                if net is None or (positive and pin == positive[0]):
                    continue
                op = {
                    (0, 1): Op.BUF,
                    (1, 0): Op.NOT,
                    (0, 0): Op.CONST0,
                    (1, 1): Op.CONST1,
                }[table]
                operands = () if op in (Op.CONST0, Op.CONST1) else (q,)
                self.emit(op, self.net(net), operands)

            flops.append(Flop(UNUSED, q, UNUSED, UNUSED, UNUSED, self._kind(cell)))
        return flops

    @staticmethod
    def _state_binding(state_vars: tuple[str, ...], held: int) -> dict[str, int]:
        values = {state_vars[0]: held}
        if len(state_vars) > 1:
            values[state_vars[1]] = 1 - held
        return values

    @staticmethod
    def _kind(cell: Cell) -> int:
        seq = cell.sequential
        if seq.is_latch:
            return int(FlopKind.DLATCH)
        if seq.clear is not None and seq.preset is not None:
            return int(FlopKind.DFFSR)
        if seq.clear is not None:
            return int(FlopKind.DFFR)
        if seq.preset is not None:
            return int(FlopKind.DFFS)
        return int(FlopKind.DFF)

    def combinational(self) -> None:
        for inst, cell in self.simulator.combinational:
            for pin, expr in cell.functions.items():
                if pin in inst.connections:
                    self.function(inst, cell, pin, expr)

    def support(self, flops: list[Flop]) -> list[Flop]:
        """The ops computing each flop's d/clk/rst/set, as active-high nets

        These read nets the combinational section has already computed and write
        only temporaries, so appending them to the same stream is safe.
        """
        out: list[Flop] = []
        for flop, (inst, cell) in zip(flops, self.simulator.flops):
            seq = cell.sequential
            d = self.expression(inst, cell, seq.next_state)
            clk = self.expression(inst, cell, seq.clocked_on)
            rst = (
                UNUSED if seq.clear is None else self.expression(inst, cell, seq.clear)
            )
            preset = (
                UNUSED
                if seq.preset is None
                else self.expression(inst, cell, seq.preset)
            )
            out.append(Flop(d, flop.q, clk, rst, preset, flop.kind))
        return out

    def run(self) -> GateTape:
        flops = self.prologue()
        self.combinational()
        flops = self.support(flops)

        nl = self.netlist
        consts = tuple(
            (self.net(net), 0 if net.endswith("GND") else 1)
            for net in sorted(nl.power_nets)
        )
        inputs = tuple(
            self.net(net) for net in sorted(nl.ports) if nl.ports[net] == "input"
        )
        return GateTape(
            tape_version=TAPE_VERSION,
            n_nets=self.next_id,
            n_flops=len(flops),
            inputs=inputs,
            ops=tuple(self.ops),
            flops=tuple(flops),
            consts=consts,
            names={name: self.ids[name] for name in sorted(nl.nets)},
            flop_names=tuple(inst.name for inst, _ in self.simulator.flops),
        )


def compile(netlist: Netlist) -> GateTape:  # noqa: A001
    """Compile a netlist to a `GateTape`

    Raises `UnsupportedFunction` for a cell function no opcode reproduces, and
    `UnsupportedCell` (from `Simulator`) for a cell the library does not
    describe at all.
    """
    return _Compiler(netlist).run()
