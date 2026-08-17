"""Carve a physical region of the die out as a netlist of its own

`slice` carves along logical lines, everything between one set of nets and
another. This carves along physical ones: take a rectangle of the floorplan
and export the cells inside it. A design is placed to keep connected things
together, so a rectangle of die usually is a functional block, and 200 cells
is margnially easier to analyse than 900.

Cut ratio is: of the nets the region touches, the fraction that
cross its boundary. A real block reads a few control signals and produces a few
results, so its cut ratio is low. A rectangle drawn through the middle of
something has a cut ratio near 1, and the netlist it produces is a fragment
with fifty ports and no meaning.

An individual cell is atomic, it is in or out by where its centre lies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core.graph import Graph
from .netlist import Netlist


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x0", min(self.x0, self.x1))
        object.__setattr__(self, "x1", max(self.x0, self.x1))
        object.__setattr__(self, "y0", min(self.y0, self.y1))
        object.__setattr__(self, "y1", max(self.y0, self.y1))

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def __str__(self) -> str:
        return f"({self.x0:.2f}, {self.y0:.2f}) .. ({self.x1:.2f}, {self.y1:.2f})"


@dataclass
class Region:
    box: Box
    inside: list[str]
    netlist: Netlist
    total_cells: int
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    internal: list[str] = field(default_factory=list)

    @property
    def crossing(self) -> int:
        return len(self.inputs) + len(self.outputs)

    @property
    def cut_ratio(self) -> float:
        """Fraction of the region's nets that cross its boundary

        Low means the rectangle found a block. High means it found a (incomplete) fragment
        """
        total = self.crossing + len(self.internal)
        return self.crossing / total if total else 0.0

    @property
    def verdict(self) -> str:
        if not self.inside:
            return "empty"
        if self.cut_ratio <= 0.35:
            return "a self-contained block"
        if self.cut_ratio <= 0.6:
            return "a block with a lot of context"
        return "a fragment -- the boundary cuts through the logic"

    def report(self) -> str:
        return "\n".join(
            [
                f"region {self.box}",
                f"  {len(self.inside)} of {self.total_cells} cells "
                f"({len(self.inside) / self.total_cells:.0%})",
                f"  {len(self.inputs)} inputs, {len(self.outputs)} outputs, "
                f"{len(self.internal)} internal nets",
                f"  cut ratio {self.cut_ratio:.2f} -- {self.verdict}",
            ]
        )


def within(
    points: dict[str, tuple[float, float]],
    box: Box,
    extents: dict[str, tuple[float, float]] | None = None,
) -> list[str]:
    """Instances whose centre lies in the box"""
    chosen = []
    for name, (x, y) in points.items():
        w, h = (extents or {}).get(name, (0.0, 0.0))
        if box.contains(x + w / 2, y + h / 2):
            chosen.append(name)
    return sorted(chosen)


def extract(
    nl: Netlist,
    points: dict[str, tuple[float, float]],
    box: Box,
    name: str | None = None,
    extents: dict[str, tuple[float, float]] | None = None,
) -> Region:
    """Everything in `box`, as a netlist with the cut nets promoted to ports"""
    inside = within(points, box, extents)
    carved = Graph.of(nl).subgraph(set(inside), name or f"{nl.top}_region")

    inputs = sorted(p for p, d in carved.ports.items() if d == "input")
    outputs = sorted(p for p, d in carved.ports.items() if d == "output")
    internal = sorted(
        net
        for net in carved.nets
        if net not in carved.ports and net not in carved.power_nets
    )
    return Region(
        box=box,
        inside=inside,
        netlist=carved,
        total_cells=len(nl.instances),
        inputs=inputs,
        outputs=outputs,
        internal=internal,
    )


def bounding_box(
    points: dict[str, tuple[float, float]],
    members: list[str],
    pad: float = 0.0,
    extents: dict[str, tuple[float, float]] | None = None,
) -> Box:
    """The smallest box holding these instances, optionally padded"""
    here = [
        (points[m], (extents or {}).get(m, (0.0, 0.0))) for m in members if m in points
    ]
    if not here:
        raise ValueError("no placed members")
    return Box(
        min(p[0] for p, _ in here) - pad,
        min(p[1] for p, _ in here) - pad,
        max(p[0] + e[0] for p, e in here) + pad,
        max(p[1] + e[1] for p, e in here) + pad,
    )
