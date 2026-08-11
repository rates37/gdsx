"""Minimal LEF reader"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Rect:
    layer: str
    left: int
    bottom: int
    right: int
    top: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.bottom + self.top) // 2


@dataclass
class LefPin:
    name: str
    direction: str  # input / output / inout / power
    rects: list[Rect] = field(default_factory=list)


@dataclass
class Macro:
    name: str
    pins: dict[str, LefPin] = field(default_factory=dict)
    width: int = 0
    height: int = 0


def _dbu(value: str, scale: float) -> int:
    return int(round(float(value) * scale))


def parse(text: str, dbu: float = 0.001) -> dict[str, Macro]:
    """Parse LEF source into macros. `dbu` is the layout's database unit in um"""
    scale = 1.0 / dbu
    macros: dict[str, Macro] = {}
    macro: Macro | None = None
    pin: LefPin | None = None
    layer: str | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip(";").strip()
        if not line:
            continue
        words = line.split()
        keyword = words[0].upper()

        if keyword == "MACRO":
            macro = Macro(words[1])
            macros[macro.name] = macro
        elif macro is None:
            continue  # header, or a section we do not care about
        elif keyword == "SIZE" and len(words) >= 4:
            # SIZE <w> BY <h>
            macro.width = _dbu(words[1], scale)
            macro.height = _dbu(words[3], scale)
        elif keyword == "PIN":
            pin = LefPin(words[1], "inout")
            macro.pins[pin.name] = pin
        elif (
            keyword == "END"
            and len(words) > 1
            and pin is not None
            and words[1] == pin.name
        ):
            pin, layer = None, None
        elif keyword == "END" and len(words) > 1 and words[1] == macro.name:
            macro, pin, layer = None, None, None
        elif pin is None:
            continue  # OBS and other macro-level sections
        elif keyword == "DIRECTION":
            pin.direction = words[1].lower()
        elif (
            keyword == "USE"
            and words[1].upper() == "POWER"
            or (keyword == "USE" and words[1].upper() == "GROUND")
        ):
            pin.direction = "power"
        elif keyword == "LAYER":
            layer = words[1]
        elif keyword == "RECT" and layer is not None:
            left, bottom, right, top = (_dbu(w, scale) for w in words[1:5])
            pin.rects.append(Rect(layer, left, bottom, right, top))

    return macros


def read(path: Path | str, dbu: float = 0.001) -> dict[str, Macro]:
    return parse(Path(path).read_text(), dbu)
