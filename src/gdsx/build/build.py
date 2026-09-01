"""The layout-generation driver: Verilog + a layout spec in, `design.gds`
out.
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
    logic_width: int = 0  # summed cell widths, for the occupancy figure
    grid_drops: int = 0
    check: Comparison | None = None

    @property
    def ok(self) -> bool:
        return self.check is None or self.check.ok

    @property
    def height(self) -> int:
        return self.rows * 2720

    @property
    def aspect(self) -> float:
        """Achieved width:height. The spec asks for one; the router's spread
        retry can only be paid for in die shape, so this is what came out."""
        return self.core_width / self.height if self.height else 0.0

    @property
    def occupancy(self) -> float:
        """Fraction of the core's row area holding a logic cell. The spec's
        `fill` is the nominal figure; every step of spread dilutes it, so a
        spec whose `fill` is far above this one is not describing the die."""
        area = self.core_width * self.rows
        return self.logic_width / area if area else 0.0

    def lines(self) -> list[str]:
        out = [
            f"{self.instances} instances, {self.nets} nets, "
            f"{self.filler_cells} filler cells",
            f"core {self.core_width} nm wide x {self.rows} rows "
            f"({self.height} nm), recipe {self.recipe}, "
            f"row spread {self.spread}x",
            f"shape: aspect 1:{1 / self.aspect:.2f} width:height, "
            f"{100 * self.occupancy:.1f} % of the core occupied by logic",
            f"power grid: {self.grid_drops} strap-to-rail drops",
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
    self-contained cell library in the repository.

    With `check`, the written file is read back and extracted, and the result
    compared against the netlist that went in. That doubles the build time
    and is the only thing that actually proves the geometry is right, so it
    is on by default from the CLI and off for callers that do their own
    checking.
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
        logic_width=placed.logic_width,
        grid_drops=routed.grid_drops,
    )
    if check:
        from .. import config as config_mod, loader, netlist as netlist_mod

        design = loader.load(out_path, tech or config_mod.load())
        report.check = compare(nl, netlist_mod.build(design))
    return report


def _place_and_route(nl, spec, reference: Path, pin_table):
    """Place, route, and if the router runs out of resource, thin the design
    and try again.

    `spread` is effective utilisation: each step grows the core in both
    directions, so the requested aspect survives and the die simply gets
    emptier. The scarce resource it buys is riser columns -- a pin's own
    column is exclusive, and both extra rows and extra width create more of
    them. Each step is roughly 1.5x the last, so the sequence covers a 12x
    range in six tries; a design that still cannot route at 12x has something
    wrong with it that more area will not fix, and the router's own error is
    the right thing to surface at that point.

    A spread above 1.0 means the spec's `fill` is not what the die ended up
    at, so `BuildReport` prints the achieved occupancy next to it. Lowering
    `fill` until the spread settles at 1.0 gives a *denser* die than letting
    the retry loop do it, because the loop can only overshoot in 1.5x steps.
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
        f"not a density problem."
    )


def _trans(p: Placement) -> Trans:
    return Trans(0, p.mirror, p.x, p.y)