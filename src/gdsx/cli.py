"""Command line surface for library: `gdsx inspect|pins|extract`."""

from __future__ import annotations
import functools
import inspect as _inspect
import json as _json
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.markup import escape
import typer

from . import analyse as analysis
from . import (
    connectivity,
    fsm as control,
    loader,
    netlist,
    region as _region,
    sequential,
)
from . import lift as lifting, normalise as _normalise, verify as equiv
from . import puzzle as puzzle_mod
from .core.context import Design
from .physical import draw as _fp, placement as _geo
from .pins import PinOracle
from .report import (
    analyse as report_analyse,
    draw as report_draw,
    extract as report_extract,
    fsm as report_fsm,
    guards as report_guards,
)
from .report import (
    inspect as report_inspect,
    interface as report_interface,
    lift as report_lift,
    normalise as report_normalise,
)
from .report import (
    pins as report_pins,
    placement as report_placement,
    puzzle as report_puzzle,
    region as report_region,
)
from .report import sequential as report_sequential
from .report import solve as report_solve, verify as report_verify

app = typer.Typer(
    add_completion=False, help="Extract a gate-level netlist from a standard-cell GDS."
)
console = Console()

GdsArg = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    help="input GDS, or a netlist JSON written by `gdsx extract` or `gdsx region`",
)
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


_PARAM, _POK = _inspect.Parameter, _inspect.Parameter.POSITIONAL_OR_KEYWORD
_COMMON_TAIL = [
    ("tech", Optional[Path], TechOpt),
    ("top", Optional[str], TopOpt),
    ("lef", Optional[Path], LefOpt),
]


def with_design(func):
    """Open a `Design` from the four options every command needs, so the
    command itself only ever sees the result.

    `func`'s first parameter must be `design`; whatever else it declares is
    exposed on the CLI between `gds` and the trailing `--tech/--top/--lef`,
    matching the order every command already used.
    """
    extra = list(_inspect.signature(func).parameters.values())[1:]
    common = [
        _PARAM("gds", _POK, annotation=Path, default=GdsArg),
        *extra,
        *(
            _PARAM(name, _POK, annotation=ann, default=default)
            for name, ann, default in _COMMON_TAIL
        ),
    ]

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        design = Design.open(
            kwargs.pop("gds"),
            tech=kwargs.pop("tech"),
            top=kwargs.pop("top"),
            lef=kwargs.pop("lef"),
        )
        return func(design, *args, **kwargs)

    wrapper.__signature__ = _inspect.Signature(common)
    return wrapper


@app.command()
@with_design
def inspect(design: Design) -> None:
    """Report hierarchy, layers, cell mix and whether the GDS is self-contained"""
    report_inspect.render(console, loader.inspect(design.layout, design.macros()))


@app.command()
@with_design
def pins(design: Design) -> None:
    """Dump the pin oracle for every logic cell used in the design"""
    report_pins.render(
        console, design.layout, PinOracle(design.layout, design.macros())
    )


@app.command()
@with_design
def extract(
    design: Design,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
) -> None:
    """Trace the routing and write the netlist (Verilog + JSON + dot)"""
    nl = design.netlist
    report_extract.render(console, nl, connectivity.trace(design.layout))
    for path in netlist.write_all(nl, out):
        console.print(f"  wrote {path}")


@app.command()
@with_design
def normalise(
    design: Design,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
    fold: bool = typer.Option(
        True, "--fold/--no-fold", help="propagate tie cells and constants"
    ),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="drop cells driving nothing"
    ),
) -> None:
    """Collapse buffers and inverter pairs, propagate constants

    Structure-preserving, nothing is resynthesised, so what comes out is the
    same design with the place-and-route scaffolding removed.
    """
    nl = design.netlist
    result = _normalise.normalise(nl, fold=fold, clean=clean)
    report_normalise.render(console, nl, result)
    for path in netlist.write_all(result.netlist, out):
        console.print(f"  wrote {path}")


@app.command()
@with_design
def region(
    design: Design,
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
) -> None:
    """Carve a physical area of the die out as a netlist of its own

    `slice` cuts along logical lines; this cuts along physical ones. Nets that
    cross the boundary become ports, and the reported cut ratio says whether
    the area was a block or just a rectangle drawn through the middle of one.

    With none of --box/--band/--group, lists the bands so you can pick one.
    """
    nl = design.netlist
    placed = _fp.placements(design.layout, nl)
    if not placed:
        console.print("[yellow]no placement in this file[/]")
        raise typer.Exit(1)
    points = {p.name: (p.x, p.y) for p in placed}
    extents = _region.extents_of(design.layout, placed)

    try:
        selected = _region.resolve(
            points,
            extents,
            box=box,
            band=band,
            group=group,
            groups_file=groups_file,
            pad=pad,
            axis=axis,
        )
    except _region.SelectionError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    if selected is None:
        report_region.render_bands(
            console, [b for b in _geo.bands(points, axis) if len(b.members) >= 5]
        )
        return

    area, label = selected
    carved = _region.extract(nl, points, area, f"{nl.top}_{label or 'region'}", extents)
    report_region.render(console, carved, out)


