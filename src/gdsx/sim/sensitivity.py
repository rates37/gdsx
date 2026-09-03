"""Temporal sensitivity maps: for each cycle, which state elements react.

Generalises the single-pulse probe (`probe`) into a sweep: `map` probes
the same design once per cycle in `cycles` and reports which watched elements
moved for each one. This is the technique that found the main puzzle's
per-cycle write slots (workspace/pair_sensitivity.py), not by reading Liberty,
but by asking the running circuit directly which cycle a perturbation lands on.

Built on the gate tape (`sim.tape` / `sim.execute`), not on `Simulator`: a
sweep over `cycles` cycles, each re-run for `cycles` steps, is `cycles ** 2`
tape steps. `Simulator` re-walks a parsed Liberty expression per gate per
cycle and is roughly two orders of magnitude too slow to make that sweep an
interactive operation rather than a batch job.

Two self-checks are part of the API, not left to the caller, because they are
the only way to know a sweep was set up correctly rather than just quietly
wrong:

- a watched element that reacts to no cycle at all: the wrong elements are
  being watched, or `perturb` never reaches them;
- a cycle that touches no watched element: an element was missed, or
  `cycles` is shorter than the design's write window.

Both are `warnings.warn`, not exceptions: a sweep with either problem is still
data, and the caller may be probing precisely to find out which elements
matter or how wide the window is.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from ..core.netlist import Netlist
from .execute import TapeExecutor
from .tape import GateTape
from .tape import compile as _compile

# One clock cycle's worth of primary-input assignments.
Vector = dict[str, int]


def _tape_of(design: Netlist | GateTape) -> GateTape:
    return design if isinstance(design, GateTape) else _compile(design)


def _run(
    tape: GateTape,
    base_vector: Vector,
    perturbations: dict[int, Vector],
    cycles: int,
    reset: Vector | None,
) -> TapeExecutor:
    """One full trace: `base_vector` held every cycle, overridden at the
    cycles named in `perturbations`.

    `reset` is one extra step applied before the trace, for designs whose
    async reset must be pulsed rather than assumed true of a freshly
    constructed executor (whose flop state already starts at 0).
    """
    executor = TapeExecutor(tape)
    if reset is not None:
        executor.step(reset)
    for cycle in range(cycles):
        vector = dict(base_vector)
        vector.update(perturbations.get(cycle, {}))
        executor.step(vector)
    return executor


@dataclass(frozen=True)
class ProbeResult:
    """One baseline trace and one perturbed trace, diffed over `watch`.

    `changed` is `watch`, in `watch`'s order, restricted to the elements whose
    final value differs between the two traces.
    """

    watch: tuple[str, ...]
    baseline: dict[str, int]
    perturbed: dict[str, int]
    changed: tuple[str, ...]


def probe(
    design: Netlist | GateTape,
    base_vector: Vector,
    perturbations: dict[int, Vector],
    watch: list[str] | tuple[str, ...],
    *,
    cycles: int,
    reset: Vector | None = None,
) -> ProbeResult:
    """Run `base_vector` for `cycles` cycles, once as given and once with
    `perturbations` applied, and report which of `watch`'s final values moved.

    `perturbations` maps a cycle index to the input overrides that apply for
    that one cycle only -- every other cycle runs `base_vector` unperturbed. A
    single pulse is `{cycle: {"I": 1}}`; nothing stops several at once.
    """
    tape = _tape_of(design)
    watch = tuple(watch)
    base_state = _run(tape, base_vector, {}, cycles, reset).state_by_name()
    pert_state = _run(tape, base_vector, perturbations, cycles, reset).state_by_name()
    baseline = {name: base_state[name] for name in watch}
    perturbed = {name: pert_state[name] for name in watch}
    changed = tuple(name for name in watch if baseline[name] != perturbed[name])
    return ProbeResult(
        watch=watch, baseline=baseline, perturbed=perturbed, changed=changed
    )


@dataclass(frozen=True)
class SensitivityMap:
    """Which watched elements a lone single-cycle perturbation moves, for
    every cycle in `range(cycles)`.

    `hits` and `by_cycle` are the same data viewed two ways -- by element and
    by cycle -- because both are real questions a caller asks of this data:
    "which cycle writes this flop" and "what does this cycle write" both come
    up, and computing one from the other is what `cycles_for`/`elements_for`
    are for.
    """

    cycles: int
    watch: tuple[str, ...]
    hits: dict[str, tuple[int, ...]]
    by_cycle: dict[int, tuple[str, ...]]
    unreactive: tuple[str, ...]
    silent_cycles: tuple[int, ...]

    def cycles_for(self, element: str) -> list[int]:
        """Cycles whose lone perturbation moved `element`."""
        return list(self.hits.get(element, ()))

    def elements_for(self, cycle: int) -> list[str]:
        """Watched elements a perturbation at `cycle` alone moved."""
        return list(self.by_cycle.get(cycle, ()))


def map(  # noqa: A001 - `sensitivity.map` is the documented name
    design: Netlist | GateTape,
    *,
    cycles: int,
    baseline: Vector,
    perturb: Vector,
    watch: list[str] | tuple[str, ...],
    reset: Vector | None = None,
) -> SensitivityMap:
    """For every cycle in `range(cycles)`, does perturbing it alone move any
    element in `watch`?

    One baseline trace, then one perturbed trace per cycle: `cycles ** 2` tape
    steps in total, which is what makes a 121-cycle x 92-flop sweep interactive
    rather than a batch job (see module docstring).

    Warns, rather than raising, when either self-check fails: a watched
    element that never reacted (`unreactive`) or a cycle that touched nothing
    watched (`silent_cycles`). Both are still recorded on the returned map, so
    a caller that wants to fail loudly on either can do so itself.
    """
    tape = _tape_of(design)
    watch = tuple(watch)
    base_state = _run(tape, baseline, {}, cycles, reset).state_by_name()

    hits: dict[str, list[int]] = {name: [] for name in watch}
    by_cycle: dict[int, list[str]] = {}
    for cycle in range(cycles):
        state = _run(tape, baseline, {cycle: perturb}, cycles, reset).state_by_name()
        touched = [name for name in watch if state[name] != base_state[name]]
        if touched:
            by_cycle[cycle] = touched
            for name in touched:
                hits[name].append(cycle)

    unreactive = tuple(name for name in watch if not hits[name])
    silent_cycles = tuple(c for c in range(cycles) if c not in by_cycle)

    if unreactive:
        warnings.warn(
            f"{len(unreactive)} watched element(s) never reacted to any "
            f"single-cycle perturbation: {', '.join(unreactive)}",
            stacklevel=2,
        )
    if silent_cycles:
        warnings.warn(
            f"{len(silent_cycles)} cycle(s) touched no watched element: "
            f"{', '.join(str(c) for c in silent_cycles)}",
            stacklevel=2,
        )

    return SensitivityMap(
        cycles=cycles,
        watch=watch,
        hits={name: tuple(cs) for name, cs in hits.items()},
        by_cycle={c: tuple(names) for c, names in by_cycle.items()},
        unreactive=unreactive,
        silent_cycles=silent_cycles,
    )
