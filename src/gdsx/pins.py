"""
A pin is a name plus a point on a routing layer. 
We never need the pin polygon itself, because the point lands inside whatever
merged cluster the pin belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass

import klayout.db as db

from .loader import Design

# sky130_fd_sc_hd output pin names. 
# The library is consistent enough, at least for this puzzle
# todo: in order to extend to broader applications, find a better way
OUTPUT_PINS = {"X", "Y", "Q", "Q_N", "SUM", "COUT", "COUT_N", "HI", "LO"}


@dataclass(frozen=True)
class Pin:
    name: str
    layer: str  # routing layer name, e.g. "li1"
    point: db.Point  # in cell local coordinates

    @property
    def is_power(self) -> bool:
        return self.name in {"VPWR", "VGND", "VPB", "VNB"}

    @property
    def direction(self) -> str:
        if self.is_power:
            return "power"
        return "output" if self.name in OUTPUT_PINS else "input"


class PinOracle:
    """cell type -> pins, resolved once and cached in memory"""

    def __init__(self, design: Design):
        self.design = design
        self._cache: dict[str, list[Pin]] = {}

    def pins(self, cell_name: str) -> list[Pin]:
        if cell_name not in self._cache:
            self._cache[cell_name] = self._read(cell_name)
        return self._cache[cell_name]

    def signal_pins(self, cell_name: str) -> list[Pin]:
        return [p for p in self.pins(cell_name) if not p.is_power]

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
