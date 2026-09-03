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

from . import geo
from .config import TechConfig


@dataclass
class Design:
    layout: geo.Layout
    top: str
    tech: TechConfig
    path: Path
    layer_index: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def dbu(self) -> float:
        # Returns the layout database unit
        return self.layout.dbu

    def index_of(self, ld: tuple[int, int]) -> int | None:
        # Returns the backend's layer handle for a (layer, datatype) pair
        return self.layer_index.get(ld)

    def instances(self):
        """Yield every leaf-cell instance in global coordinates

        Arrays are expanded and hierarchy is traversed recursively, producing
        one placement for each leaf instance together with its accumulated
        transformation.
        """
        yield from self.layout.leaf_instances(self.top)


def load(path: Path | str, tech: TechConfig, top_name: str | None = None) -> Design:
    """Load a GDS layout and construct a Design object"""
    layout = geo.read_file(path)

    # Find candidate top cells:
    tops = layout.top_cells()

    if top_name:
        if not layout.has_cell(top_name):
            raise ValueError(f"no cell named {top_name!r}")
        top = top_name

    elif len(tops) == 1:
        top = tops[0]

    else:
        # Choose top cell with largest bounding-box area (best guess)
        top = max(tops, key=lambda c: layout.cell_bbox(c).area())

    # Build a lookup from (layer, datatype) to the backend's layer handle
    layer_index = {ld: layout.layer_index(ld) for ld in layout.layers()}

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
    pin_source: str

    # Top-level text labels as (label, routing layer name)
    top_labels: list[tuple[str, str]]


def inspect(design: Design, macros: dict | None = None) -> Inspection:
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
    pin_source = "none"
    if logic:
        probe = next(iter(logic))
        li1_pin = design.index_of(tech.layer("li1").pin)
        self_contained = li1_pin is not None and any(
            True for _ in design.layout.shapes(probe, li1_pin)
        )
        if self_contained:
            pin_source = "in-GDS labels"
        elif macros and set(logic) <= set(macros):
            pin_source = "LEF abstracts"
        elif macros:
            missing = len(set(logic) - set(macros))
            pin_source = f"LEF abstracts ({missing} cells missing)"
        else:
            pin_source = "unavailable"

    # Collect top-level text labels from each routing layer's pin layer
    top_labels = []
    for rl in tech.routing:
        idx = design.index_of(rl.pin)
        if idx is None:
            continue

        for shape in design.layout.shapes(design.top, idx):
            if isinstance(shape, geo.Text):
                top_labels.append((shape.string, rl.name))

    return Inspection(
        top=design.top,
        dbu=design.dbu,
        layers=sorted(design.layer_index),
        cell_counts=counts,
        logic_cells=logic,
        nonlogic_cells=nonlogic,
        unknown_cells=unknown,
        self_contained=self_contained,
        pin_source=pin_source,
        top_labels=sorted(top_labels),
    )
