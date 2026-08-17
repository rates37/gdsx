"""Rendering for gdsx.inspect: hierarchy, layers, cell mix, pin source"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..loader import Inspection


def render(console: Console, report: Inspection) -> None:
    console.print(
        f"[bold]{report.top}[/]  dbu={report.dbu}  layers={len(report.layers)}"
    )
    console.print(
        f"pin source: [green]{report.pin_source}[/]"
        if report.self_contained
        else f"pin source: [red]{report.pin_source}[/] (cells are abstract; supply --lef)"
    )

    table = Table("kind", "cells", "instances")
    for kind, counter in (
        ("logic", report.logic_cells),
        ("non-logic (tap/decap/fill)", report.nonlogic_cells),
        ("other (via/unknown)", report.unknown_cells),
    ):
        table.add_row(kind, str(len(counter)), str(sum(counter.values())))
    console.print(table)

    console.print("\n[bold]logic cells[/]")
    for name, n in sorted(report.logic_cells.items(), key=lambda kv: -kv[1]):
        console.print(f"  {n:>4} x {name}")

    console.print("\n[bold]top-level labels[/]")
    for text, layer in report.top_labels:
        console.print(f"  {text}  ({layer})")
