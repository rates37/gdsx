"""Address-decode discovery and orbit classification.

`selects` answers "which values of these control flops make this group react to a
perturbation at all" by sweeping every control combination and diffing the group's
D-cone with a baseline settled against that baseline overridden by the perturbation.
That finds an address decoder's selected address without first finding, or even
naming, the decode gate.

`orbit` answers a different question: "what does repeatedly applying this stimulus do
to this group's state, starting here". `fsm.explore` already finds every state a
design can reach; it does not say what happens if you keep doing the same thing to
it, which is what turns "the pair's next-state function looks like a counter" into a
checked fact instead of a name someone stuck on it. Two answers look almost identical
and are not: a saturating counter climbs to a cap and holds there forever after,
so "the flop is set" means "at least N pulses landed". A wrapping counter climbs
back down through zero and keeps going, so the same observation means "the pulse count
is N, mod the wrap period".

Both primitives drive the design directly rather than reading Liberty by hand, and
both go through `core.graph.Graph` for anything structural (Rule 4): neither contains
a cone walk of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Literal, Sequence

from ..core.graph import Graph
from ..sim import Simulator

# The step budget for `orbit` when a full cycle would need more states than this to
# close. A group's state space is `2 ** len(group)`, which is fine to walk in full
# for anything up to a wide byte-ish counter, but is not something to attempt for an
# arbitrary-width register: this caps the walk and reports "counter" (still moving,
# not yet confirmed to do anything else) rather than hanging.
MAX_STEPS = 4096

OrbitKind = Literal[
    "fixed-point", "saturating", "wrapping", "shift", "counter", "unknown"
]


def selects(
    graph: Graph,
    group: Sequence[str],
    control: Sequence[str],
    baseline: dict[str, int],
    perturb: dict[str, int],
) -> list[dict[str, int]]:
    """Sweep every value of `control`; return the ones at which `group` reacts to
    `perturb` at all.

    "Reacts" means: with the control flops forced to that combination, the D pin of
    at least one flop in `group` differs between `baseline` settled and `baseline`
    overridden by `perturb` (`{**baseline, **perturb}`, the same composition
    `sim.sensitivity.map` uses) -- for *some* setting of `group`'s own current
    state. Every combination of `group`'s state is tried, not just all-zero: a
    decode gate can depend on the very flops it is about to write (a mux selected
    by the group's own bits, say), and fixing them at 0 would silently miss
    exactly the address that matters. `baseline` is what the rest of the design
    needs held steady to mean anything at all -- `{"enable": 1}`, typically --
    while `perturb` is the one signal being tested for reaction, e.g. `{"I": 1}`
    against a baseline that already carries `"I": 0`.

    Each hit is `control`-keyed, e.g. `{"dfrtp_2_40": 0, "dfrtp_2_41": 1, ...}`,
    the address itself, not an index into anything.
    """
    simulator = Simulator(graph.netlist)
    simulator.reset()

    group = tuple(group)
    d_pins = {f: graph.d_pin(f) for f in group}
    unresolved = sorted(f for f, p in d_pins.items() if p is None)
    if unresolved:
        raise ValueError(
            f"not a single-D-pin flop, or unconnected: {', '.join(unresolved)}"
        )

    on = {**baseline, **perturb}
    hits: list[dict[str, int]] = []
    for bits in product((0, 1), repeat=len(control)):
        addr = dict(zip(control, bits))
        for flop, value in addr.items():
            simulator.state[flop] = value

        reacts = False
        for gstate in product((0, 1), repeat=len(group)):
            for flop, value in zip(group, gstate):
                simulator.state[flop] = value
            off = simulator.settle(baseline)
            high = simulator.settle(on)
            if any(off[d_pins[f]] != high[d_pins[f]] for f in group):
                reacts = True
                break
        if reacts:
            hits.append(addr)
    return hits


@dataclass(frozen=True)
class Orbit:
    """The state sequence `orbit` walked, and what it turned out to be.

    `states` is every state visited, start first, in the order they occurred,
    including the repeated closing state that proved the cycle. `kind` is the
    classification; see `orbit`'s docstring for what each value means.
    """

    states: tuple[tuple[int, ...], ...]
    kind: OrbitKind


def _value(state: tuple[int, ...]) -> int:
    """`state`, read as a plain binary number with `state[i]` at weight `2 ** i`.

    This is a classification aid, not a claim about the group's real bit-weight
    order (that is `weights.infer`'s job, and it is not assumed here), it only
    needs some fixed, consistent embedding to notice arithmetic structure, and any
    fixed positional order will show a wrap as a wrap regardless of which end is
    really the MSB.
    """
    return sum(bit << i for i, bit in enumerate(state))


def _arithmetic_wrap(values: list[int]) -> bool:
    """True if `values`, read cyclically, increases by exactly 1 every step but one.

    That one exception is the wrap itself, the drop from the cycle's top value back
    to its bottom. A plain incrementing counter, full-range or not (a mod-11 nibble
    counter inside a 4-bit register never visits states 11-15, and still passes this),
    looks like this. A ring/shift counter does not: its steps are bit rotations, not
    arithmetic, so consecutive values jump around rather than climbing by 1.
    """
    n = len(values)
    steps = [values[(i + 1) % n] - values[i] for i in range(n)]
    return sum(1 for step in steps if step == 1) == n - 1


def _rotation(cycle: tuple[tuple[int, ...], ...]) -> bool:
    """True if every state in `cycle` is the previous one rotated by one position.

    Population count constant across the whole cycle is necessary but not
    sufficient (a counter could coincidentally hold weight constant for one step);
    the per-step rotation check is what actually tells a ring counter apart from
    everything else.
    """
    if any(sum(s) != sum(cycle[0]) for s in cycle):
        return False
    rotated = cycle[1:] + cycle[:1]
    for a, b in zip(cycle, rotated):
        left = a[1:] + a[:1]
        right = a[-1:] + a[:-1]
        if b != left and b != right:
            return False
    return True


def _classify(states: list[tuple[int, ...]], first: int, capped: bool) -> OrbitKind:
    if not capped:
        cycle = states[first:-1]
        if len(cycle) == 1:
            return "fixed-point" if first == 0 else "saturating"
        if _arithmetic_wrap([_value(s) for s in cycle]):
            return "wrapping"
        if _rotation(cycle):
            return "shift"
        return "unknown"

    # Hit the step budget without the state ever repeating. Not a confirmed
    # classification -- just what the observed prefix looks like so far.
    values = [_value(s) for s in states]
    if all(b >= a for a, b in zip(values, values[1:])):
        return "counter"
    return "unknown"


def orbit(
    graph: Graph,
    group: Sequence[str],
    stimulus: dict[str, int],
    start: dict[str, int] | None = None,
) -> Orbit:
    """Apply `stimulus` repeatedly to `group`, starting from `start`, and classify
    what comes back.

    `group` is walked as its own isolated system, each step forces `group`'s flops to the
    current state, `settle`s `stimulus` combinationally, and reads the next state
    straight off `group`'s own D pins (`Graph.d_pin`), it never calls
    `Simulator.step`. That distinction matters. `group` usually lives inside a
    design that also contains other running state (an address counter gating when
    `group` even reacts, say), and letting that other state free-run underneath the
    walk would make two visits to the same `group` state falsely look identical,
    the design's actual next move can depend on hidden state this walk cannot see.
    Evaluating `group` as a pure function of its own state and `stimulus` avoids that
    trap entirely, at the cost of needing the right context supplied up front:

    `start` sets the initial value of any flop by name, not only `group`'s, so a
    control/address flop `selects` found necessary can be pinned there for the whole
    walk (never re-forced afterwards, so it stays put), while `group`'s own flops
    start there and then evolve step by step. Anything left unset, in `group` or out
    of it, starts at 0.

    A deterministic step function over a finite state space must eventually revisit
    a state, so the walk always terminates at either a genuine repeat or, for a
    `group` too wide to walk in full (`MAX_STEPS`, see module docstring), a step
    budget, `kind` distinguishes the two:

    - `"fixed-point"`: the very first application already returns the start state.
      `stimulus` does nothing to it, from here.
    - `"saturating"`: some later state maps to itself, after a real transient.
      Climbs to a cap and then holds there: "the flop being set" means "at least
      this many pulses landed", never more precisely than that, because it never
      un-sets again.
    - `"wrapping"`: the state cycles through more than one value, incrementing by
      one each step but for a single wrap back down. "The flop being set" now means
      an exact pulse count, modulo the wrap period, not "at least".
    - `"shift"`: the cycle is a bit rotation, step after step, a ring/shift
      counter, not an arithmetic one.
    - `"counter"`: the step budget was hit before any repeat, and the observed
      values never decreased, still counting, not yet confirmed to do anything
      else. Not a final answer; widen `MAX_STEPS` or drive it further to get one.
    - `"unknown"`: a real cycle was found, but it fits none of the above.
    """
    group = tuple(group)
    simulator = Simulator(graph.netlist)
    simulator.reset()

    d_pins = [graph.d_pin(f) for f in group]
    unresolved = sorted(f for f, p in zip(group, d_pins) if p is None)
    if unresolved:
        raise ValueError(
            f"not a single-D-pin flop, or unconnected: {', '.join(unresolved)}"
        )

    if start is not None:
        for flop, value in start.items():
            simulator.state[flop] = value

    def advance(state: tuple[int, ...]) -> tuple[int, ...]:
        for flop, value in zip(group, state):
            simulator.state[flop] = value
        values = simulator.settle(stimulus)
        return tuple(values[pin] for pin in d_pins)

    limit = min(1 << len(group), MAX_STEPS) if group else 0
    states: list[tuple[int, ...]] = [tuple(simulator.state[f] for f in group)]
    seen: dict[tuple[int, ...], int] = {states[0]: 0}

    while len(states) <= limit:
        state = advance(states[-1])
        states.append(state)
        if state in seen:
            kind = _classify(states, seen[state], False)
            return Orbit(states=tuple(states), kind=kind)
        seen[state] = len(states) - 1

    return Orbit(states=tuple(states), kind=_classify(states, 0, True))
