"""Rendering for gdsx.verify: equivalence against a reference RTL"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from ..verify import EquivalenceResult


def render(
    console: Console, result: EquivalenceResult, out: Path, structural: bool
) -> bool:
    if result.proven:
        console.print(f"[green]equivalent[/] -- {result.summary}")
        return True
    console.print(f"[red]not proven[/] -- {result.summary}")
    tag = "equiv_structural.log" if structural else "equiv.log"
    console.print(f"[dim]full log: {out / tag}[/]")
    return False
