"""Command line surface for library: `gdsx inspect|pins|extract`."""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from . import config, connectivity, loader, netlist
from . import analyse as analysis
from . import verify as equiv
from .pins import PinOracle

app = typer.Typer(
    add_completion=False, help="Extract a gate-level netlist from a standard-cell GDS."
)
console = Console()

GdsArg = typer.Argument(..., exists=True, dir_okay=False, help="input GDS file")
TechOpt = typer.Option(
    None, "--tech", help="layer-map YAML (default: config/sky130.yaml)"
)
TopOpt = typer.Option(None, "--top", help="top cell name (default: auto-detect)")


def _load(gds: Path, tech: Optional[Path], top: Optional[str]) -> loader.Design:
    return loader.load(gds, config.load(tech), top)


@app.command()
def inspect(
    gds: Path = GdsArg, tech: Optional[Path] = TechOpt, top: Optional[str] = TopOpt
):
    """Report hierarchy, layers, cell mix and whether the GDS is self-contained"""
    design = _load(gds, tech, top)
    report = loader.inspect(design)

    console.print(
        f"[bold]{report.top}[/]  dbu={report.dbu}  layers={len(report.layers)}"
    )
    console.print(
        "pin source: [green]in-GDS labels[/]"
        if report.self_contained
        else "pin source: [red]unavailable[/] (cells are abstract, need PDK LEF)"
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


@app.command()
def pins(
    gds: Path = GdsArg, tech: Optional[Path] = TechOpt, top: Optional[str] = TopOpt
):
    """Dump the pin oracle for every logic cell used in the design"""
    design = _load(gds, tech, top)
    oracle = PinOracle(design)
    used = {name for name, _ in design.instances() if design.tech.is_logic_cell(name)}
    for cell in sorted(used):
        names = sorted({p.name for p in oracle.pins(cell)})
        console.print(f"[bold]{cell}[/]: {', '.join(names)}")


@app.command()
def extract(
    gds: Path = GdsArg,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
):
    """Trace the routing and write the netlist (Verilog + JSON + dot)"""
    design = _load(gds, tech, top)
    conn = connectivity.trace(design)
    nl = netlist.build(design, conn)

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

    for path in netlist.write_all(nl, out):
        console.print(f"  wrote {path}")


@app.command()
def analyse(
    gds: Path = GdsArg, tech: Optional[Path] = TechOpt, top: Optional[str] = TopOpt
):
    """Recover registers and identify what the logic computes"""

    nl = netlist.build(_load(gds, tech, top))
    result = analysis.analyse(nl)

    console.print("[bold]registers[/]")
    for reg in result.registers:
        source = f" <- {reg.serial_input}" if reg.serial_input else ""
        console.print(f"  {reg.name}: {reg.width}-bit shift register{source}")
        console.print(f"    [dim]{' -> '.join(reg.flops)}[/]")

    console.print("\n[bold]blocks[/] (functional match, with the gates backing each call)")
    for block in result.blocks:
        console.print(f"  {block.name}: {block.description}  [dim]({len(block.instances)} cells)[/]")

    console.print("\n[bold]operators[/] (proven over the full input space)")
    for op in result.operators:
        console.print(f"  [green]{op}[/]")
    for note in result.notes:
        console.print(f"  [yellow]{note}[/]")

    covered = {i for b in result.blocks for i in b.instances}
    rest = sorted({i.name for i in nl.instances} - covered)
    if rest:
        console.print(f"\n[dim]not attributed to a block: {len(rest)} cells "
                      f"({', '.join(sorted({r.rsplit('_', 1)[0] for r in rest}))})[/]")


@app.command()
def solve(
    gds: Path = GdsArg,
    output: str = typer.Option("S", "--output", help="the output port to assert"),
    limit: int = typer.Option(10, "--limit", help="how many solutions to report"),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
):
    """Work out how to drive the design so an output goes high"""

    nl = netlist.build(_load(gds, tech, top))
    mode, solutions = analysis.solve(nl, output, limit)
    if mode is None:
        console.print("[red]could not work out how to load the registers[/]")
        raise typer.Exit(1)

    console.print(f"drive: [bold]{mode.description}[/]")
    registers = [
        r for r in analysis.find_registers(nl) if r.serial_input and r.width > 1
    ]
    console.print(f"\nshowing {len(solutions)} input(s) that assert {output}:")
    for values, verified in solutions:
        loaded = ", ".join(f"{r.serial_input}={v}" for r, v in zip(registers, values))
        mark = "[green]verified[/]" if verified else "[red]FAILED[/]"
        console.print(f"  {loaded}   {mark}")


@app.command()
def verify(
    gds: Path = GdsArg,
    ref: Path = typer.Option(
        ..., "--ref", exists=True, help="reference RTL to prove against"
    ),
    out: Path = typer.Option(
        Path("out"), "-o", "--out", help="where to put the yosys artifacts"
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
):
    """Prove the extracted netlist equivalent to a reference RTL (needs yosys installed)"""
    nl = netlist.build(_load(gds, tech, top))
    paths = netlist.write_all(nl, out)
    generic = next(p for p in paths if p.name.endswith(".generic.v"))

    console.print(f"proving {generic} == {ref} ...")
    try:
        result = equiv.equivalence(generic, ref, nl.top, out)
    except equiv.YosysMissing as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    if result.proven:
        console.print(f"[green]equivalent[/] -- {result.summary}")
    else:
        console.print(f"[red]not proven[/] -- {result.summary}")
        console.print(f"[dim]full log: {out / 'equiv.log'}[/]")
        raise typer.Exit(1)


def main() -> None:
    app()
