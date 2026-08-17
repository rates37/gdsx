"""Rendering for gdsx.fsm, recovered state machines"""

from __future__ import annotations

from itertools import product

from ..fsm import StateMachine


def describe(machine: StateMachine) -> str:
    return (
        f"{len(machine.states)}-state machine in {machine.register} "
        f"({machine.width} bits, {machine.density:.0%} of the encoding space used)"
    )


def to_table(machine: StateMachine) -> str:
    """The transition table, as text"""
    width = machine.width
    label = {s: f"S{i}" for i, s in enumerate(machine.states)}
    lines = [
        describe(machine),
        f"  inputs: {', '.join(machine.inputs) or '(none)'}",
        "",
        f"  {'state':<8} {'encoding':<{width + 3}} {'outputs':<24} transitions",
    ]
    for state in machine.states:
        outputs = ", ".join(
            f"{p}={v}" for p, v in sorted(machine.moore_outputs[state].items())
        )
        arrows = []
        for index, combo in enumerate(product((0, 1), repeat=len(machine.inputs))):
            nxt = machine.transitions[(state, index)]
            condition = "".join(str(b) for b in combo) or "-"
            arrows.append(f"{condition}->{label[nxt]}")
        marker = " (reset)" if state == machine.reset_state else ""
        lines.append(
            f"  {label[state] + marker:<8} {state:0{width}b}{'':<3} {outputs:<24} {' '.join(arrows)}"
        )
    return "\n".join(lines)
