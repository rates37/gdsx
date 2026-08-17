"""Rendering for gdsx.pins: the pin oracle for every logic cell in use"""

from __future__ import annotations

from rich.console import Console

from ..loader import Design
from ..pins import PinOracle


def render(console: Console, design: Design, oracle: PinOracle) -> None:
    used = {name for name, _ in design.instances() if design.tech.is_logic_cell(name)}
    for cell in sorted(used):
        names = sorted({p.name for p in oracle.pins(cell)})
        console.print(f"[bold]{cell}[/]: {', '.join(names)}")
