"""Rendering for gdsx.extract: the traced netlist and how it was assembled"""

from __future__ import annotations

from rich.console import Console

from ..connectivity import Connectivity
from ..netlist import Netlist


def render(console: Console, nl: Netlist, conn: Connectivity) -> None:
    console.print(
        f"{len(nl.instances)} instances, {len(nl.nets)} nets "
        f"({conn.n_clusters} clusters merged by {len(conn.nets)} nets)"
    )
    if conn.dangling_vias:
        console.print(f"[yellow]{len(conn.dangling_vias)} vias landed on nothing[/]")
    if nl.floating:
        console.print(
            f"[yellow]{len(nl.floating)} floating pins[/]: {', '.join(nl.floating[:8])}"
        )
    if nl.conflicts:
        console.print(
            f"[red]{len(nl.conflicts)} pins on multiple nets[/]: {', '.join(nl.conflicts[:8])}"
        )
    console.print(
        "ports: " + ", ".join(f"{p} ({d})" for p, d in sorted(nl.ports.items()))
    )
