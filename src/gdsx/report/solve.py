"""Rendering for gdsx.solve: how to drive the design so an output goes high"""

from __future__ import annotations

from rich.console import Console

from ..analysis.registers import Register
from ..sim.state import ShiftMode


def render(
    console: Console,
    mode: ShiftMode,
    registers: list[Register],
    output: str,
    solutions: list,
) -> None:
    console.print(f"drive: [bold]{mode.description}[/]")
    console.print(f"\nshowing {len(solutions)} input(s) that assert {output}:")
    for values, verified in solutions:
        loaded = ", ".join(f"{r.serial_input}={v}" for r, v in zip(registers, values))
        mark = "[green]verified[/]" if verified else "[red]FAILED[/]"
        console.print(f"  {loaded}   {mark}")
