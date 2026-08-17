"""Draw analysis results onto the layout's own coordinates

The GDS carries a two-dimensional embedding of the design that a person drew, or
that a placer produced with real objectives, and it is better than anything a
force-directed graph will invent.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from pathlib import Path

from ..loader import Design
from ..netlist import Netlist

WIDTH = 1200
MARGIN = 20


@dataclass(frozen=True)
class Placement:
    name: str
    x: float
    y: float
    cell: str


def placements(design: Design, nl: Netlist) -> list[Placement]:
    """Where each instance of the netlist sits, in microns

    Instance names have to be reproduced exactly as `netlist.build` assigns
    them, or the picture is of a different design than the analysis.
    """
    tech = design.tech
    ordered = sorted(
        design.instances(), key=lambda t: (t[0], t[1].disp.y, t[1].disp.x, str(t[1]))
    )
    counters: dict[str, int] = {}
    known = {i.name for i in nl.instances}

    found = []
    for cell_name, trans in ordered:
        if not tech.is_logic_cell(cell_name):
            continue
        counters[cell_name] = counters.get(cell_name, 0) + 1
        short = cell_name.split("__", 1)[1]
        name = f"{short}_{counters[cell_name]}"
        if name in known:
            found.append(
                Placement(
                    name,
                    trans.disp.x * design.dbu,
                    trans.disp.y * design.dbu,
                    cell_name,
                )
            )
    return found


def palette(count: int) -> list[str]:
    """Evenly spaced hues"""
    out = []
    for index in range(max(1, count)):
        red, green, blue = colorsys.hsv_to_rgb(index / max(1, count), 0.62, 0.88)
        out.append(f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}")
    return out


def draw(
    placed: list[Placement],
    groups: dict[str, str] | None = None,
    title: str = "",
    size: float = 3.0,
) -> str:
    """An SVG of the floorplan, one dot per cell, coloured by group"""
    if not placed:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"

    groups = groups or {}
    labels = sorted({label for label in groups.values()})
    colour = dict(zip(labels, palette(len(labels))))

    xs = [p.x for p in placed]
    ys = [p.y for p in placed]
    span_x = max(max(xs) - min(xs), 1e-6)
    span_y = max(max(ys) - min(ys), 1e-6)
    scale = (WIDTH - 2 * MARGIN) / span_x
    height = span_y * scale + 2 * MARGIN + (24 if title else 0)

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{WIDTH}' "
        f"height='{height:.0f}' viewBox='0 0 {WIDTH} {height:.0f}'>",
        "<rect width='100%' height='100%' fill='#111'/>",
    ]
    if title:
        parts.append(
            f"<text x='{MARGIN}' y='18' fill='#ddd' font-family='monospace' "
            f"font-size='13'>{title}</text>"
        )

    top = height - MARGIN
    for placement in placed:
        x = MARGIN + (placement.x - min(xs)) * scale
        # y upwards in a layout, downwards in SVG
        y = top - (placement.y - min(ys)) * scale
        fill = colour.get(groups.get(placement.name, ""), "#444")
        parts.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{size}' fill='{fill}'>"
            f"<title>{placement.name} ({groups.get(placement.name, 'unlabelled')})</title>"
            "</circle>"
        )

    for index, label in enumerate(labels[:20]):
        parts.append(
            f"<rect x='{MARGIN + index * 130}' y='{height - 14:.0f}' width='10' height='10' "
            f"fill='{colour[label]}'/>"
            f"<text x='{MARGIN + 14 + index * 130}' y='{height - 5:.0f}' fill='#bbb' "
            f"font-family='monospace' font-size='11'>{label[:14]}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def spread(placed: list[Placement], groups: dict[str, str]) -> dict[str, float]:
    """How tightly each group sits together, as a fraction of the die"""
    by_name = {p.name: p for p in placed}
    xs = [p.x for p in placed]
    ys = [p.y for p in placed]
    diagonal = max(((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5, 1e-6)

    out = {}
    for label in sorted(set(groups.values())):
        members = [by_name[n] for n in groups if groups[n] == label and n in by_name]
        if len(members) < 2:
            continue
        cx = sum(p.x for p in members) / len(members)
        cy = sum(p.y for p in members) / len(members)
        mean = sum(((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5 for p in members) / len(
            members
        )
        out[label] = mean / diagonal
    return out


def write(path: Path, svg: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg)
    return path


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
