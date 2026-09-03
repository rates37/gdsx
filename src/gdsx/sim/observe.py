"""Quiescent traces and bus decoding.

Two small utilities that every investigation of a design ends up rewriting
from scratch: run the thing with no data input and watch what it does anyway
(`quiescent`), and read an indexed output bus off a trace as a word instead of
one bit at a time (`bus`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..netlist import Netlist
from .simulator import Simulator

_INDEXED = re.compile(r"^(.*?)[_\[](\d+)\]?$")


@dataclass(frozen=True)
class Trace:
    """`watch`'s values, one row per cycle, in cycle order.

    A row mixes net names (read off the settled values returned by
    `Simulator.step`) and instance names (read off `Simulator.state`,
    i.e. a flop's own held bit), `quiescent` resolves each name against
    whichever of the two has it, once, so callers can watch both without
    caring which is which.
    """

    watch: tuple[str, ...]
    rows: tuple[dict[str, int], ...]

    def __len__(self) -> int:
        return len(self.rows)

    def column(self, name: str) -> tuple[int, ...]:
        """`name`'s value across every cycle, in cycle order."""
        return tuple(row[name] for row in self.rows)


def quiescent(
    design: Netlist,
    cycles: int,
    watch: list[str] | tuple[str, ...],
    *,
    stimulus: dict[str, int] | None = None,
    reset: dict[str, int] | None = None,
) -> Trace:
    """Run `design` for `cycles` cycles under a constant `stimulus` and
    record `watch` every cycle. Reveals what the design does with no data
    input at all.

    `stimulus` defaults to every input port held at 0. `reset` -- typically a
    single input vector with the design's reset asserted -- is applied once,
    before the first recorded cycle, if given.
    """
    values = (
        dict(stimulus)
        if stimulus is not None
        else {p: 0 for p, d in design.ports.items() if d == "input"}
    )
    watch = tuple(watch)
    simulator = Simulator(design)
    if reset is not None:
        simulator.step(reset)

    rows = []
    for _ in range(cycles):
        settled = simulator.step(values)
        rows.append(
            {
                name: settled[name] if name in settled else simulator.state[name]
                for name in watch
            }
        )
    return Trace(watch=watch, rows=tuple(rows))


def bus(trace: Trace, port_prefix: str, *, decode: str = "int") -> list:
    """Read `trace`'s `port_prefix[i]` (or `port_prefix_i`) names as one word
    per cycle, LSB `i=0`, and decode it.

    `analysis.bitorder.indexed_ports` is what finds a bus's name and bit
    order from a `Netlist` in the first place; this reads the values that
    mapping named, once they are in a `Trace`.

    `decode`: `"int"` (default), `"hex"`, or `"ascii"` (`chr(code)` for
    printable codes, `"."` otherwise).
    """
    bits = sorted(
        (int(match.group(2)), name)
        for name in trace.watch
        if (match := _INDEXED.match(name)) and match.group(1) == port_prefix
    )
    if not bits:
        raise ValueError(f"no watched name looks like a bit of {port_prefix!r}")

    words = [sum(row[name] << i for i, name in bits) for row in trace.rows]

    if decode == "int":
        return words
    if decode == "hex":
        width = bits[-1][0] + 1
        digits = (width + 3) // 4
        return [f"0x{w:0{digits}x}" for w in words]
    if decode == "ascii":
        return [chr(w) if 32 <= w < 127 else "." for w in words]
    raise ValueError(f"unknown decode {decode!r}: use 'int', 'hex', or 'ascii'")