@app.command()
@with_design
def placement(
    design: Design,
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
) -> None:
    """Read the floorplan as evidence, cell rows, functional bands, ordered arrays"""
    nl = design.netlist
    points = {p.name: (p.x, p.y) for p in _fp.placements(design.layout, nl)}
    by_group = _json.loads(groups.read_text()) if groups else None
    ordered_arrays = _json.loads(ordered.read_text()) if ordered else None
    report_placement.render(console, points, by_group, axis, ordered_arrays)


@app.command()
@with_design
def guards(
    design: Design,
    fanout: int = typer.Option(
        4, "--fanout", help="minimum readers for a net to be a candidate"
    ),
) -> None:
    """What has to be true for each register to change, (i.e., enables)"""
    console.print(escape(report_guards.report(design.guards(min_fanout=fanout))))


@app.command()
@with_design
def map(
    design: Design,
    groups_file: Path = typer.Option(
        ...,
        "--groups",
        exists=True,
        help="JSON of {label: [instance, ...]} to colour the cells by",
    ),
    out: Path = typer.Option(Path("out/floorplan.svg"), "-o", "--out"),
) -> None:
    """Colour a grouping onto the layout's own coordinates

    The grouping comes from wherever you worked it out: `gdsx guards`, a
    hand-written list, another tool, etc.. Placement is produced by a different
    process from connectivity, so a grouping that turns out to be physically
    compact is corroborated by something the analysis never looked at.
    """
    nl = design.netlist
    groups = {
        inst: label
        for label, members in _json.loads(groups_file.read_text()).items()
        for inst in members
    }
    placed = _fp.placements(design.layout, nl)
    if not placed:
        console.print("[yellow]no placement in this file[/]")
        raise typer.Exit(1)

    report_draw.render(console, placed, groups, out, nl.top, groups_file.stem)


@app.command()
@with_design
def ports(
    design: Design,
    cycles: int = typer.Option(
        12, help="how long to drive the design for each measurement"
    ),
) -> None:
    """What each pin is for: clock, reset, gate, data"""
    console.print(escape(report_interface.report(design.interface(cycles))))


@app.command()
@with_design
def registers(design: Design) -> None:
    """What kind of thing each register is"""
    nl = design.netlist
    roles = sequential.classify(nl, design.registers(ordered=True))
    console.print(
        escape(report_sequential.report(nl, roles, sequential.pipelines(roles)))
    )


@app.command()
@with_design
def analyse(design: Design) -> None:
    """Recover registers and identify what the logic computes"""
    nl = design.netlist
    report_analyse.render(console, nl, analysis.analyse(nl))


@app.command()
@with_design
def solve(
    design: Design,
    output: str = typer.Option("S", "--output", help="the output port to assert"),
    limit: int = typer.Option(10, "--limit", help="how many solutions to report"),
) -> None:
    """Work out how to drive the design so an output goes high"""
    mode, solutions = analysis.solve(design.netlist, output, limit)
    if mode is None:
        console.print("[red]could not work out how to load the registers[/]")
        raise typer.Exit(1)
    registers = [r for r in design.registers() if r.serial_input and r.width > 1]
    report_solve.render(console, mode, registers, output, solutions)


@app.command()
@with_design
def fsm(design: Design) -> None:
    """Recover state machines by exploring each register's reachable states"""
    nl = design.netlist
    machines = control.find_state_machines(nl, design.registers())

    if not machines:
        console.print(
            "[yellow]no state machines found[/] -- every register reaches most of its encoding space, which is what data registers do"
        )
        return
    for machine in machines:
        console.print(escape(report_fsm.to_table(machine)))
        console.print()


@app.command()
@with_design
def lift(
    design: Design,
    out: Path = typer.Option(Path("out"), "-o", "--out", help="output directory"),
    prove: bool = typer.Option(
        True, "--prove/--no-prove", help="check the lift with yosys"
    ),
) -> None:
    """Recover RTL from the gate netlist and prove the recovery is faithful"""
    nl = design.netlist
    result = lifting.build(nl, analysis.analyse(nl))
    report_lift.render_recovered(console, result)
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
    if not report_lift.render_proof(console, proof):
        raise typer.Exit(1)


