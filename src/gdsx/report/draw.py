"""Rendering for gdsx.physical.draw"""

from __future__ import annotations

from ..physical.draw import Placement


def report(
    placed: list[Placement], groups: dict[str, str], tightness: dict[str, float]
) -> str:
    lines = [f"{len(placed)} placed cells, {len(set(groups.values()))} groups", ""]
    for label, value in sorted(tightness.items(), key=lambda kv: kv[1]):
        members = sum(1 for name in groups if groups[name] == label)
        verdict = (
            "compact" if value < 0.15 else "spread out" if value > 0.3 else "loose"
        )
        lines.append(
            f"  {label:20s} {members:5d} cells   spread {value:.2f}  {verdict}"
        )
    lines += [
        "",
        "  Spread is the mean distance from a group's centre, over the die",
        "  diagonal. A compact group is one the designer laid out together; a",
        "  spread-out one is more likely an artefact of the analysis.",
    ]
    return "\n".join(lines)
