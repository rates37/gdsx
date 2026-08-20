"""The layout-generation driver: Verilog + a layout spec in, `design.gds`
out. See docs/game/layout-guide.md §7.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import synth
from ..config import TechConfig, load as load_tech
from ..geo.types import Trans
from .compare import Comparison, compare
from .gdsii_write import Cell as GdsCell
from .gdsii_write import write_gds
from .place import LayoutSpec, Placement, place
from .route import RouteError, build_pin_table, route

# Row-thinning factors tried in turn when the router runs out of tracks.
SPREADS = (1.0, 1.5, 2.25, 3.4, 5.0, 7.5, 12.0)


@dataclass
class BuildReport:
    instances: int
    nets: int
    core_width: int
    rows: int
    out_path: Path
    recipe: str
    spread: float = 1.0
    # routing diagnostics -- how hard the router had to work, which is the
    # early warning that a spec is too dense for the next puzzle up
    jogged_pins: int = 0
    max_column_offset: int = 0
    max_track_offset: int = 0
    filler_cells: int = 0
    check: Comparison | None = None

    @property
    def ok(self) -> bool:
        return self.check is None or self.check.ok

    def lines(self) -> list[str]:
        out = [
            f"{self.instances} instances, {self.nets} nets, "
            f"{self.filler_cells} filler cells",
            f"core {self.core_width} nm wide x {self.rows} rows "
            f"({self.rows * 2720} nm), recipe {self.recipe}, "
            f"row spread {self.spread}x",
            f"routing: {self.jogged_pins} pins needed a detour "
            f"(worst {self.max_column_offset} columns, "
            f"{self.max_track_offset} tracks off ideal)",
        ]
        if self.check is not None:
            out.append(str(self.check))
        return out


def build(
    source: str,
    top: str,
    spec: LayoutSpec,
    out_path: Path,
    workdir: Path,
    reference: Path,
    tech: TechConfig | None = None,
    recipe: synth.Recipe | None = None,
    check: bool = False,
) -> BuildReport:
    """Verilog source + a `LayoutSpec` in, `design.gds` out.

    `reference` is the GDS the standard-cell geometry is copied from --
    `samples/puzzle.gds` for every puzzle in the pack, since it is the only
    self-contained cell library in the repository (layout-guide.md §3).

    With `check`, the written file is read back and extracted, and the result
    compared against the netlist that went in (layout-guide.md §8). That
    doubles the build time and is the only thing that actually proves the
    geometry is right, so it is on by default from the CLI and off for
    callers that do their own checking.
    """
    tech = tech or load_tech()
    nl = synth.from_verilog(source, top, workdir, recipe)
    pin_table = build_pin_table(tech, reference)

    placed, routed, spread = _place_and_route(nl, spec, reference, pin_table)

    write_gds(
        out_path,
        top,
        routed.boundaries,
        [
            GdsCell(placed.cells[name], _trans(p))
            for name, p in sorted(placed.placements.items())
        ],
        routed.texts,
        set(placed.cells.values()),
        reference,
    )

    report = BuildReport(
        instances=len(nl.instances),
        nets=len(nl.nets),
        core_width=placed.core_width,
        rows=placed.rows,
        out_path=out_path,
        recipe=(recipe or synth.RECIPES[0]).name,
        spread=spread,
        jogged_pins=routed.jogged_pins,
        max_column_offset=routed.max_column_offset,
        max_track_offset=routed.max_track_offset,
        filler_cells=len(placed.placements) - len(nl.instances),
    )
    if check:
        from .. import config as config_mod, loader, netlist as netlist_mod

        design = loader.load(out_path, tech or config_mod.load())
        report.check = compare(nl, netlist_mod.build(design))
    return report


def _place_and_route(nl, spec, reference: Path, pin_table):
    """Place, route, and if the router runs out of tracks, thin the rows and
    try again.

    `SPREADS` holds the core width fixed and adds rows, which is the only
    direction that adds routing resource without also adding demand -- see
    `place()`. Each step is roughly 1.5x the last, so the sequence covers a
    12x range in six tries; a design that still cannot route at 12x sparsity
    has something wrong with it that more area will not fix, and the router's
    own error is the right thing to surface at that point.
    """
    last: RouteError | None = None
    for spread in SPREADS:
        placed = place(nl, spec, reference, spread=spread)
        try:
            routed = route(
                nl,
                placed.placements,
                placed.cells,
                pin_table,
                placed.core_width,
                placed.rows,
            )
        except RouteError as exc:
            last = exc
            continue
        return placed, routed, spread
    raise RouteError(
        f"{last} -- still unroutable at {SPREADS[-1]}x row spread, so this is "
        f"not a density problem. See docs/game/layout-guide.md section 7.3."
    )


def _trans(p: Placement) -> Trans:
    return Trans(0, p.mirror, p.x, p.y)