@app.command()
@with_design
def verify(
    design: Design,
    ref: Path = typer.Option(
        ..., "--ref", exists=True, help="reference RTL to prove against"
    ),
    out: Path = typer.Option(
        Path("out"), "-o", "--out", help="where to put the yosys artifacts"
    ),
    structural: bool = typer.Option(
        False,
        "--structural",
        help="match corresponding points instead of inducting over the whole state space. Much faster when the reference is a lift of this netlist",
    ),
) -> None:
    """Prove the extracted netlist equivalent to a reference RTL (needs yosys installed)"""
    nl = design.netlist
    paths = netlist.write_all(nl, out)
    generic = next(p for p in paths if p.name.endswith(".generic.v"))
    console.print(f"proving {generic} == {ref} ...")
    prove = equiv.structural_equivalence if structural else equiv.equivalence
    try:
        result = prove(generic, ref, nl.top, out)
    except equiv.YosysMissing as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    if not report_verify.render(console, result, out, structural):
        raise typer.Exit(1)


puzzle_app = typer.Typer(
    add_completion=False, help="Author, check and inspect `.gdsxpuzzle` bundles."
)
app.add_typer(puzzle_app, name="puzzle")

PuzzleDirArg = typer.Argument(
    ...,
    exists=True,
    file_okay=False,
    help="a puzzle directory (manifest.json, design.gds, solution.json, ...)",
)


@puzzle_app.command("bake")
def puzzle_bake(
    puzzle_dir: Path = PuzzleDirArg,
    zip_bundle: bool = typer.Option(
        True, "--zip/--no-zip", help="also write <id>.gdsxpuzzle next to the directory"
    ),
) -> None:
    """Regenerate netlist.json, render.bin and tape.bin from design.gds"""
    try:
        result = puzzle_mod.bake(puzzle_dir, zip_bundle=zip_bundle)
    except puzzle_mod.PuzzleError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    report_puzzle.render_bake(console, result)


@puzzle_app.command("verify")
def puzzle_verify(puzzle_dir: Path = PuzzleDirArg) -> None:
    """Re-solve the puzzle and check the intended solution.

    Asserts: the key raises `success` on the extracted netlist (not the
    RTL); extraction is naming-stable; and the constraint structure
    solution.json describes is the one the netlist implements.
    """
    try:
        result = puzzle_mod.verify(puzzle_dir)
    except puzzle_mod.PuzzleError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    if not report_puzzle.render_verify(console, result):
        raise typer.Exit(1)


@puzzle_app.command("stats")
def puzzle_stats(puzzle_dir: Path = PuzzleDirArg) -> None:
    """Gate/flop counts and difficulty metrics"""
    try:
        result = puzzle_mod.stats(puzzle_dir)
    except puzzle_mod.PuzzleError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    report_puzzle.render_stats(console, result)


@puzzle_app.command("build")
def puzzle_build(
    source: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="the puzzle's Verilog source"
    ),
    top: str = typer.Option(..., "--top", help="the top-level module name"),
    spec_path: Path = typer.Option(
        ...,
        "--spec",
        exists=True,
        dir_okay=False,
        help="a layout spec JSON file (see docs/game/layout-guide.md §9)",
    ),
    out: Path = typer.Option(..., "-o", "--out", help="the design.gds to write"),
    reference: Path = typer.Option(
        Path("samples/puzzle.gds"),
        "--reference",
        exists=True,
        dir_okay=False,
        help="the GDS to copy standard-cell geometry from",
    ),
    recipe_name: str = typer.Option(
        "default", "--recipe", help="a synth.py recipe name"
    ),
    check: bool = typer.Option(
        True,
        "--check/--no-check",
        help="extract the result and compare it against the netlist that went in",
    ),
) -> None:
    """Synthesise, place and route RTL into a design.gds.

    Runs yosys for synthesis and this repository's own placer and router for
    the rest -- see docs/game/layout-guide.md, which specifies the flow. The
    OpenLane path that produced samples/puzzle.gds is not reproducible here
    (no `openlane` binary and no local PDK), and is not what this builds.
    """
    import tempfile

    from . import synth
    from .build.build import build as build_layout
    from .build.spec import SpecError, load_spec

    recipes = {r.name: r for r in synth.RECIPES}
    if recipe_name not in recipes:
        console.print(f"[red]unknown recipe {recipe_name!r}[/]; "
                      f"try one of {sorted(recipes)}")
        raise typer.Exit(1)

    text = source.read_text()
    workdir = Path(tempfile.mkdtemp(prefix=f"{top}_build_"))
    try:
        # The spec's groups are resolved against the netlist, so synthesis
        # has to run before the spec can be read. It runs once: `build`
        # takes the same workdir and yosys caches nothing, so this is the
        # one place the cost is paid twice, and it is a second.
        nl = synth.from_verilog(text, top, workdir, recipes[recipe_name])
        spec = load_spec(spec_path, nl)
        report = build_layout(
            text, top, spec, out, workdir, reference,
            recipe=recipes[recipe_name], check=check,
        )
    except (SpecError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    for line in report.lines():
        console.print(line)
    console.print(f"wrote {report.out_path}")
    if not report.ok:
        raise typer.Exit(1)


def main() -> None:
    app()
