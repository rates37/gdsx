"""Reading a `LayoutSpec` from a JSON file.

The spec is a dataclass whose `groups` field is a full instance-name ->
label map. That map cannot be written by
hand: instance names come out of synthesis, so they are only known once the
netlist exists. What an author actually knows is which *register* belongs to
which group, so that is what the file carries, and `groups` is derived from
it against the netlist being placed (see `groups.by_register`).

A spec file for puzzle 1:

    {
      "fill": 0.30,
      "aspect": 1.0,
      "mode": "banded",
      "order": ["accumulator", "phase", "cycle"],
      "groups_by_prefix": {"acc": "accumulator", "O": "accumulator",
                           "ph": "phase", "cyc": "cycle"}
    }

Registers are not always the right unit. A design whose interesting groups
all read the *same* register bank -- five comparators over one shift register
-- has every combinational cell nearest to that one bank, so `by_register`
returns one group and `mode: "interleaved"` has nothing to interleave. For
that case a spec may also carry `groups_by_cone`, an ordered list naming a
cone by its root net and the group its cells belong to:

    "groups_by_cone": [{"root": "O[6]", "label": "cmp_b"},
                       {"root": "success", "label": "cmp_match"}]

Cone labels win over prefix labels for the cells they claim; everything they
do not claim, flops included, still comes from `groups_by_prefix`. See
`groups.by_cone` for how a root is resolved and why the list is ordered.
"""

from __future__ import annotations

import json
from pathlib import Path

from .groups import by_cone, by_register
from .place import LayoutSpec

FIELDS = {
    "fill",
    "aspect",
    "mode",
    "order",
    "channels",
    "seed",
    "groups_by_prefix",
    "groups_by_cone",
}


class SpecError(ValueError):
    """A layout spec file is malformed or disagrees with the netlist."""


def load_spec(path: Path, nl) -> LayoutSpec:
    """Read a spec file and bind it to `nl`, resolving `groups`."""
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a JSON object")

    unknown = set(data) - FIELDS
    if unknown:
        raise SpecError(f"{path}: unknown field(s) {sorted(unknown)}")
    if "fill" not in data:
        raise SpecError(f"{path}: 'fill' is required -- it is the target utilisation")

    by_prefix = data.get("groups_by_prefix", {})
    cone_roots = data.get("groups_by_cone", [])
    try:
        groups = by_register(nl, by_prefix) if by_prefix else {}
        if cone_roots:
            groups.update(
                by_cone(nl, [(c["root"], c["label"]) for c in cone_roots])
            )
    except (ValueError, KeyError, TypeError) as exc:
        raise SpecError(f"{path}: {exc}") from exc

    order = tuple(data.get("order", ()))
    labels = set(groups.values())
    missing = labels - set(order)
    if order and missing:
        raise SpecError(f"{path}: 'order' does not mention {sorted(missing)}")

    return LayoutSpec(
        fill=float(data["fill"]),
        aspect=float(data.get("aspect", 1.0)),
        groups=groups,
        order=order,
        mode=data.get("mode", "banded"),
        channels=tuple(data.get("channels", ())),
        seed=int(data.get("seed", 0)),
    )