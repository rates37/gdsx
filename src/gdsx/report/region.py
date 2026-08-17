"""Rendering for gdsx.region: a carved-out physical block"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape

from .. import netlist as _netlist
from ..region import Region


def render(console: Console, carved: Region, out: Path | None) -> None:
    console.print(escape(report(carved)))
    if carved.inputs:
        console.print(
            "[dim]  inputs:  " + escape(", ".join(carved.inputs[:12])) + "[/]"
        )
    if carved.outputs:
        console.print(
            "[dim]  outputs: " + escape(", ".join(carved.outputs[:12])) + "[/]"
        )
    if out:
        for path in _netlist.write_all(carved.netlist, out):
            console.print(f"  wrote {path}")
        console.print(
            "[dim]the JSON can be passed to any other command in place of the GDS[/]"
        )


def render_bands(console: Console, found) -> None:
    console.print("bands available (use --band N):")
    for i, b in enumerate(found):
        console.print(f"  {i}: {b.lo:9.2f} .. {b.hi:9.2f}  {len(b.members):4d} cells")


def report(region: Region) -> str:
    return "\n".join(
        [
            f"region {region.box}",
            f"  {len(region.inside)} of {region.total_cells} cells "
            f"({len(region.inside) / region.total_cells:.0%})",
            f"  {len(region.inputs)} inputs, {len(region.outputs)} outputs, "
            f"{len(region.internal)} internal nets",
            f"  cut ratio {region.cut_ratio:.2f} -- {region.verdict}",
        ]
    )
