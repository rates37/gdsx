"""Layer-stack configuration.

Defines immutable data classes for routing layers, via layers, and technology
configuration, and provides a helper to load the configuration from a YAML file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import datafiles

# Default configuration files (defaults to sky130 for the puzzle). The YAML is
# the source of truth; the JSON is baked from it at build time
# and committed, so loading the default config does not require pyyaml.
DEFAULT_CONFIG = datafiles.data_file("sky130.yaml")
DEFAULT_JSON_CONFIG = datafiles.data_file("sky130.json")


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
class DeviceLayer:
    """A device-level layer: transistors, wells, contacts.

    Nothing is traced on these -- they carry no nets and extraction ignores
    them -- but they are most of what a GDS viewer draws, so the render bundle
    carries them. They live inside the standard cells rather than at the top
    level and have no pin or label purpose, which is why they are not
    `RoutingLayer`s.
    """

    name: str
    drawing: tuple[int, int]
    # True for a layer that covers regions rather than drawing wires (a well,
    # a diffusion tub). The viewer renders these flatter and further back.
    fill: bool = False
    # True for a contact layer, which is dropped from the zoomed-out level of
    # detail and from the 3D view, exactly as the metal vias are.
    via: bool = False


@dataclass(frozen=True)
class StackLayer:
    """One layer of the physical stack, for a 3D view."""

    # Layer name (matches a `RoutingLayer.name` or `ViaLayer.name`)
    name: str

    # Microns. `z` is the bottom of the layer; z(n+1) == z(n) + thickness(n)
    # for a contiguous stack
    z: float
    thickness: float
    # True for a via/contact layer rather than a routing layer
    via: bool = False


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
    # The physical layer stack, bottom to top. Empty for a tech config with no
    # 3D data (older configs, or one built by hand for a test)
    stack: tuple[StackLayer, ...] = ()
    # Device-level layers, bottom to top. Empty for a config that predates
    # them; every consumer must cope with that rather than assume.
    device: tuple[DeviceLayer, ...] = ()

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


def dump_json(
    src: Path | str = DEFAULT_CONFIG, dst: Path | str = DEFAULT_JSON_CONFIG
) -> Path:
    # Bakes a YAML config to JSON so loading it does not require pyyaml.
    import yaml

    raw = yaml.safe_load(Path(src).read_text())
    dst = Path(dst)
    dst.write_text(json.dumps(raw, indent=2) + "\n")
    return dst


def _read_raw(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text())

    import yaml

    return yaml.safe_load(path.read_text())


def from_raw(raw: dict) -> TechConfig:
    """A TechConfig from an already-parsed config document

    Split out of `load` for callers that have the document but no file to read
    it from.
    """
    # Convert the parsed config into configuration objects
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
        stack=tuple(
            StackLayer(s["name"], s["z"], s["thickness"], s.get("via", False))
            for s in raw.get("stack", ())
        ),
        device=tuple(
            DeviceLayer(
                d["name"], tuple(d["drawing"]),
                d.get("fill", False), d.get("via", False),
            )
            for d in raw.get("device", ())
        ),
    )


def load(path: Path | str | None = None) -> TechConfig:
    # Loads a technology configuration, preferring the baked JSON and
    # falling back to the YAML source (which requires pyyaml) if no JSON
    # config is available.
    if path is None:
        path = DEFAULT_JSON_CONFIG if DEFAULT_JSON_CONFIG.exists() else DEFAULT_CONFIG
    return from_raw(_read_raw(Path(path)))
