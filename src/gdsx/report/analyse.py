"""Rendering for gdsx.analyse: recovered registers, blocks, operators"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from ..analysis.registers import Analysis, describe as describe_register
from ..netlist import Netlist


def render(console: Console, nl: Netlist, result: Analysis) -> None:
    console.print("[bold]registers[/]")
    for reg in result.registers:
        source = f" <- {reg.serial_input}" if reg.serial_input else ""
        console.print(f"  {reg.name}: {describe_register(reg)}{source}")
        console.print(f"    [dim]{' -> '.join(reg.flops)}[/]")

    console.print(
        "\n[bold]blocks[/] (functional match, with the gates backing each call)"
    )
    for block in result.blocks:
        console.print(
            f"  {block.name}: {escape(block.description)}  "
            f"[dim]({len(block.instances)} cells)[/]"
        )

    console.print("\n[bold]operators[/] (proven over the full input space)")
    for op in result.operators:
        console.print(f"  [green]{escape(op)}[/]")
    for note in result.notes:
        console.print(f"  [yellow]{escape(note)}[/]")

    covered = {i for b in result.blocks for i in b.instances}
    rest = sorted({i.name for i in nl.instances} - covered)
    if rest:
        console.print(
            f"\n[dim]not attributed to a block: {len(rest)} cells "
            f"({', '.join(sorted({r.rsplit('_', 1)[0] for r in rest}))})[/]"
        )
