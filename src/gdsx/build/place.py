"""A row placer: a `Netlist` plus a `LayoutSpec` in, per-instance coordinates
out. See docs/game/layout-guide.md §7.2 and §9.

Not a quality placer: no wirelength objective, no annealing, no legalisation
loop. Cells go down in spec order, row by row.

It does do one thing beyond that, and it is not optional. Within a group,
instances are ordered by a depth-first walk of the netlist rather than by
name, so that cells which are connected end up near each other. Instance
names come out of synthesis grouped by cell type, and placing in name order
puts every AND gate in one band and every flop in another, which leaves
every net spanning the die. The router cannot recover from that -- see
`_connectivity_order` and route.py's module docstring. This is placement
quality in the only sense the router needs: a linear order, not a
coordinate optimisation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..geo import gdsii

# Measured from samples/puzzle.gds -- see docs/game/layout-guide.md §5.
# Re-measured while implementing this module; all four numbers matched the
# document exactly, so they are trusted constants here rather than derived
# at runtime on every call.
DBU_UM = 0.001
SITE = 460  # nm, the x placement grid
ROW_HEIGHT = 2720  # nm
OVERHANG = 240  # nm, all four sides, for every logic cell except the tap
TAP_OVERHANG = 190  # nm, left/right only, for tapvpwrvgnd_1

PREFIX = "sky130_fd_sc_hd__"
DECAP = f"{PREFIX}decap_3"
TAP = f"{PREFIX}tapvpwrvgnd_1"
TAP_SPACING = 15000  # nm, "every ~15 um" per layout-guide.md §7.2 step 5
# Empty rows for a visible block boundary (layout-guide.md §7.2). A channel is
# only a boundary if `physical.placement.bands` splits on it, and that splits
# where the spacing exceeds 4x the median -- which here is one origin line,
# 5.44 um. Four empty rows leave a 10.88-16.32 um gap, under the 21.76 um
# threshold, so the eight bands of puzzle 3 came back as one. Ten empty rows
# clear it with margin in both the alignments the row packer produces.
CHANNEL_ROWS = 10

# Filler is cosmetic -- decap and tap carry no function and `lookup()` returns
# None for both -- so it is budgeted rather than poured into every gap. A
# spread-out core is mostly empty space, and filling all of it costs one
# instance per 1.38 um of slack: on a sparse die that is tens of thousands of
# cells, which extraction then has to trace. The budget is a multiple of the
# logic cell count, so the die stays plausibly populated without the filler
# ever dominating the design it is decorating.
FILLER_BUDGET = 2.0


def measure_widths(reference: Path) -> dict[str, int]:
    """Full cell name -> width in database units, measured from `reference`.

    layout-guide.md §5: width = (bbox width - 2*overhang), *snapped* to the
    site grid -- "do not compute a width as bbox_width - 0.48", the tap cell
    has different overhangs and some cells' raw bbox width is a site or two
    off after subtracting the nominal overhang (dfxtp_2 measures 7720 nm
    exactly this way, 16.78 sites, not 17 -- rounding is required, not
    optional, confirmed against real measurements while building this).
    Correctness is established downstream instead, per the document's own
    cross-check: consecutive x origins in a placed row must differ by
    exactly the left cell's width (see place()'s test coverage).
    """
    lay = gdsii.read_file(reference)
    widths: dict[str, int] = {}
    for name in lay._lib.structures:
        if not name.startswith(PREFIX):
            continue
        base = name[len(PREFIX) :].rsplit("_", 1)[0]
        overhang = TAP_OVERHANG if base == "tapvpwrvgnd" else OVERHANG
        bbox_width = lay.cell_bbox(name).width()
        sites = round((bbox_width - 2 * overhang) / SITE)
        widths[name] = sites * SITE
    return widths


@dataclass(frozen=True)
class LayoutSpec:
    """The pack's layout intent, translated into something a placer can
    execute. See layout-guide.md §9 for the field-by-field rationale.
    """

    fill: float
    aspect: float = 1.0
    groups: dict[str, str] = field(default_factory=dict)
    order: tuple[str, ...] = ()
    mode: str = "banded"  # "banded" | "interleaved" | "scattered"
    channels: tuple[str, ...] = ()
    seed: int = 0


@dataclass(frozen=True)
class Placement:
    x: int
    y: int
    mirror: bool


@dataclass
class PlacementResult:
    placements: dict[str, Placement]
    cells: dict[str, str]  # instance name -> full cell name, logic + filler
    core_width: int
    rows: int


def _group_of(name: str, spec: LayoutSpec) -> str:
    return spec.groups.get(name, "")


HUB_FANOUT = 12  # a net on more than this many instances is a hub (clk, reset,
# an enable): it says nothing about locality, so it is left out of the
# adjacency used for ordering. Including it makes every instance a neighbour
# of every other and the walk below degenerates to arbitrary order.


def _connectivity_order(nl, names: list[str]) -> dict[str, int]:
    """A linear order in which connected instances sit near each other.

    Instance names are assigned per cell type (`and2_1`, `and2_2`, ...), so
    ordering by name groups every AND gate together and scatters the cells
    that actually talk to each other -- which is the worst case for the
    router, because a net's pins then land anywhere on the die and its links
    have to cross it. A depth-first walk of the netlist instead lays a shift
    register or a carry chain out as a chain, which is what makes the links
    short.

    Deterministic: seeds are taken in name order and neighbours are visited
    in name order, so the same netlist always produces the same layout
    (layout-guide.md §10 point 8).
    """
    members = set(names)
    adjacency: dict[str, set[str]] = {n: set() for n in names}
    for net, refs in nl.nets.items():
        if net in nl.power_nets:
            continue
        on_net = sorted({r.split("/")[0] for r in refs} & members)
        if len(on_net) > HUB_FANOUT:
            continue
        for i, a in enumerate(on_net):
            for b in on_net[i + 1 :]:
                adjacency[a].add(b)
                adjacency[b].add(a)

    order: list[str] = []
    seen: set[str] = set()
    for seed in sorted(names):
        if seed in seen:
            continue
        stack = [seed]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            order.append(node)
            stack.extend(sorted(adjacency[node] - seen, reverse=True))
    return {name: i for i, name in enumerate(order)}


def _ordered_instances(nl, names: list[str], spec: LayoutSpec) -> list[str]:
    near = _connectivity_order(nl, names)

    if spec.mode == "banded":
        rank = {g: i for i, g in enumerate(spec.order)}
        return sorted(
            names, key=lambda n: (rank.get(_group_of(n, spec), len(rank)), near[n])
        )

    if spec.mode == "interleaved":
        buckets: dict[str, list[str]] = {}
        for n in sorted(names, key=lambda n: near[n]):
            buckets.setdefault(_group_of(n, spec), []).append(n)
        order: list[str] = []
        while any(buckets.values()):
            for g in list(buckets):
                if buckets[g]:
                    order.append(buckets[g].pop(0))
        return order

    if spec.mode == "scattered":
        # The named group (spec.order[0]) is scattered via a seeded
        # permutation across the whole core; everything else stays banded.
        # layout-guide.md §9: "Use a fixed seed and record it".
        scattered_group = spec.order[0] if spec.order else None
        rest = sorted(
            (n for n in names if _group_of(n, spec) != scattered_group),
            key=lambda n: near[n],
        )
        scattered = sorted(
            (n for n in names if _group_of(n, spec) == scattered_group),
            key=lambda n: near[n],
        )
        rng = random.Random(spec.seed)
        result = list(rest)
        for name in scattered:
            pos = rng.randrange(len(result) + 1)
            result.insert(pos, name)
        return result

    raise ValueError(f"unknown placement mode {spec.mode!r}")


def _round_up(value: float, grid: int) -> int:
    return math.ceil(value / grid) * grid


def _row_y(row: int, mirror: bool) -> int:
    # A mirrored row's origin is at the row's top, so adjacent rows share a
    # power rail -- confirmed by measurement, layout-guide.md §5.
    return (row + 1) * ROW_HEIGHT if mirror else row * ROW_HEIGHT


def _assign_rows(order: list[str], widths: dict[str, int], cell_of: dict[str, str],
                 capacity: int, spec: LayoutSpec) -> list[list[str]]:
    """Instances split into rows, each row holding at most `capacity` worth of
    real cells. Empty rows are inserted where `spec.channels` asks for a
    visible block boundary.
    """
    rows: list[list[str]] = [[]]
    used = 0
    prev_group: str | None = None
    channel_groups = set(spec.channels)

    for n in order:
        group = _group_of(n, spec)
        if prev_group is not None and group != prev_group and prev_group in channel_groups:
            rows.extend([] for _ in range(CHANNEL_ROWS + 1))
            used = 0
        w = widths[cell_of[n]]
        if used + w > capacity and used > 0:
            rows.append([])
            used = 0
        rows[-1].append(n)
        used += w
        prev_group = group
    return rows


def _lay_out_row(
    members: list[str],
    row: int,
    widths: dict[str, int],
    cell_of: dict[str, str],
    core_width: int,
    placements: dict[str, Placement],
    cells: dict[str, str],
    filler_budget: int,
) -> None:
    """Place one row's cells across the full core width, with the row's spare
    space shared out evenly between them rather than left as one tail gap.

    This is the one place this module departs from layout-guide.md §7.2's
    letter, which says to abut a row's cells and pad the tail. Spreading them
    is what actually delivers §9's `fill`: with the cells abutted, a row is a
    dense block of pins with an empty strip beside it, and the router cannot
    get a met2 riser across that block -- every column within it is claimed
    by a pin for the whole height of the row. Spread out at 30 % fill, the
    same row leaves roughly six columns free between neighbours, which is
    what makes vertical routing across a row possible at all. The spare space
    is filled with decap, plus a tap roughly every 15 um; `lookup()` returns
    None for both, so extraction ignores them (§7.2 step 5).
    """
    mirror = row % 2 == 1
    y = _row_y(row, mirror)
    occupied = sum(widths[cell_of[n]] for n in members)
    slots = len(members) + 1
    slack_sites = max(0, (core_width - occupied) // SITE)
    base, extra = divmod(slack_sites, slots)

    x = 0
    idx = 0
    since_tap = 0
    placed_filler = 0
    for i in range(slots):
        gap = (base + (1 if i < extra else 0)) * SITE
        end = x + gap
        while True:
            remaining = end - x
            if since_tap >= TAP_SPACING and remaining >= widths[TAP]:
                name, cell, w = f"__tap_{row}_{idx}", TAP, widths[TAP]
                since_tap = 0
            elif remaining >= widths[DECAP] and placed_filler < filler_budget:
                name, cell, w = f"__decap_{row}_{idx}", DECAP, widths[DECAP]
                since_tap += widths[DECAP]
            else:
                break
            placements[name] = Placement(x, y, mirror)
            cells[name] = cell
            x += w
            idx += 1
            placed_filler += 1
        x = end
        if i < len(members):
            n = members[i]
            placements[n] = Placement(x, y, mirror)
            w = widths[cell_of[n]]
            x += w
            since_tap += w


def place(
    nl,
    spec: LayoutSpec,
    reference: Path,
    spread: float = 1.0,
) -> PlacementResult:
    widths = measure_widths(reference)
    cell_of = {i.name: i.cell for i in nl.instances}
    names = [i.name for i in nl.instances]
    order = _ordered_instances(nl, names, spec)

    # layout-guide.md §7.2 step 1 and §9's `fill`: the die holds `total_width`
    # of cells at `fill` utilisation, with the requested width/height ratio.
    #   core_width  = sqrt(aspect * total_width * row_height / fill)
    #   rows        = total_width / (fill * core_width)
    # which together give core_width / (rows * row_height) == aspect.
    #
    # `spread` thins the rows without touching `core_width`, which is the one
    # way of adding die area that actually helps the router. Track supply is
    # the core's height divided by the met4 pitch; track demand per net is the
    # x-span of its own pins. Lowering `fill` grows both -- a wider core means
    # wider nets -- and empirically does not converge until the die is
    # ridiculous. Holding the width and adding rows grows supply alone. The
    # router asks for this via `build()`'s retry loop, and the spread that
    # succeeded is reported so an author can bake it into the spec.
    total_width = sum(widths[cell_of[n]] for n in order)
    core_width = _round_up(
        math.sqrt(spec.aspect * total_width * ROW_HEIGHT / spec.fill), SITE
    )
    widest = max((widths[cell_of[n]] for n in order), default=SITE)
    capacity = max(_round_up(core_width * spec.fill / spread, SITE), widest)

    rows = _assign_rows(order, widths, cell_of, capacity, spec)

    placements: dict[str, Placement] = {}
    cells: dict[str, str] = dict(cell_of)
    per_row = max(1, math.ceil(FILLER_BUDGET * len(order) / max(1, len(rows))))
    for row, members in enumerate(rows):
        _lay_out_row(
            members, row, widths, cell_of, core_width, placements, cells, per_row
        )

    return PlacementResult(placements, cells, core_width, len(rows))