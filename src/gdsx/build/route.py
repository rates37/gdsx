"""A router: connects placed instances on met1-met3, one net at a time.

The scheme:

    li1 pin -> mcon -> met1 -> via -> met2 vertical
            -> via2 -> met3 horizontal

with met3 tracks and met2 columns allocated by interval colouring so that a
resource is reused by a different net only when the two are genuinely
disjoint. Which layer plays which part is declared once, by role, in the
block below the layer table; nothing else in this module names a layer.

**Signal routing stops at met3, and met4/met5 belong to the power grid.**
That split is the one the reference design uses and it is not free real
estate -- a perpendicular power mesh needs two adjacent layers of its own,
so every layer signal takes is one the grid cannot have. Two low layers are
enough here because the pack's designs are small and loosely packed.

A previous revision of this module routed on met3/met4 instead, on the
grounds that risers could not work on met2. That reasoning was wrong, and
the correction is worth recording because it looks convincing. The argument
was: a pin's escape lift runs up its own column, so no other net's riser may
cross that column inside the pin's row; with risers on met2 that means
reserving a met2 band across each pin's whole row; those bands tile the row,
so a net whose pins straddle several rows can find no column crossing them
all. Every step is sound *except* the second. The band is not needed --
`access()` reserves a single square per pin, and a riser that would cross
this pin's column inside this row must pass through the pin's own y to do
it, where the square already is. Once the band went away, met2 became
routable, and the whole pack routes on met2/met3 at the same die sizes it
used on met3/met4.

Three further properties matter and are worth stating, because getting any
of them wrong produces a layout that extracts to *almost* the right netlist:

**Tracks are distributed across the die, not funnelled into one channel.**
A net's horizontal track is placed on a grid line near its own pins, which
may be in the middle of a row. That keeps every riser short (usually under a
row or two), which in turn is what makes riser columns reusable at all. A
router that puts every track in one channel above the rows makes every
vertical span "from its row up to the channel", so every vertical overlaps
every other in y, no column is ever reusable, and the allocator is forced
into long horizontal jogs that cross wires nobody checked them against.

**Grid lines are shared before they are opened.** Given the freedom above, a
run could sit on any line near its own midpoint, and picking a fresh one each
time smears a row's wiring over every line it has. Offering the lines already
carrying a run nearby -- within `TRACK_POOL_REACH`, so the sharing never costs
more riser than it saves -- packs a row into a few visible channels instead.
Two runs that do not overlap in x then cost one line between them rather than
two. That is interval-graph colouring, and it is the difference between a die
that reads as wiring and one that reads as noise. It is an ordering heuristic
only: every candidate is still checked against emitted geometry.

**Allocation is checked against emitted geometry, not argued.** Every
rectangle this module emits is inserted into a per-layer occupancy index
(`_Occupancy`), and a candidate is accepted only if it clears every
rectangle already placed for a *different* net by `SPACING`. There is no
ordering invariant to reason about and no case left implicit: if a candidate
column or track would touch another net, it is rejected and the search moves
on. `verify()` re-checks the finished result the same way, so a short is a
raised exception at build time rather than a merged net at extract time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import TechConfig
from ..functions import async_nets, clock_nets, lookup
from ..geo.types import Point
from ..netlist import Netlist
from .place import ROW_HEIGHT, Placement

# Layer (GDS layer, datatype/texttype) pairs -- see config/sky130.yaml.
# Labels are read from each routing layer's *pin* purpose (`rl.pin` in
# loader.inspect / netlist._top_labels), which is datatype 5, not the
# `label` purpose at 16.
MET1 = (68, 20)
MET1_PIN = (68, 5)
MCON = (67, 44)
VIA = (68, 44)
MET2 = (69, 20)
MET2_PIN = (69, 5)
VIA2 = (69, 44)
MET3 = (70, 20)
MET3_PIN = (70, 5)
VIA3 = (70, 44)
MET4 = (71, 20)
MET5 = (72, 20)
VIA4 = (71, 44)

# --- the signal stack, by role -------------------------------------------
#
# Which physical layer plays which part. Everything below names the *role*,
# never the layer, so moving signal routing up or down the stack is this block
# and nothing else -- and what is left over is what a power grid can use.
#
#   ACCESS   the lift off a cell pin, in order, ending on the riser's layer
#   RISER    vertical: one per pin, from its access stack up to the track
#   LANDING  the via joining a riser to a track
#   TRACK    horizontal: the lines that actually join pins together
#   PORT_LABEL  the pin purpose a port's TEXT goes on -- the top of ACCESS
#
# The rule the choice has to satisfy: RISER must be a layer the standard
# cells do not use, or a pin's escape has to share a layer with the geometry
# it is escaping from. sky130 high-density cells draw on li1 and met1 only.
ACCESS = (MCON, MET1, VIA, MET2)
RISER = MET2
LANDING = VIA2
TRACK = MET3
PORT_LABEL = MET2_PIN
VIA_LAYERS = (MCON, VIA, VIA2, VIA3, VIA4)

# --- the power grid ------------------------------------------------------
#
# Measured from samples/puzzle.gds, which is a real OpenLane result: straps
# 2 um wide in VPWR/VGND pairs 3.7 um apart, the pairs repeating every 30 um,
# vertical on met4 and horizontal on met5, tied at every crossing.
GRID_V = MET4  # vertical straps
GRID_H = MET5  # horizontal straps
GRID_VIA = VIA4  # joins the two
STRAP_WIDTH = 2000
STRAP_PAIR = 3700  # centre-to-centre, VPWR to VGND within a pair
STRAP_PITCH = 30000  # pair to pair

STUB = 85  # nm, half-width of a wire and of an mcon/via/via2 square --
# measured from VIA_L1M1_PR_MR etc in samples/puzzle.gds: (-85,-85;85,85)
RAIL_HALF_HEIGHT = 240  # nm, half-thickness of a met1 power rail: the rails
# drawn inside the cells themselves span y-240..y+240 about each row boundary
SPACING = 140  # nm, minimum gap between two different nets on one layer
RISER_PITCH = 340  # nm, riser column spacing -- 2*STUB wire plus SPACING margin
TRACK_PITCH = 340  # nm, track spacing

COLUMN_SEARCH = 48  # riser columns to try either side of a pin before failing
TRACK_SEARCH = 400  # track grid lines to try either side of a net's midpoint
TRACK_TRIES = 24  # of those, how many to route the whole net on before failing
TRACK_POOL_REACH = ROW_HEIGHT  # how far from its own midpoint a net will reach
# to share a grid line another net has already opened. Unbounded, a tall net
# sees every open line on the die and settles on one an arbitrary distance
# away, which lengthens its risers instead of shortening anyone's.
STRAP_COLUMNS = (2, 4)  # riser columns past the core edge for VPWR/VGND straps
RAIL_MARGIN = 6 * RISER_PITCH  # how far past the core the met1 rails run

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1, inclusive


def _rect(x0: int, y0: int, x1: int, y1: int) -> list[Point]:
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return [Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)]


def _box(pts: list[Point]) -> Rect:
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _square(cx: int, cy: int, half: int = STUB) -> list[Point]:
    return _rect(cx - half, cy - half, cx + half, cy + half)


def _span(a: int, b: int, half: int) -> tuple[int, int]:
    """The inclusive extent of a wire running from `a` to `b`, never shorter
    than the `2*half` square that its end vias need. A zero-length segment
    would otherwise be a degenerate (zero-area) polygon, which a region
    merge silently drops -- and a dropped segment is a via landing on
    nothing, which extraction reports as a dangling pin.
    """
    lo, hi = (a, b) if a <= b else (b, a)
    if hi - lo < 2 * half:
        mid = (lo + hi) // 2
        lo, hi = mid - half, mid + half
    return lo, hi


def _vwire(x: int, y0: int, y1: int, half: int = STUB) -> list[Point]:
    lo, hi = _span(y0, y1, half)
    return _rect(x - half, lo, x + half, hi)


def _hwire(y: int, x0: int, x1: int, half: int = STUB) -> list[Point]:
    lo, hi = _span(x0, x1, half)
    return _rect(lo, y - half, hi, y + half)


def _transform(pt: Point, p: Placement) -> Point:
    y = -pt.y if p.mirror else pt.y
    return Point(pt.x + p.x, y + p.y)


def _outward(centre: int, limit: int):
    """0, +1, -1, +2, -2, ... -- nearest-first candidate offsets."""
    yield centre
    for d in range(1, limit + 1):
        yield centre + d
        yield centre - d


# ---------------------------------------------------------------------------
# occupancy


BUCKET = 4000  # nm, spatial-hash cell size for _Occupancy


class _Occupancy:
    """Rectangles already committed on one layer, indexed by a coarse grid.

    `free(rect, net)` is true when `rect` clears every committed rectangle
    belonging to a *different* net by at least `SPACING`. Same-net overlap is
    always allowed -- that is what a wire joining two of its own pins is.
    """

    def __init__(self, margin: int = SPACING) -> None:
        self.margin = margin
        self._cells: dict[tuple[int, int], list[tuple[Rect, str]]] = {}

    def _keys(self, r: Rect, pad: int = 0):
        x0, y0, x1, y1 = r
        for i in range((x0 - pad) // BUCKET, (x1 + pad) // BUCKET + 1):
            for j in range((y0 - pad) // BUCKET, (y1 + pad) // BUCKET + 1):
                yield (i, j)

    def add(self, r: Rect, net: str, journal: list | None = None) -> None:
        for key in self._keys(r):
            bucket = self._cells.setdefault(key, [])
            bucket.append((r, net))
            if journal is not None:
                journal.append(bucket)

    def free(self, r: Rect, net: str) -> bool:
        m = self.margin
        x0, y0, x1, y1 = r
        for key in self._keys(r, m):
            for other, onet in self._cells.get(key, ()):
                if onet == net:
                    continue
                ox0, oy0, ox1, oy1 = other
                if x1 + m < ox0 or ox1 + m < x0:
                    continue
                if y1 + m < oy0 or oy1 + m < y0:
                    continue
                return False
        return True


# ---------------------------------------------------------------------------
# cell pins


PinTable = dict[str, dict[str, Point]]  # cell name -> pin name -> local li1 point


def build_pin_table(tech: TechConfig, reference) -> PinTable:
    """cell name -> {pin name: one representative li1-local point}

    A pin's drawn shape is often labelled more than once; any one of those
    labels sits on the same merged li1 cluster, so the first is as good as
    any.
    """
    from ..geo import gdsii
    from ..loader import load
    from ..pins import PinOracle

    design = load(reference, tech)
    oracle = PinOracle(design)
    # Structure names, enumerated with the pure-Python reader regardless of
    # which backend `design.layout` actually is -- only PinOracle's shape
    # lookups need the active backend's precision.
    names = gdsii.read_file(reference)._lib.structures
    table: PinTable = {}
    for name in names:
        if not name.startswith("sky130_fd_sc_hd__"):
            continue
        pins: dict[str, Point] = {}
        for p in oracle.pins(name):
            if p.is_power:
                continue
            pins.setdefault(p.name, p.point)
        table[name] = pins
    return table


# ---------------------------------------------------------------------------
# port pins

# How far outside the core's own furniture a pin column sits. The pin's access
# stack is a square of half-width STUB, so at two riser pitches the nearest
# metal it could touch is 595 nm away -- four times SPACING -- on every side:
# the met1 rails start at x=0 and end at core_width + RAIL_MARGIN, the met2
# power straps sit inside that, and the lowest rail spans y=0 +/- RAIL_HALF_HEIGHT.
PORT_MARGIN = 2 * RISER_PITCH
# Bottom pins sit below the lowest power rail rather than beside it, so their
# clearance is in y instead of x.
BOTTOM_PIN_Y = -(RAIL_HALF_HEIGHT + PORT_MARGIN)


def _natural(name: str) -> tuple:
    """Sort key that orders `O[2]` before `O[10]`, so a bus reads in order."""
    return tuple(
        int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)
    )


def _control_nets(nl: Netlist) -> set[str]:
    """The nets that land on a sequential cell's clock or async set/reset pin.

    Read off the cell library rather than matched by name, so a design that
    calls its clock something other than `clk` still gets its pins on the
    right edge.
    """
    nets: set[str] = set()
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None or cell.sequential is None:
            continue
        nets |= clock_nets(cell, inst.connections)
        nets |= async_nets(cell, inst.connections)
    return nets


def _spread(count: int, lo: int, hi: int, pitch: int) -> list[int]:
    """`count` evenly pitched positions strictly inside `[lo, hi]`."""
    step = (hi - lo) / (count + 1)
    return [round((lo + step * (i + 1)) / pitch) * pitch for i in range(count)]


def port_pins(nl: Netlist, core_width: int, n_rows: int) -> dict[str, Point]:
    """One die-edge pin per top-level port: data inputs on the left edge,
    outputs on the right edge, clock and async reset along the bottom.

    A port is only a port because a label sits on its net, so where that label
    goes is the whole of a port's physical identity. Dropping it wherever the
    net happened to be routed makes every port an interior point and the die
    unreadable as a chip: you cannot tell by looking which side data enters
    from. Giving each port a real pin -- its own access stack, on its own
    edge, evenly pitched along it, in name order -- costs one extra track link
    per port and makes the die view say what the interface is.

    Inputs go on the left, outputs on the right, and clock/reset control
    signals along the bottom. Within a side the first name is placed at the
    top (or, along the bottom, at the left), so a bus runs in index order the
    way a pin list does.
    """
    control = _control_nets(nl)
    left: list[str] = []
    right: list[str] = []
    bottom: list[str] = []
    for net, direction in nl.ports.items():
        if net in nl.power_nets:
            continue
        if net in control:
            bottom.append(net)
        elif direction == "input":
            left.append(net)
        else:
            right.append(net)

    pins: dict[str, Point] = {}
    height = n_rows * ROW_HEIGHT
    for side, x in (
        (left, -PORT_MARGIN),
        (right, core_width + RAIL_MARGIN + PORT_MARGIN),
    ):
        ys = _spread(len(side), 0, height, TRACK_PITCH)
        for net, y in zip(sorted(side, key=_natural), reversed(ys)):
            pins[net] = Point(x, y)
    xs = _spread(len(bottom), 0, core_width, RISER_PITCH)
    for net, x in zip(sorted(bottom, key=_natural), xs):
        pins[net] = Point(x, BOTTOM_PIN_Y)
    return pins


# ---------------------------------------------------------------------------
# result


@dataclass
class RouteResult:
    boundaries: list[tuple[int, int, list[Point]]] = field(default_factory=list)
    texts: list[tuple[int, int, str, int, int]] = field(default_factory=list)
    # diagnostics, for the build report
    jogged_pins: int = 0
    max_column_offset: int = 0
    max_track_offset: int = 0
    tracks_used: int = 0
    grid_drops: int = 0  # mesh-to-rail via stacks actually placed


class RouteError(RuntimeError):
    """No legal track or column was available. Widen the core or add tracks
    -- area is free.
    """


# ---------------------------------------------------------------------------
# the router


@dataclass
class _Router:
    core_width: int
    n_rows: int

    def __post_init__(self) -> None:
        self.out = RouteResult()
        # One index per layer. Every rectangle this router emits goes into
        # the index for its layer, including the via layers -- two pins of
        # different nets in adjacent cells are close enough that their mcon
        # squares are worth checking, not assuming.
        self.occ: dict[tuple[int, int], _Occupancy] = {}
        self.shapes: list[tuple[tuple[int, int], Rect, str]] = []
        self._journal: list | None = None
        self.rail_ys: dict[str, list[int]] = {}
        # row index -> track grid lines already carrying a run in that row,
        # the pool `track_candidates` draws from before opening a new line
        self._open_tracks: dict[int, set[int]] = {}

    @property
    def met1(self) -> _Occupancy:
        return self._occ(MET1)

    @property
    def met2(self) -> _Occupancy:
        return self._occ(MET2)

    @property
    def riser(self) -> _Occupancy:
        return self._occ(RISER)

    @property
    def track(self) -> _Occupancy:
        return self._occ(TRACK)

    @property
    def via2(self) -> _Occupancy:
        return self._occ(VIA2)

    @property
    def landing(self) -> _Occupancy:
        return self._occ(LANDING)

    def _occ(self, layer: tuple[int, int]) -> _Occupancy:
        return self.occ.setdefault(layer, _Occupancy())

    # -- emission ---------------------------------------------------------

    def _emit(self, layer, pts: list[Point], net: str) -> None:
        self.out.boundaries.append((*layer, pts))
        box = _box(pts)
        self._occ(layer).add(box, net, self._journal)
        self.shapes.append((layer, box, net))

    # -- undo -------------------------------------------------------------
    #
    # A net is routed as one transaction. Which met3 track it should sit on
    # cannot be decided in advance of its columns -- the track has to span
    # whatever columns the pins end up in, and a pin's column is only known
    # once the search has run -- and reserving a track wide enough to cover
    # every column the search *might* pick claims most of a grid line for
    # every net, which exhausts met3 within twenty nets. So: pick a track,
    # route the whole net on it, and if the finished track does not fit,
    # undo everything and try the next one.

    def _begin(self) -> tuple[int, int, list]:
        mark = (len(self.out.boundaries), len(self.shapes), [])
        self._journal = mark[2]
        return mark

    def _rollback(self, mark: tuple[int, int, list]) -> None:
        n_boundaries, n_shapes, journal = mark
        for bucket in reversed(journal):
            bucket.pop()
        del self.out.boundaries[n_boundaries:]
        del self.shapes[n_shapes:]
        self._journal = None

    def _commit(self) -> None:
        self._journal = None

    # -- power ------------------------------------------------------------

    def rail_net(self, k: int) -> str:
        return "VGND" if k % 2 == 0 else "VPWR"

    def power(self) -> None:
        """met1 rails across every row boundary, tied together per net by a
        met2 strap just past the right edge of the core.

        The straps are not optional. Rows alternate orientation so that
        neighbours share a rail, which means all VPWR rails sit at odd
        multiples of the row height and all VGND rails at even ones -- so
        without a vertical tie each rail is its own net and extraction sees
        `n_rows + 1` power nets rather than two.
        """
        strap_x = {
            "VPWR": self.core_width + STRAP_COLUMNS[0] * RISER_PITCH,
            "VGND": self.core_width + STRAP_COLUMNS[1] * RISER_PITCH,
        }
        rail_x1 = self.core_width + RAIL_MARGIN
        ys: dict[str, list[int]] = {"VPWR": [], "VGND": []}

        for k in range(self.n_rows + 1):
            y = k * ROW_HEIGHT
            net = self.rail_net(k)
            ys[net].append(y)
            self._emit(
                MET1,
                _rect(0, y - RAIL_HALF_HEIGHT, rail_x1, y + RAIL_HALF_HEIGHT),
                net,
            )
            self._emit(VIA, _square(strap_x[net], y), net)

        self.rail_ys = ys
        for net, x in strap_x.items():
            if not ys[net]:
                continue
            self._emit(MET2, _vwire(x, min(ys[net]), max(ys[net])), net)
            # one label per power net; the strap has made all its rails one net
            self.out.texts.append((*MET1_PIN, net, x, ys[net][0]))

    def _strap_positions(self, extent: int) -> list[tuple[str, int]]:
        """Strap centres across `extent`, as (net, centre) in ascending order.

        Pairs are laid on `STRAP_PITCH` and centred in the extent, so a die
        that is not a whole number of pitches wide gets an even margin either
        side rather than a wide gap at one edge. A die too small for a whole
        pair still gets one, because a grid with no straps in one direction is
        not a grid.
        """
        span = STRAP_PAIR + STRAP_WIDTH
        pairs = max(1, int((extent - span) // STRAP_PITCH) + 1)
        used = (pairs - 1) * STRAP_PITCH + span
        start = (extent - used) // 2 + STRAP_WIDTH // 2
        out: list[tuple[str, int]] = []
        for k in range(pairs):
            base = start + k * STRAP_PITCH
            out.append(("VPWR", base))
            out.append(("VGND", base + STRAP_PAIR))
        return out

    def grid(self, taken: dict[tuple[int, int], str] | None = None) -> None:
        """The power mesh: vertical straps on one layer, horizontal on the
        next, tied at every crossing, and dropped to the met1 rails.

        This is what a die looks like from above -- the reference design in
        `samples/puzzle.gds` spends more metal on its mesh than on its signals
        -- and it is the whole of the top of the 3D stack. Its dimensions are
        measured from that design rather than invented.

        The mesh is a genuine electrical structure, not decoration. Each
        vertical strap drops to every met1 rail carrying its own net, through
        a via stack that crosses the two signal layers. Those crossings are
        the only place the mesh costs the signal router anything, so they are
        emitted as single vias rather than strap-width slabs: one blocked
        riser column and one blocked track line each, against a strap-width
        drop that would block six of both.

        `taken` maps an already-reserved pin square's centre to its net, and a
        drop landing on one is skipped. A pin cannot move and the mesh can:
        the strap above it is unbroken either way, and one rail out of the
        seventy-odd a strap crosses makes no electrical difference.
        """
        taken = taken or {}
        height = self.n_rows * ROW_HEIGHT
        verticals = self._strap_positions(self.core_width)
        horizontals = self._strap_positions(height)
        half = STRAP_WIDTH // 2

        for net, x in verticals:
            self._emit(GRID_V, _rect(x - half, 0, x + half, height), net)
        for net, y in horizontals:
            self._emit(GRID_H, _rect(0, y - half, self.core_width, y + half), net)
        for vnet, x in verticals:
            for hnet, y in horizontals:
                if vnet == hnet:
                    self._emit(GRID_VIA, _square(x, y, half), vnet)

        for net, x in verticals:
            for y in self.rail_ys.get(net, ()):
                if taken.get((x, y), net) != net:
                    continue
                for layer in (VIA3, TRACK, LANDING, RISER, VIA):
                    self._emit(layer, _square(x, y), net)
                self.out.grid_drops += 1

    # -- signals ----------------------------------------------------------

    def track_candidates(self, net: str, pins: list[Point]):
        """Track grid lines this net's horizontal run could sit on, best
        first: outward from the line nearest the midpoint of its own pins, so
        that the risers reaching it stay short.

        A candidate is offered when the line is clear over the net's pin x
        range. That is necessary but not sufficient -- the finished track
        also has to span the columns the pins actually get, which is not
        known yet -- so the caller routes the net on it and rolls back if it
        does not work out.

        Lines already carrying a run in this net's own rows are offered
        first. This is interval-graph colouring, and it is what stops the
        die looking like noise: two runs that do not overlap
        in x cost one grid line between them rather than two, so a row's
        wiring settles into a few shared channels instead of smearing across
        all eight lines the row has. Correctness does not depend on it --
        every candidate is still checked against emitted geometry -- so this
        is an ordering heuristic and nothing more.
        """
        x0 = min(p.x for p in pins)
        x1 = max(p.x for p in pins)
        lo = min(p.y for p in pins)
        hi = max(p.y for p in pins)
        mid = (lo + hi) // 2

        open_here: set[int] = set()
        for row in range(lo // ROW_HEIGHT, hi // ROW_HEIGHT + 1):
            open_here |= self._open_tracks.get(row, set())
        open_here = {y for y in open_here if abs(y - mid) <= TRACK_POOL_REACH}

        seen: set[int] = set()
        for y in sorted(open_here, key=lambda y: abs(y - mid)):
            seen.add(y)
            if self.track.free((x0, y - STUB, x1, y + STUB), net):
                yield y
        for t in _outward(round(mid / TRACK_PITCH), TRACK_SEARCH):
            if t < 0:
                continue
            y = t * TRACK_PITCH
            if y in seen:
                continue
            if self.track.free((x0, y - STUB, x1, y + STUB), net):
                yield y

    def escape_tracks(self, pt: Point) -> list[int]:
        """Track grid lines inside the pin's own row, nearest the pin first.

        A pin that cannot rise in its own column has to move sideways, and
        the layer it moves on matters. Moving sideways on the riser layer
        does not work: it carries every other pin's riser, so a run at the
        pin's own height crosses all of them passing through that row. The
        track layer carries only finished runs, over a grid eight lines deep
        per row, so the great majority of its lines are empty.
        """
        row = pt.y // ROW_HEIGHT
        lines = [
            row * ROW_HEIGHT + k * TRACK_PITCH
            for k in range(1, ROW_HEIGHT // TRACK_PITCH)
        ]
        return sorted(lines, key=lambda y: abs(y - pt.y))

    def column_for(self, net: str, pt: Point, track_y: int) -> int:
        """The x of this pin's riser, and the geometry that reaches it.

        Tried at the pin's own x first, where the riser runs straight from
        the pin's access stack to the net's track and nothing else is
        needed. Failing that, one riser pitch at a time either side, reached
        by a two-riser detour through a spare track line inside the pin's own
        row (`escape_tracks`). The detour is bounded by `COLUMN_SEARCH`
        pitches; if that is not enough this raises rather than reaching
        further, because a long sideways run crosses wires it has no
        business crossing.
        """
        for step in _outward(0, COLUMN_SEARCH):
            x = pt.x + step * RISER_PITCH
            landing = _square(x, track_y)
            if not self.landing.free(_box(landing), net):
                continue

            if step == 0:
                riser = _vwire(x, pt.y, track_y)
                if not self.riser.free(_box(riser), net):
                    continue
                self._emit(RISER, riser, net)
                self._emit(LANDING, landing, net)
                return x

            for jog_y in self.escape_tracks(pt):
                lift = _vwire(pt.x, pt.y, jog_y)
                hop = _hwire(jog_y, pt.x, x)
                riser = _vwire(x, jog_y, track_y)
                at_pin = _square(pt.x, jog_y)
                at_col = _square(x, jog_y)
                if not self.riser.free(_box(lift), net):
                    continue
                if not self.track.free(_box(hop), net):
                    continue
                if not self.riser.free(_box(riser), net):
                    continue
                if not all(
                    self.landing.free(_box(s), net) for s in (at_pin, at_col)
                ):
                    continue
                self._emit(RISER, lift, net)
                self._emit(LANDING, at_pin, net)
                self._emit(TRACK, hop, net)
                self._emit(LANDING, at_col, net)
                self._emit(RISER, riser, net)
                self._emit(LANDING, landing, net)
                self.out.jogged_pins += 1
                self.out.max_column_offset = max(self.out.max_column_offset, abs(step))
                return x
        raise RouteError(f"net {net}: no free riser column near x={pt.x}")

    def access(self, pins_of: dict[str, list[Point]]) -> None:
        """The `li1 -> mcon -> met1 -> via -> met2` access stack
        over every signal pin in the design, emitted before any net is
        routed.

        Doing all of them up front is what makes the column search below
        sound. A pin's own x is the column its riser wants, and it is also
        the one place no *other* net's riser may ever pass -- it would land
        on top of the pin. Routing net by net without this pass lets an
        early net take a column straight through a later net's pin, at which
        point that pin has no legal column at all: every candidate needs a
        jog, and every jog starts inside the riser sitting on it.

        The stack is a single square per pin and reserves nothing beyond
        itself. An earlier revision also reserved the pin's riser column
        across the full height of its row, to guarantee the pin's escape
        lift; that band blocks every riser crossing the row, which is what
        made the pack look unroutable on met2 (see the module docstring). The
        squares alone are enough: a riser that would cross this pin's column
        inside this row has to pass through the pin's own y to do it, and
        the square is already there.
        """
        for net in sorted(pins_of):
            for pt in pins_of[net]:
                stack = _square(pt.x, pt.y)
                for layer in ACCESS:
                    if layer in VIA_LAYERS:
                        continue  # a via is only ever inside its own stack
                    if not self._occ(layer).free(_box(stack), net):
                        raise RouteError(
                            f"net {net}: pin access at {pt} on layer "
                            f"{layer[0]}/{layer[1]} touches another net"
                        )
                for layer in ACCESS:
                    self._emit(layer, stack, net)

    def signal(self, net: str, pins: list[Point], port_pin: Point | None) -> None:
        """Route one net as a chain of local links, not as one wide track.

        Pins are sorted by y and each neighbouring pair is joined
        independently, on a track line chosen from between the two of them.
        The chain is connected because every pin shares a link with the next,
        and the whole net is one electrical node.

        This replaces a single track spanning every pin, which does not
        survive contact with a real design. That scheme puts the track at the
        midpoint of the net, so a pin at either extreme needs a riser
        running half the height of the net -- and a riser may not cross
        another net's pin access square, of which there is one per pin per
        column. Across a tall design every column has something in it
        somewhere, so the long risers have nowhere to go, and *thinning* the
        rows makes it worse rather than better: the pins move further apart,
        so the risers get longer. Linking neighbours keeps every riser inside
        the gap between two adjacent pins, which is short no matter how large
        the die is.

        `port_pin` is the net's die-edge pin, where it has one. It is joined
        to whichever of the net's own pins is closest in y -- not folded into
        the chain by y order, which would put a long link on either side of it
        instead of one -- and it carries the net's label, because that pin is
        what makes the net a port.
        """
        ordered = sorted(pins, key=lambda p: (p.y, p.x))
        anchor: tuple[int, int] | None = None
        for a, b in zip(ordered, ordered[1:]):
            anchor = self.link(net, a, b) or anchor
        if anchor is None and port_pin is None:
            # A one-pin net with no edge pin has nothing to join. It is still
            # a real net -- an unconnected output -- and a zero-length stub
            # keeps it on the grid rather than leaving it as a bare via stack.
            self.link(net, ordered[0], ordered[0])
        if port_pin is not None:
            nearest = min(ordered, key=lambda p: (abs(p.y - port_pin.y), p.y, p.x))
            self.link(net, port_pin, nearest)
            self.out.texts.append((*PORT_LABEL, net, port_pin.x, port_pin.y))

    def link(self, net: str, a: Point, b: Point) -> tuple[int, int] | None:
        """Join two pins of one net through a track line between them."""
        mid = (a.y + b.y) // 2
        pair = [a] if a is b or (a.x, a.y) == (b.x, b.y) else [a, b]
        tried = 0
        for track_y in self.track_candidates(net, pair):
            tried += 1
            if tried > TRACK_TRIES:
                break
            mark = self._begin()
            try:
                columns = [self.column_for(net, pt, track_y) for pt in pair]
                track = _hwire(track_y, min(columns), max(columns))
                if not self.track.free(_box(track), net):
                    raise RouteError(f"net {net}: track at y={track_y} does not span")
                self._emit(TRACK, track, net)
            except RouteError:
                self._rollback(mark)
                continue
            self._commit()
            lo, hi = min(p.y for p in pair), max(p.y for p in pair)
            for row in range(
                min(lo, track_y) // ROW_HEIGHT, max(hi, track_y) // ROW_HEIGHT + 1
            ):
                self._open_tracks.setdefault(row, set()).add(track_y)
            self.out.tracks_used += 1
            self.out.max_track_offset = max(
                self.out.max_track_offset, abs(track_y - mid) // TRACK_PITCH
            )
            return columns[0], track_y
        raise RouteError(
            f"net {net}: no track line free between its pins at y={a.y} and y={b.y}"
        )


def route(
    nl: Netlist,
    placements: dict[str, Placement],
    cell_of: dict[str, str],
    pin_table: PinTable,
    core_width: int,
    n_rows: int,
) -> RouteResult:
    r = _Router(core_width, n_rows)
    r.power()

    pins_of: dict[str, list[Point]] = {}
    for net in sorted(nl.nets):
        if net in nl.power_nets:
            continue  # carried by the rails, which the cells' own pins sit on
        pts = []
        for ref in nl.nets[net]:
            inst, pin = ref.split("/")
            pts.append(_transform(pin_table[cell_of[inst]][pin], placements[inst]))
        pins_of[net] = pts

    ports = port_pins(nl, core_width, n_rows)

    # Ports first, then widest-first: a net whose pins straddle many rows has
    # the least freedom in which riser column it can reach, so it picks
    # before the local nets that can settle anywhere -- and a port's link runs
    # from the die edge to somewhere inside the core, which is wider still and
    # wants a track line while the layer is empty. Ties break on name, so the
    # result is deterministic.
    order = sorted(
        pins_of,
        key=lambda n: (
            n not in ports,
            -(max(p.y for p in pins_of[n]) - min(p.y for p in pins_of[n])),
            -len(pins_of[n]),
            n,
        ),
    )
    access_pins = {
        net: pts + ([ports[net]] if net in ports else [])
        for net, pts in pins_of.items()
    }
    r.access(access_pins)
    # The mesh goes down after the pin squares are reserved, so a drop that
    # would land on one can be skipped, and before any net is routed, so the
    # drops it does place are obstructions the signal router can see.
    r.grid({(pt.x, pt.y): net for net, pts in access_pins.items() for pt in pts})
    for net in order:
        r.signal(net, pins_of[net], ports.get(net))

    verify(r.shapes)
    return r.out


def verify(shapes: list[tuple[tuple[int, int], Rect, str]]) -> None:
    """Re-check the finished layout: no two rectangles on one layer, from two
    different nets, may come within `SPACING`.

    This deliberately duplicates the incremental check in `_Occupancy`. The
    incremental one only ever saw the shapes the allocator thought to ask
    about, and the emitted rectangle is not always the one that was tested
    (a track claims a wider x range while it is being chosen than the
    wire finally drawn on it). This pass runs over what was actually
    emitted, which is what extraction will see. A short is the one failure
    mode that extraction reports as a plausible-looking netlist rather than
    an error, so it is worth paying for twice.
    """
    by_layer: dict[tuple[int, int], _Occupancy] = {}
    for layer, box, net in shapes:
        occ = by_layer.setdefault(layer, _Occupancy())
        if not occ.free(box, net):
            raise RouteError(
                f"short on layer {layer[0]}/{layer[1]}: net {net}'s shape {box} "
                f"comes within {SPACING} nm of another net"
            )
        occ.add(box, net)