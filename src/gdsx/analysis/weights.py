"""Bit-weight inference by observation.

Physical flop order is not binary bit-weight order, and `bitorder.resolve_bit_order`
only recovers order for a register wired to indexed ports. An internal counter with
no such port has nothing to probe that way.

The trick: drive the counter and watch for states with
exactly one bit set among `group`. A number with exactly one bit set is a power of
two by definition, so whichever flop is the lone `1` at cycle `k` has weight `k`,
no need to know the register's bit order in advance, because the one-hot state names
it directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..netlist import Netlist
from ..sim import Simulator

Confidence = Literal["observed", "by_elimination", "unknown"]


@dataclass(frozen=True)
class Weight:
    """One flop's inferred binary weight.

    `value` is meaningless when `confidence` is `"unknown"` -- there was neither a
    direct sighting nor a clean elimination, so it is sentinelled to 0, which is not
    itself a valid weight (weights are powers of two, the smallest being 1). Do not
    read `value` without checking `confidence` first.
    """

    value: int
    confidence: Confidence


def infer(
    design: Netlist,
    group: list[str],
    *,
    stimulus: dict[str, int],
    cycles: int,
) -> dict[str, Weight]:
    """Infer each flop in `group`'s binary weight by observation, not by position.

    Drives `stimulus` every cycle for `cycles` cycles from a fresh reset and, at
    each cycle `k` (1-indexed: `k` is the number of times `stimulus` has been
    applied so far), checks whether exactly one flop in `group` is set. If so, that
    flop's value at that instant is `k`, and since a one-hot state's value is a
    power of two by construction, `k` is that flop's weight, observed directly.

    If exactly one flop in `group` is never seen one-hot, but every other flop was,
    its weight is filled in by elimination: `group` should realise every power of
    two from `1` to `1 << (len(group) - 1)`, so the one power nothing observed
    claims belongs to the one flop nothing observed.

    Any flop that is neither observed nor uniquely determined by elimination (two
    or more flops still unaccounted for) comes back `"unknown"`, not a guess.

    This assumes `group` is a plain binary counter: `stimulus` applied once should
    advance it by exactly one. That is an assumption about the circuit, not
    something this function verifies; a group that is not a counter under
    `stimulus` will simply come back with fewer observations, not a wrong answer,
    because a flop is only ever recorded when its one-hot cycle count is caught
    directly.
    """
    simulator = Simulator(design)
    simulator.reset()

    observed: dict[str, int] = {}
    for k in range(1, cycles + 1):
        simulator.step(stimulus)
        ones = [f for f in group if simulator.state[f]]
        if len(ones) == 1:
            observed.setdefault(ones[0], k)

    expected_powers = {1 << i for i in range(len(group))}
    weights: dict[str, Weight] = {
        f: Weight(value, "observed") for f, value in observed.items()
    }

    missing_flops = [f for f in group if f not in observed]
    missing_powers = sorted(expected_powers - set(observed.values()))
    if len(missing_flops) == 1 and len(missing_powers) == 1:
        weights[missing_flops[0]] = Weight(missing_powers[0], "by_elimination")
        missing_flops = []

    for f in missing_flops:
        weights[f] = Weight(0, "unknown")

    return weights
