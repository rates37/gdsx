"""Differential validation: run two models over the same vectors, find the
first cycle where they disagree.

Either side of the comparison may be a `GateTape` (run through
`TapeExecutor`) or a plain callable taking one vector and returning the
observable state after that cycle -- the shape a JS callback has once it
crosses the Pyodide boundary. The two are interchangeable: a tape/tape pair
checks the Python and TypeScript executors never drift (`tests/test_gate_
tape.py` already does this for the opcode table itself); a tape/callback
pair is a player's model checked against the real design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .execute import TapeExecutor
from .tape import GateTape

# One clock cycle's worth of primary-input assignments.
Vector = dict[str, int]

# `vector -> observable state after that cycle`, keyed by whatever names the
# model chooses to expose.
Stepper = Callable[[Vector], dict[str, int]]

Model = GateTape | Stepper


def _stepper(model: Model) -> Stepper:
    if isinstance(model, GateTape):
        executor = TapeExecutor(model)

        def step(vector: Vector) -> dict[str, int]:
            executor.step(vector)
            return {**executor.values_by_name(), **executor.state_by_name()}

        return step
    return model


@dataclass(frozen=True)
class Divergence:
    """The first cycle and signal at which the two models disagreed."""

    cycle: int
    signal: str
    a: int
    b: int


@dataclass(frozen=True)
class DiffResult:
    """The outcome of one `diff_models` run.

    `watch` is the signal set actually compared, the caller's `watch` if
    given, otherwise whatever names both models exposed at the first cycle.
    `cycles_run` is how many vectors were actually stepped before stopping;
    equal to `len(vectors)` when the models agree throughout, and to
    `first.cycle + 1` otherwise -- there is nothing to learn from continuing
    past the first mismatch.
    """

    watch: tuple[str, ...]
    n_vectors: int
    cycles_run: int
    agree: bool
    first: Divergence | None


def diff_models(
    a: Model,
    b: Model,
    vectors: list[Vector],
    watch: list[str] | tuple[str, ...] | None = None,
) -> DiffResult:
    """Run `a` and `b` over `vectors`, one cycle at a time, and report the
    first cycle where a watched signal differs.

    `watch` restricts the comparison to named signals; with no `watch`, every
    name both models exposed at the first cycle is compared (the intersection
    of the two models' state, not their union. A name only one model knows
    about is not a disagreement, it is missing data).

    Raises `ValueError` if `watch` is given but names a signal absent from
    either model, or if no `watch` is given and the two models share no
    observable name at all.
    """
    step_a, step_b = _stepper(a), _stepper(b)

    resolved_watch = tuple(watch) if watch is not None else None
    first: Divergence | None = None
    cycles_run = 0

    for cycle, vector in enumerate(vectors):
        state_a = step_a(vector)
        state_b = step_b(vector)
        cycles_run = cycle + 1

        if resolved_watch is None:
            resolved_watch = tuple(sorted(set(state_a) & set(state_b)))
            if not resolved_watch:
                raise ValueError(
                    "the two models share no observable signal name; pass "
                    "watch= explicitly"
                )

        for name in resolved_watch:
            if name not in state_a or name not in state_b:
                raise ValueError(
                    f"{name!r} is not observable on both models at cycle {cycle}"
                )
            if state_a[name] != state_b[name]:
                first = Divergence(cycle, name, state_a[name], state_b[name])
                break
        if first is not None:
            break

    return DiffResult(
        watch=resolved_watch or (),
        n_vectors=len(vectors),
        cycles_run=cycles_run,
        agree=first is None,
        first=first,
    )
