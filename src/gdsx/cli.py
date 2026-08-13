"""Command line surface for library: `gdsx inspect|pins|extract`."""

from __future__ import annotations
import json as _json
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.markup import escape
from rich.table import Table
import typer

from . import config, connectivity, loader, netlist
from . import analyse as analysis
from . import floorplan as _fp
from . import fsm as control
from . import geometry as _geo
from . import guards as _guards
from . import lift as lifting
from . import normalise as _normalise
from . import region as _region
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
LefOpt = typer.Option(
    None,
    "--lef",
    exists=True,
    help="LEF abstracts, for a GDS that only references its cells",
)


def _load(gds: Path, tech: Optional[Path], top: Optional[str]) -> loader.Design:
    return loader.load(gds, config.load(tech), top)


def _macros(design: loader.Design, path: Optional[Path]) -> dict:
    if path is None:
        return {}
    from . import lef

    return lef.read(path, design.dbu)


def _build(gds: Path, tech, top, lef_path) -> "netlist.Netlist":
    design = _load(gds, tech, top)
    return netlist.build(design, macros=_macros(design, lef_path))


@app.command()
def inspect(
    gds: Path = GdsArg,
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Report hierarchy, layers, cell mix and whether the GDS is self-contained"""
    design = _load(gds, tech, top)
    report = loader.inspect(design, _macros(design, lef))

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


@app.command()
def pins(
    gds: Path = GdsArg,
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Dump the pin oracle for every logic cell used in the design"""
    design = _load(gds, tech, top)
    oracle = PinOracle(design, _macros(design, lef))
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
    lef: Optional[Path] = LefOpt,
):
    """Trace the routing and write the netlist (Verilog + JSON + dot)"""
    design = _load(gds, tech, top)
    nl = netlist.build(design, macros=_macros(design, lef))
    conn = connectivity.trace(design)

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
def normalise(
    gds: Path = GdsArg,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
    fold: bool = typer.Option(
        True, "--fold/--no-fold", help="propagate tie cells and constants"
    ),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="drop cells driving nothing"
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Collapse buffers and inverter pairs, propagate constants

    Structure-preserving, nothing is resynthesised, so what comes out is the
    same design with the place-and-route scaffolding removed.
    """
    nl = _build(gds, tech, top, lef)
    result = _normalise.normalise(nl, fold=fold, clean=clean)

    console.print(
        f"{len(nl.instances)} -> {len(result.netlist.instances)} instances, "
        f"{len(nl.nets)} -> {len(result.netlist.nets)} nets"
    )
    console.print(escape(result.report()))
    for path in netlist.write_all(result.netlist, out):
        console.print(f"  wrote {path}")


@app.command()
def region(
    gds: Path = GdsArg,
    box: Optional[str] = typer.Option(None, "--box", help="x0,y0,x1,y1 in microns"),
    band: Optional[int] = typer.Option(
        None, "--band", help="index of a band from `gdsx placement`"
    ),
    group: Optional[str] = typer.Option(
        None, "--group", help="a label in --groups, by its bounding box"
    ),
    groups_file: Optional[Path] = typer.Option(
        None,
        "--groups",
        exists=True,
        help="JSON of {label: [instance, ...]}, for --group",
    ),
    pad: float = typer.Option(2.0, "--pad", help="microns to grow the selection by"),
    axis: str = typer.Option("x", "--axis", help="axis to band along, for --band"),
    out: Optional[Path] = typer.Option(
        None, "-o", "--out", help="write the region here"
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Carve a physical area of the die out as a netlist of its own

    `slice` cuts along logical lines; this cuts along physical ones. Nets that
    cross the boundary become ports, and the reported cut ratio says whether
    the area was a block or just a rectangle drawn through the middle of one.

    With none of --box/--band/--group, lists the bands so you can pick one.
    """
    design = _load(gds, tech, top)
    nl = netlist.build(design, macros=_macros(design, lef))
    placed = _fp.placements(design, nl)
    if not placed:
        console.print("[yellow]no placement in this file[/]")
        raise typer.Exit(1)
    points = {p.name: (p.x, p.y) for p in placed}

    extents: dict[str, tuple[float, float]] = {}
    sizes: dict[str, tuple[float, float]] = {}
    for p in placed:
        if p.cell not in sizes:
            bb = design.layout.cell(p.cell).bbox()
            sizes[p.cell] = (
                (bb.width() * design.dbu, bb.height() * design.dbu)
                if bb
                else (0.0, 0.0)
            )
        extents[p.name] = sizes[p.cell]

    label = None
    if box:
        try:
            x0, y0, x1, y1 = (float(v) for v in box.split(","))
        except ValueError:
            console.print("[red]--box wants four numbers: x0,y0,x1,y1[/]")
            raise typer.Exit(1)
        area = _region.Box(x0, y0, x1, y1)
    elif band is not None:
        found = [b for b in _geo.bands(points, axis) if len(b.members) >= 5]
        if not 0 <= band < len(found):
            console.print(f"[red]no band {band}; there are {len(found)}[/]")
            raise typer.Exit(1)
        area = _region.bounding_box(points, found[band].members, pad, extents)
        label = f"band{band}"
    elif group and groups_file:
        by_label = _json.loads(groups_file.read_text())
        if group not in by_label:
            console.print(f"[red]no group {group!r}; have {sorted(by_label)}[/]")
            raise typer.Exit(1)
        area = _region.bounding_box(points, by_label[group], pad, extents)
        label = group.replace(" ", "_")
    else:
        console.print("bands available (use --band N):")
        for i, b in enumerate(
            b for b in _geo.bands(points, axis) if len(b.members) >= 5
        ):
            console.print(
                f"  {i}: {b.lo:9.2f} .. {b.hi:9.2f}  {len(b.members):4d} cells"
            )
        return

    carved = _region.extract(nl, points, area, f"{nl.top}_{label or 'region'}", extents)
    console.print(escape(carved.report()))
    if carved.inputs:
        console.print(
            "[dim]  inputs:  " + escape(", ".join(carved.inputs[:12])) + "[/]"
        )
    if carved.outputs:
        console.print(
            "[dim]  outputs: " + escape(", ".join(carved.outputs[:12])) + "[/]"
        )
    if out:
        for path in netlist.write_all(carved.netlist, out):
            console.print(f"  wrote {path}")
        console.print(
            "[dim]the JSON can be passed to any other command in place of the GDS[/]"
        )


@app.command()
def placement(
    gds: Path = GdsArg,
    axis: str = typer.Option("x", "--axis", help="axis to band along: x or y"),
    groups: Optional[Path] = typer.Option(
        None,
        "--groups",
        exists=True,
        help="JSON of {name: [instance, ...]} to break the bands down by",
    ),
    ordered: Optional[Path] = typer.Option(
        None,
        "--ordered",
        exists=True,
        help="JSON of {name: {instance: index}} to test for being laid out in index order",
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Read the floorplan as evidence, cell rows, functional bands, ordered arrays"""
    design = _load(gds, tech, top)
    nl = netlist.build(design, macros=_macros(design, lef))
    points = {p.name: (p.x, p.y) for p in _fp.placements(design, nl)}

    by_group = _json.loads(groups.read_text()) if groups else None
    console.print(escape(_geo.report(points, by_group, axis)))

    if ordered:
        console.print("\n[bold]ORDERED ARRAYS[/]")
        for name, indexed in _json.loads(ordered.read_text()).items():
            result = _geo.ordering(
                name, {k: int(v) for k, v in indexed.items()}, points
            )
            if result is not None:
                console.print("  " + escape(str(result)))


@app.command()
def guards(
    gds: Path = GdsArg,
    fanout: int = typer.Option(
        4, "--fanout", help="minimum readers for a net to be a candidate"
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """What has to be true for each register to change, (i.e., enables)"""
    nl = _build(gds, tech, top, lef)
    result = _guards.find(nl, min_fanout=fanout)
    console.print(escape(result.report()))


@app.command()
def map(
    gds: Path = GdsArg,
    groups_file: Path = typer.Option(
        ...,
        "--groups",
        exists=True,
        help="JSON of {label: [instance, ...]} to colour the cells by",
    ),
    out: Path = typer.Option(Path("out/floorplan.svg"), "-o", "--out"),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Colour a grouping onto the layout's own coordinates

    The grouping comes from wherever you worked it out: `gdsx guards`, a
    hand-written list, another tool, etc.. Placement is produced by a different
    process from connectivity, so a grouping that turns out to be physically
    compact is corroborated by something the analysis never looked at.
    """
    design = _load(gds, tech, top)
    nl = netlist.build(design, macros=_macros(design, lef))
    groups = {
        inst: label
        for label, members in _json.loads(groups_file.read_text()).items()
        for inst in members
    }

    placed = _fp.placements(design, nl)
    if not placed:
        console.print("[yellow]no placement in this file[/]")
        raise typer.Exit(1)

    console.print(escape(_fp.report(placed, groups, _fp.spread(placed, groups))))
    console.print(
        f"wrote {_fp.write(out, _fp.draw(placed, groups, f'{nl.top} by {groups_file.stem}'))}"
    )


@app.command()
def analyse(
    gds: Path = GdsArg,
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Recover registers and identify what the logic computes"""

    nl = _build(gds, tech, top, lef)
    result = analysis.analyse(nl)

    console.print("[bold]registers[/]")
    for reg in result.registers:
        source = f" <- {reg.serial_input}" if reg.serial_input else ""
        console.print(f"  {reg.name}: {reg.description}{source}")
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


@app.command()
def solve(
    gds: Path = GdsArg,
    output: str = typer.Option("S", "--output", help="the output port to assert"),
    limit: int = typer.Option(10, "--limit", help="how many solutions to report"),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Work out how to drive the design so an output goes high"""

    nl = _build(gds, tech, top, lef)
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
def fsm(
    gds: Path = GdsArg,
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Recover state machines by exploring each register's reachable states"""
    nl = _build(gds, tech, top, lef)
    registers = analysis.find_registers(nl)
    machines = control.find_state_machines(nl, registers)

    if not machines:
        console.print(
            "[yellow]no state machines found[/] -- every register reaches most of its "
            "encoding space, which is what data registers do"
        )
        return
    for machine in machines:
        console.print(escape(control.to_table(machine)))
        console.print()


@app.command()
def lift(
    gds: Path = GdsArg,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
    prove: bool = typer.Option(
        True, "--prove/--no-prove", help="check the lift with yosys"
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Recover RTL from the gate netlist and prove the recovery is faithful"""
    nl = _build(gds, tech, top, lef)
    result = lifting.build(nl, analysis.analyse(nl))

    console.print("[bold]recovered[/]")
    for statement in result.statements:
        console.print(f"  [green]{escape(statement)}[/]")
    console.print(
        f"\n{len(result.lifted)} of {len(result.lifted) + len(result.kept)} cells lifted "
        f"({result.coverage:.0%}); the rest stay as gates"
    )

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{nl.top}.rtl.v"
    path.write_text(result.verilog)
    console.print(f"  wrote {path}")

    if not prove:
        console.print("[yellow]not proven -- rerun without --no-prove[/]")
        return
    try:
        proof = lifting.prove(nl, result, out)
    except lifting.verify.YosysMissing as exc:
        console.print(f"[yellow]{exc}; lift is unverified[/]")
        raise typer.Exit(1)
    if proof.proven:
        console.print(f"[green]lift is faithful[/] -- {proof.summary}")
    else:
        console.print(f"[red]lift does NOT match the netlist[/] -- {proof.summary}")
        raise typer.Exit(1)


@app.command()
def verify(
    gds: Path = GdsArg,
    ref: Path = typer.Option(
        ..., "--ref", exists=True, help="reference RTL to prove against"
    ),
    out: Path = typer.Option(
        Path("out"), "-o", "--out", help="where to put the yosys artifacts"
    ),
    structural: bool = typer.Option(
        False,
        "--structural",
        help="match corresponding points instead of inducting over the whole state space. "
        "Much faster when the reference is a lift of this netlist",
    ),
    tech: Optional[Path] = TechOpt,
    top: Optional[str] = TopOpt,
    lef: Optional[Path] = LefOpt,
):
    """Prove the extracted netlist equivalent to a reference RTL (needs yosys installed)"""
    nl = _build(gds, tech, top, lef)
    paths = netlist.write_all(nl, out)
    generic = next(p for p in paths if p.name.endswith(".generic.v"))

    console.print(f"proving {generic} == {ref} ...")
    prove = equiv.structural_equivalence if structural else equiv.equivalence
    try:
        result = prove(generic, ref, nl.top, out)
    except equiv.YosysMissing as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    if result.proven:
        console.print(f"[green]equivalent[/] -- {result.summary}")
    else:
        console.print(f"[red]not proven[/] -- {result.summary}")
        tag = "equiv_structural.log" if structural else "equiv.log"
        console.print(f"[dim]full log: {out / tag}[/]")
        raise typer.Exit(1)


def main() -> None:
    app()
