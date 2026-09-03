"""Rendering for gdsx.lift: recovered RTL and how much of the netlist it covers"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from ..lift import Lift
from ..verify import EquivalenceResult


def render_recovered(console: Console, result: Lift) -> None:
    console.print("[bold]recovered[/]")
    for statement in result.statements:
        console.print(f"  [green]{escape(statement)}[/]")
    console.print(
        f"\n{len(result.lifted)} of {len(result.lifted) + len(result.kept)} cells lifted "
        f"({result.coverage:.0%}); the rest stay as gates"
    )


def render_proof(console: Console, proof: EquivalenceResult) -> bool:
    if proof.proven:
        console.print(f"[green]lift is faithful[/] -- {proof.summary}")
    else:
        console.print(f"[red]lift does NOT match the netlist[/] -- {proof.summary}")
    return proof.proven
