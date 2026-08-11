"""
A pin is a name plus a point on a routing layer.
We never need the pin polygon itself, because the point lands inside whatever
merged cluster the pin belongs to.

When the cells are only referenced (empty frames, geometry elsewhere) the
pins come from LEF instead
"""

from __future__ import annotations

from dataclasses import dataclass

import klayout.db as db

from .loader import Design

# Fallback for cells the library doesn't describe. sky130_fd_sc_hd is
# consistent enough that the naming convention is fine when there's nothing
# better
OUTPUT_PINS = {"X", "Y", "Q", "Q_N", "SUM", "COUT", "COUT_N", "HI", "LO"}
POWER_PINS = {"VPWR", "VGND", "VPB", "VNB"}


def direction_of(cell_name: str, pin: str) -> str:
    """input / output / power for a pin, from Liberty where the cell is known"""
    from .functions import base_name
    from .liberty import library

    if pin in POWER_PINS:
        return "power"
    cell = library().get(base_name(cell_name))
    if cell is not None and (cell.inputs or cell.outputs):
        return cell.direction(pin)
    return "output" if pin in OUTPUT_PINS else "input"


@dataclass(frozen=True)
class Pin:
    name: str
    layer: str  # routing layer name, e.g. "li1"
    point: db.Point  # in cell local coordinates

    @property
    def is_power(self) -> bool:
        return self.name in POWER_PINS


class PinOracle:
    """cell type -> pins, resolved once and cached in memory"""

    def __init__(self, design: Design, macros: dict | None = None):
        self.design = design
        self.macros = macros or {}
        self._cache: dict[str, list[Pin]] = {}
        self.source: dict[str, str] = {}  # cell -> "gds" | "lef"

    def pins(self, cell_name: str) -> list[Pin]:
        if cell_name not in self._cache:
            found = self._read(cell_name)
            if found:
                self.source[cell_name] = "gds"
            else:
                found = self._from_lef(cell_name)
                if found:
                    self.source[cell_name] = "lef"
            self._cache[cell_name] = found
        return self._cache[cell_name]

    def signal_pins(self, cell_name: str) -> list[Pin]:
        return [p for p in self.pins(cell_name) if not p.is_power]

    def rects(self, cell_name: str) -> list[tuple[str, db.Box]]:
        """LEF pin rectangles for a cell, in cell-local coordinates

        Empty for GDS-sourced cells
        """
        self.pins(cell_name)
        if self.source.get(cell_name) != "lef":
            return []
        layers = {rl.name for rl in self.design.tech.routing}
        macro = self.macros[cell_name]
        return [
            (rect.layer, db.Box(rect.left, rect.bottom, rect.right, rect.top))
            for pin in macro.pins.values()
            for rect in pin.rects
            if rect.layer in layers
        ]

    def _from_lef(self, cell_name: str) -> list[Pin]:
        macro = self.macros.get(cell_name)
        if macro is None:
            return []
        layers = {rl.name for rl in self.design.tech.routing}
        pins = []
        for pin in macro.pins.values():
            for rect in pin.rects:
                if rect.layer not in layers:
                    continue
                x, y = rect.center
                pins.append(Pin(pin.name, rect.layer, db.Point(x, y)))
        return pins

    def _read(self, cell_name: str) -> list[Pin]:
        cell = self.design.layout.cell(cell_name)
        if cell is None:
            return []
        pins: list[Pin] = []
        for rl in self.design.tech.routing:
            idx = self.design.index_of(rl.pin)
            if idx is None:
                continue
            for shape in cell.shapes(idx).each():
                if not shape.is_text():
                    continue
                text = shape.text
                pins.append(Pin(text.string, rl.name, db.Point(text.x, text.y)))
        return pins


def bond_pins(design: Design, conn, oracle: PinOracle) -> int:
    """Union the clusters belonging to one LEF pin. Returns how many were bonded

    A pin's port may hold several rectangles. In the real cell they are one piece
    of metal joined inside the cell. In an abstract they are disjoint boxes with
    nothing between them. LEF says they are one electrical node
    """
    bonded = 0
    for cell_name, trans in design.instances():
        if oracle.source.get(cell_name) != "lef":
            continue
        by_pin: dict[str, list[int]] = {}
        for pin in oracle.pins(cell_name):
            cluster = conn.cluster_at(pin.layer, trans * pin.point)
            if cluster is not None:
                by_pin.setdefault(pin.name, []).append(cluster)
        for clusters in by_pin.values():
            for other in clusters[1:]:
                conn.uf.union(clusters[0], other)
                bonded += 1
    return bonded


def abstract_shapes(design: Design, oracle: PinOracle) -> dict[str, list[db.Box]]:
    """Pin rectangles of LEF-sourced cells placed in global coordinates"""
    extra: dict[str, list[db.Box]] = {}
    for cell_name, trans in design.instances():
        for layer, box in oracle.rects(cell_name):
            extra.setdefault(layer, []).append(trans * box)
    return extra
