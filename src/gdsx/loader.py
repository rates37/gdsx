"""Load and inspect a GDS layout.

Opens a GDS file, selects the top-level cell, records layer information, and
performs an initial inspection of the design. The inspection classifies
instantiated cells, determines whether the layout is self-contained,
and collects any top-level pin labels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import klayout.db as db

from .config import TechConfig


@dataclass
class Design:
    layout: db.Layout
    top: db.Cell
    tech: TechConfig
    path: Path
    layer_index: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def dbu(self) -> float:
        # Returns the layout database unit
        return self.layout.dbu

    def index_of(self, ld: tuple[int, int]) -> int | None:
        # Returns the KLayout layer index for a (layer, datatype) pair
        return self.layer_index.get(ld)

    def instances(self):
        """Yield every leaf-cell instance in global coordinates

        Arrays are expanded and hierarchy is traversed recursively, producing
        one placement for each leaf instance together with its accumulated
        transformation.
        """
        yield from _walk(self.layout, self.top, db.ICplxTrans())


def _array_trans(inst: db.Instance):
    """Every placement of an instance, expanding regular arrays"""
    base = inst.cplx_trans
    if not inst.is_regular_array():
        yield base
        return
    a, b = inst.a, inst.b
    for i in range(inst.na):
        for j in range(inst.nb):
            yield db.ICplxTrans(db.Vector(a.x * i + b.x * j, a.y * i + b.y * j)) * base


def _walk(layout: db.Layout, cell: db.Cell, trans: db.ICplxTrans):
    """Recursively traverse the cell hierarchy

    Yields:
        (cell_name, transform) for every leaf-cell instance
    """
    for inst in cell.each_inst():
        child = layout.cell(inst.cell_index)
        for itrans in _array_trans(inst):
            here = trans * itrans

            if child.is_leaf():
                yield child.name, here
            else:
                yield from _walk(layout, child, here)


def load(path: Path | str, tech: TechConfig, top_name: str | None = None) -> Design:
    """Load a GDS layout and construct a Design object"""
    layout = db.Layout()
    layout.read(str(path))

    # Find candidate top cells:
    tops = list(layout.top_cells())

    if top_name:
        top = layout.cell(top_name)
        if top is None:
            raise ValueError(f"no cell named {top_name!r}")

    elif len(tops) == 1:
        top = tops[0]

    else:
        # Choose top cell with largest bounding-box area (best guess)
        top = max(tops, key=lambda c: c.bbox().area())

    # Build a lookup from (layer, datatype) to KLayout layer index
    layer_index = {}
    for idx in layout.layer_indexes():
        info = layout.get_info(idx)
        layer_index[(info.layer, info.datatype)] = idx

    return Design(
        layout=layout,
        top=top,
        tech=tech,
        path=Path(path),
        layer_index=layer_index,
    )


@dataclass
class Inspection:
    """Summary of the contents of a loaded design."""

    # Basic layout information:
    top: str
    dbu: float
    layers: list[tuple[int, int]]

    # Cell classifications:
    cell_counts: Counter
    logic_cells: Counter
    nonlogic_cells: Counter
    unknown_cells: Counter

    # Whether logic-cell definitions include the routing data needed for
    # extraction
    self_contained: bool

    # Top-level text labels as (label, routing layer name)
    top_labels: list[tuple[str, str]]


def inspect(design: Design) -> Inspection:
    """Inspect a loaded design and summarize its contents"""
    tech = design.tech

    # Count all leaf-cell instances in the design
    counts = Counter(name for name, _ in design.instances())

    # Classify instances by cell type
    logic = Counter()
    nonlogic = Counter()
    unknown = Counter()

    for name, n in counts.items():
        if tech.is_logic_cell(name):
            logic[name] = n
        elif name.startswith(tech.library + "__"):
            # Standard-cell library cells that are not logic gates
            nonlogic[name] = n
        else:
            # Router-generated helper cells or other unrecognised cells
            unknown[name] = n

    # Determine whether the layout contains complete standard-cell geometry
    # rather than empty abstract frames
    self_contained = False
    if logic:
        probe = design.layout.cell(next(iter(logic)))
        li1_pin = design.index_of(tech.layer("li1").pin)

        self_contained = li1_pin is not None and probe.shapes(li1_pin).size() > 0

    # Collect top-level text labels from each routing layer's pin layer
    top_labels = []
    for rl in tech.routing:
        idx = design.index_of(rl.pin)
        if idx is None:
            continue

        for shape in design.top.shapes(idx).each():
            if shape.is_text():
                top_labels.append((shape.text.string, rl.name))

    return Inspection(
        top=design.top.name,
        dbu=design.dbu,
        layers=sorted(design.layer_index),
        cell_counts=counts,
        logic_cells=logic,
        nonlogic_cells=nonlogic,
        unknown_cells=unknown,
        self_contained=self_contained,
        top_labels=sorted(top_labels),
    )
