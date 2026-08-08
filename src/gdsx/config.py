"""Layer-stack configuration.

Defines immutable data classes for routing layers, via layers, and technology
configuration, and provides a helper to load the configuration from a YAML file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Default configuration file (defaults to sky130 for the puzzle)
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sky130.yaml"


@dataclass(frozen=True)
class RoutingLayer:
    """Configuration for a routing layer and its associated GDS layers."""

    # Logical layer name (e.g. "M1")
    name: str

    # GDS layer/datatype pairs for geometry, pins, and labels
    drawing: tuple[int, int]
    pin: tuple[int, int]
    label: tuple[int, int]


@dataclass(frozen=True)
class ViaLayer:
    """Configuration for a via connecting two routing layers"""

    # Via name (e.g. "VIA1")
    name: str

    # GDS layer/datatype for the via geometry
    layer: tuple[int, int]
    # Adjacent routing layers connected by this via
    below: str
    above: str


@dataclass(frozen=True)
class TechConfig:
    """Technology-specific routing and library configuration."""

    # PDK and standard-cell library names
    pdk: str
    library: str

    # Routing and via layer definitions
    routing: list[RoutingLayer]
    vias: list[ViaLayer]
    # Names of power and ground pins to ignore during logic extraction
    power_pins: set[str]
    # Cell-name prefixes that identify non-logic library cells
    nonlogic_prefixes: tuple[str, ...]

    def layer(self, name: str) -> RoutingLayer:
        # Return the routing-layer configuration with the given name
        for rl in self.routing:
            if rl.name == name:
                return rl
        raise KeyError(f"unknown routing layer {name!r}")

    def is_logic_cell(self, cell_name: str) -> bool:
        # Returns True if the cell should be treated as a logic gate
        # Ignore cells outside the configured standard-cell library
        if not cell_name.startswith(self.library + "__"):
            return False

        # Exclude filler, tap, decap, and other non-logic cells
        return not cell_name.startswith(self.nonlogic_prefixes)


def load(path: Path | str | None = None) -> TechConfig:
    # Loads a technology configuration from a YAML file
    # Read and parse the configuration
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text())

    # Convert the parsed YAML into configuration objects
    return TechConfig(
        pdk=raw["pdk"],
        library=raw["library"],
        routing=[
            RoutingLayer(
                r["name"],
                tuple(r["drawing"]),
                tuple(r["pin"]),
                tuple(r["label"]),
            )
            for r in raw["routing"]
        ],
        vias=[
            ViaLayer(
                v["name"],
                tuple(v["layer"]),
                v["below"],
                v["above"],
            )
            for v in raw["vias"]
        ],
        power_pins=set(raw["power_pins"]),
        nonlogic_prefixes=tuple(raw["nonlogic_prefixes"]),
    )
