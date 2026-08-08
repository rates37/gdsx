"""Build tiny GDS fixtures whose net partition is known by construction

Borrows the real sky130 cell definitions from the sample GDS, then
hand-draw the routing between named pins
"""

from __future__ import annotations

from pathlib import Path

import klayout.db as db

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample.gds"

LI1, MET1, MCON = (67, 20), (68, 20), (67, 44)
LI1_PIN = (67, 5)
OUTLINE = (81, 4)  # areaid.standardc, the true cell footprint

MCON_SIZE = 170  # dbu
MET1_ENCLOSURE = 60


class Fixture:
    """Place real cells on a row and draw wires between their pins"""

    def __init__(self, top_name: str = "fixture", library: Path = SAMPLE):
        source = db.Layout()
        source.read(str(library))
        self.source = source
        self.layout = db.Layout()
        self.layout.dbu = source.dbu
        self.top = self.layout.create_cell(top_name)
        self._placed: dict[str, tuple[str, db.Trans]] = {}
        self._x = 0

    def _layer(self, ld):
        return self.layout.layer(*ld)

    def _import(self, cell_name: str) -> db.Cell:
        cell = self.layout.cell(cell_name)
        if cell is None:
            cell = self.layout.create_cell(cell_name)
            cell.copy_tree(self.source.cell(cell_name))
        return cell

    def place(self, inst_name: str, cell_name: str, mirror_x: bool = False) -> None:
        """Abut a cell onto the row, left to right. mirror_x flips it in place"""
        cell = self._import(cell_name)
        width = (
            self.source.cell(cell_name)
            .bbox_per_layer(self.source.layer(*OUTLINE))
            .width()
        )
        offset = db.Trans(db.Vector(self._x + (width if mirror_x else 0), 0))
        trans = offset * (db.Trans.M90 if mirror_x else db.Trans.R0)
        self.top.insert(db.CellInstArray(cell.cell_index(), trans))
        self._placed[inst_name] = (cell_name, trans)
        self._x += width

    def pin_points(self, inst_name: str, pin: str) -> list[db.Point]:
        """Every labelled location of a named pin, in global coordinates"""
        cell_name, trans = self._placed[inst_name]
        cell = self.source.cell(cell_name)
        points = [
            trans * db.Point(s.text.x, s.text.y)
            for s in cell.shapes(self.source.layer(*LI1_PIN)).each()
            if s.is_text() and s.text.string == pin
        ]
        if not points:
            raise KeyError(f"{inst_name} has no li1 pin {pin!r}")
        return points

    def pin_point(
        self, inst_name: str, pin: str, near_y: int | None = None
    ) -> db.Point:
        points = self.pin_points(inst_name, pin)
        if near_y is None:
            return points[0]
        return min(points, key=lambda p: abs(p.y - near_y))

    def route(self, *refs: str, y: int | None = None) -> None:
        """Connect pins ("inst/pin") with a met1 wire dropped onto li1

        The wire runs horizontally at `y` with vertical stubs to each pin, which
        is enough for the single-row fixtures we build here
        """
        if y is None:
            y = max(self.pin_point(*ref.split("/")).y for ref in refs)
        points = [self.pin_point(*ref.split("/"), near_y=y) for ref in refs]
        met1 = self._layer(MET1)
        half = MCON_SIZE // 2 + MET1_ENCLOSURE
        xs = [p.x for p in points]
        self.top.shapes(met1).insert(
            db.Box(min(xs) - half, y - half, max(xs) + half, y + half)
        )
        for p in points:
            self.top.shapes(met1).insert(
                db.Box(p.x - half, min(y, p.y) - half, p.x + half, max(y, p.y) + half)
            )
            self.wire_via(p)

    def wire_via(self, p: db.Point) -> None:
        h = MCON_SIZE // 2
        self.top.shapes(self._layer(MCON)).insert(
            db.Box(p.x - h, p.y - h, p.x + h, p.y + h)
        )

    def label(self, text: str, ref: str) -> None:
        p = self.pin_point(*ref.split("/"))
        self.top.shapes(self._layer(LI1_PIN)).insert(db.Text(text, p.x, p.y))

    def save(self, path: Path) -> Path:
        self.layout.write(str(path))
        return path


def two_gates(path: Path) -> Path:
    """nand2 -> nor2, one wire, shared power rails by abutment"""
    fx = Fixture("two_gates")
    fx.place("u1", "sky130_fd_sc_hd__nand2_2")
    fx.place("u2", "sky130_fd_sc_hd__nor2_2")
    fx.route("u1/Y", "u2/A")
    fx.label("out", "u2/Y")
    fx.label("in_a", "u1/A")
    return fx.save(path)


def mirrored(path: Path) -> Path:
    """Same idea  as aobove but the second cell is mirrored(checks pin transforms)"""
    fx = Fixture("mirrored")
    fx.place("u1", "sky130_fd_sc_hd__nand2_2")
    fx.place("u2", "sky130_fd_sc_hd__nor2_2", mirror_x=True)
    fx.route("u1/Y", "u2/B")
    return fx.save(path)


def chain(path: Path, stages: int = 5) -> Path:
    """A chain of nand2s wired output to input"""
    fx = Fixture("chain")
    for i in range(stages):
        fx.place(f"u{i}", "sky130_fd_sc_hd__nand2_2")
    for i in range(stages - 1):
        fx.route(f"u{i}/Y", f"u{i + 1}/A", y=1700)
    fx.label("head", "u0/A")
    fx.label("tail", f"u{stages - 1}/Y")
    return fx.save(path)
