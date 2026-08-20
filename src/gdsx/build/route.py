"""A router: connects placed instances per docs/game/layout-guide.md §7.3.

The scheme:

    li1 pin -> mcon -> met1 -> via -> met2 -> via2 -> met3 vertical
            -> via3 -> met4 horizontal

with met4 tracks and met3 columns allocated by interval colouring so that a
resource is reused by a different net only when the two are genuinely
disjoint.

**Routing lives on met3 and met4, one pair above where §7.3 first put it.**
The original scheme risered on met2 and tracked on met3. That fails on every
design in the pack, for a reason worth recording. A pin's escape lift runs up
its own column, so no other net's riser may cross that column inside the
pin's row -- which, with risers on met2, meant reserving a met2 band across
each pin's whole row. Those bands are the entire row area, so a net whose
pins straddle several rows could not find any column that crossed them all,
and no amount of extra die area helped: more area means more rows, and every
row is another band to cross. Moving the risers to met3 removes the
conflict at the source. met2 is no longer a routing layer at all -- it
appears only as a via pad inside each pin's own access stack -- so there is
nothing to reserve, and met3/met4 start out completely empty because the
standard cells use li1 and met1 only.

Two further properties matter and are worth stating, because getting either
wrong produces a layout that extracts to *almost* the right netlist:

**Tracks are distributed across the die, not funnelled into one channel.**
A net's horizontal track is placed on the met4 grid line nearest its own
pins, which may be in the middle of a row. That keeps every met3 riser short
(usually under a row or two), which in turn is what makes met3 columns
reusable at all. A router that puts every track in one channel above the
rows makes every vertical span "from its row up to the channel", so every
vertical overlaps every other in y, no column is ever reusable, and the
allocator is forced into long horizontal jogs that cross wires nobody
checked them against.

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

from dataclasses import dataclass, field

from ..config import TechConfig
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
VIA2 = (69, 44)
MET3 = (70, 20)
VIA3 = (70, 44)
MET4 = (71, 20)
MET4_PIN = (71, 5)

STUB = 85  # nm, half-width of a wire and of an mcon/via/via2 square --
# measured from VIA_L1M1_PR_MR etc in samples/puzzle.gds: (-85,-85;85,85)
RAIL_HALF_HEIGHT = 240  # nm, half-thickness of a met1 power rail: the rails
# drawn inside the cells themselves span y-240..y+240 about each row boundary
SPACING = 140  # nm, minimum gap between two different nets on one layer
MET3_PITCH = 340  # nm, met3 column spacing -- 2*STUB wire plus SPACING margin
MET4_PITCH = 340  # nm, met4 track spacing

COLUMN_SEARCH = 48  # met3 columns to try either side of a pin before failing
TRACK_SEARCH = 400  # met4 grid lines to try either side of a net's midpoint
TRACK_TRIES = 24  # of those, how many to route the whole net on before failing
STRAP_COLUMNS = (2, 4)  # met2 columns past the core edge for VPWR/VGND straps
RAIL_MARGIN = 6 * MET3_PITCH  # how far past the core the met1 rails run

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
# result


@dataclass
class RouteResult:
    boundaries: list[tuple[int, int, list[Point]]] = field(default_factory=list)
    texts: list[tuple[int, int, str, int, int]] = field(default_factory=list)
    # diagnostics, for the build report -- see layout-guide.md §10 point 9
    jogged_pins: int = 0
    max_column_offset: int = 0
    max_track_offset: int = 0
    tracks_used: int = 0


class RouteError(RuntimeError):
    """No legal track or column was available. Widen the core or add tracks
    (layout-guide.md §7.3: "area is free").
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

    @property
    def met1(self) -> _Occupancy:
        return self._occ(MET1)

    @property
    def met2(self) -> _Occupancy:
        return self._occ(MET2)

    @property
    def met3(self) -> _Occupancy:
        return self._occ(MET3)

    @property
    def met4(self) -> _Occupancy:
        return self._occ(MET4)

    @property
    def via2(self) -> _Occupancy:
        return self._occ(VIA2)

    @property
    def via3(self) -> _Occupancy:
        return self._occ(VIA3)

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
            "VPWR": self.core_width + STRAP_COLUMNS[0] * MET3_PITCH,
            "VGND": self.core_width + STRAP_COLUMNS[1] * MET3_PITCH,
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

        for net, x in strap_x.items():
            if not ys[net]:
                continue
            self._emit(MET2, _vwire(x, min(ys[net]), max(ys[net])), net)
            # one label per power net; the strap has made all its rails one net
            self.out.texts.append((*MET1_PIN, net, x, ys[net][0]))

    # -- signals ----------------------------------------------------------

    def track_candidates(self, net: str, pins: list[Point]):
        """met4 grid lines this net's horizontal track could sit on, best
        first: outward from the line nearest the midpoint of its own pins, so
        that the risers reaching it stay short.

        A candidate is offered when the line is clear over the net's pin x
        range. That is necessary but not sufficient -- the finished track
        also has to span the columns the pins actually get, which is not
        known yet -- so the caller routes the net on it and rolls back if it
        does not work out.
        """
        x0 = min(p.x for p in pins)
        x1 = max(p.x for p in pins)
        mid = (min(p.y for p in pins) + max(p.y for p in pins)) // 2
        for t in _outward(round(mid / MET4_PITCH), TRACK_SEARCH):
            if t < 0:
                continue
            y = t * MET4_PITCH
            if self.met4.free((x0, y - STUB, x1, y + STUB), net):
                yield y

    def escape_tracks(self, pt: Point) -> list[int]:
        """met4 grid lines inside the pin's own row, nearest the pin first.

        A pin that cannot rise in its own column has to move sideways, and
        the layer it moves on matters. Horizontally on met3 does not work:
        met3 carries the risers, so a sideways run at the pin's own height
        crosses every riser passing through that row. met4 carries only
        finished tracks, over a grid eight lines deep per row, so the great
        majority of its lines are empty.
        """
        row = pt.y // ROW_HEIGHT
        lines = [
            row * ROW_HEIGHT + k * MET4_PITCH
            for k in range(1, ROW_HEIGHT // MET4_PITCH)
        ]
        return sorted(lines, key=lambda y: abs(y - pt.y))

    def column_for(self, net: str, pt: Point, track_y: int) -> int:
        """The x of this pin's met3 riser, and the geometry that reaches it.

        Tried at the pin's own x first, where the riser runs straight from
        the pin's access stack to the net's track and nothing else is
        needed. Failing that, one met3 pitch at a time either side, reached
        by a two-riser detour through a spare met4 line inside the pin's own
        row (`escape_tracks`). The detour is bounded by `COLUMN_SEARCH`
        pitches; if that is not enough this raises rather than reaching
        further, because a long sideways run crosses wires it has no
        business crossing.
        """
        for step in _outward(0, COLUMN_SEARCH):
            x = pt.x + step * MET3_PITCH
            landing = _square(x, track_y)
            if not self.via3.free(_box(landing), net):
                continue

            if step == 0:
                riser = _vwire(x, pt.y, track_y)
                if not self.met3.free(_box(riser), net):
                    continue
                self._emit(MET3, riser, net)
                self._emit(VIA3, landing, net)
                return x

            for jog_y in self.escape_tracks(pt):
                lift = _vwire(pt.x, pt.y, jog_y)
                hop = _hwire(jog_y, pt.x, x)
                riser = _vwire(x, jog_y, track_y)
                at_pin = _square(pt.x, jog_y)
                at_col = _square(x, jog_y)
                if not self.met3.free(_box(lift), net):
                    continue
                if not self.met4.free(_box(hop), net):
                    continue
                if not self.met3.free(_box(riser), net):
                    continue
                if not all(
                    self.via3.free(_box(s), net) for s in (at_pin, at_col)
                ):
                    continue
                self._emit(MET3, lift, net)
                self._emit(VIA3, at_pin, net)
                self._emit(MET4, hop, net)
                self._emit(VIA3, at_col, net)
                self._emit(MET3, riser, net)
                self._emit(VIA3, landing, net)
                self.out.jogged_pins += 1
                self.out.max_column_offset = max(self.out.max_column_offset, abs(step))
                return x
        raise RouteError(f"net {net}: no free met3 column near x={pt.x}")

    def access(self, pins_of: dict[str, list[Point]]) -> None:
        """The `li1 -> mcon -> met1 -> via -> met2 -> via2 -> met3` stack
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
        itself. An earlier revision also reserved the pin's met2 column
        across the full height of its row, to guarantee the pin's escape
        lift; with the risers on met3 that band would block every riser
        crossing the row, which is what made the pack unroutable. The
        squares alone are enough: a riser that would cross this pin's column
        inside this row has to pass through the pin's own y to do it, and
        the square is already there.
        """
        for net in sorted(pins_of):
            for pt in pins_of[net]:
                stack = _square(pt.x, pt.y)
                for layer in (MET1, MET2, MET3):
                    if not self._occ(layer).free(_box(stack), net):
                        raise RouteError(
                            f"net {net}: pin access at {pt} on layer "
                            f"{layer[0]}/{layer[1]} touches another net"
                        )
                self._emit(MCON, stack, net)
                self._emit(MET1, stack, net)
                self._emit(VIA, stack, net)
                self._emit(MET2, stack, net)
                self._emit(VIA2, stack, net)
                self._emit(MET3, stack, net)

    def signal(self, net: str, pins: list[Point], is_port: bool) -> None:
        """Route one net as a chain of local links, not as one wide track.

        Pins are sorted by y and each neighbouring pair is joined
        independently, on a met4 line chosen from between the two of them.
        The chain is connected because every pin shares a link with the next,
        and the whole net is one electrical node.

        This replaces a single met4 track spanning every pin, which does not
        survive contact with a real design. That scheme puts the track at the
        midpoint of the net, so a pin at either extreme needs a met3 riser
        running half the height of the net -- and a riser may not cross
        another net's pin access square, of which there is one per pin per
        column. Across a tall design every column has something in it
        somewhere, so the long risers have nowhere to go, and *thinning* the
        rows makes it worse rather than better: the pins move further apart,
        so the risers get longer. Linking neighbours keeps every riser inside
        the gap between two adjacent pins, which is short no matter how large
        the die is.
        """
        ordered = sorted(pins, key=lambda p: (p.y, p.x))
        anchor: tuple[int, int] | None = None  # (column x, track y) for a label
        for a, b in zip(ordered, ordered[1:]):
            anchor = self.link(net, a, b) or anchor
        if anchor is None:
            # A one-pin net has nothing to join. It is still a real net -- an
            # unconnected output, or a port with a single reader -- and it
            # still needs a label if it is a port, so give it a stub track.
            anchor = self.link(net, ordered[0], ordered[0])
        if is_port and anchor is not None:
            self.out.texts.append((*MET4_PIN, net, anchor[0], anchor[1]))

    def link(self, net: str, a: Point, b: Point) -> tuple[int, int] | None:
        """Join two pins of one net through a met4 line between them."""
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
                if not self.met4.free(_box(track), net):
                    raise RouteError(f"net {net}: track at y={track_y} does not span")
                self._emit(MET4, track, net)
            except RouteError:
                self._rollback(mark)
                continue
            self._commit()
            self.out.tracks_used += 1
            self.out.max_track_offset = max(
                self.out.max_track_offset, abs(track_y - mid) // MET4_PITCH
            )
            return columns[0], track_y
        raise RouteError(
            f"net {net}: no met4 line free between its pins at y={a.y} and y={b.y}"
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

    # Widest-first: a net whose pins straddle many rows has the least freedom
    # in which met3 grid line it can reach, so it picks before the local nets
    # that can settle anywhere. Ties break on name, so the result is
    # deterministic (layout-guide.md §10 point 8).
    order = sorted(
        pins_of,
        key=lambda n: (
            -(max(p.y for p in pins_of[n]) - min(p.y for p in pins_of[n])),
            -len(pins_of[n]),
            n,
        ),
    )
    r.access(pins_of)
    for net in order:
        r.signal(net, pins_of[net], net in nl.ports)

    verify(r.shapes)
    return r.out


def verify(shapes: list[tuple[tuple[int, int], Rect, str]]) -> None:
    """Re-check the finished layout: no two rectangles on one layer, from two
    different nets, may come within `SPACING`.

    This deliberately duplicates the incremental check in `_Occupancy`. The
    incremental one only ever saw the shapes the allocator thought to ask
    about, and the emitted rectangle is not always the one that was tested
    (a met3 track claims a wider x range while it is being chosen than the
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