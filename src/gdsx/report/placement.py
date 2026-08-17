"""Rendering for gdsx.physical.placement: rows, bands, ordered arrays"""

from __future__ import annotations

from collections import defaultdict

from ..physical.placement import Ordering, bands, pitch, rows


def report(
    points: dict[str, tuple[float, float]],
    groups: dict[str, list[str]] | None = None,
    axis: str = "x",
) -> str:
    found = rows(points)
    out = [
        f"{len(points)} placed cells",
        f"{len(found)} cell rows, pitch {pitch(found)}",
        "",
        f"BANDS along {axis}",
    ]
    for i, band in enumerate(bands(points, axis)):
        out.append(
            f"  {i}: {band.lo:9.2f} .. {band.hi:9.2f}  ({band.span:7.2f} wide) "
            f"{len(band.members):4d} cells"
        )
        if groups:
            mix: dict[str, int] = defaultdict(int)
            for name in band.members:
                for group, members in groups.items():
                    if name in members:
                        mix[group] += 1
            for group, n in sorted(mix.items(), key=lambda kv: -kv[1]):
                out.append(f"        {n:4d}  {group}")
    return "\n".join(out)


def describe_ordering(ordering: Ordering) -> str:
    verdict = "ordered" if ordering.ordered else "not ordered"
    direction = "increasing" if ordering.tau > 0 else "decreasing"
    return (
        f"{ordering.name}: {ordering.members} entries, tau={ordering.tau:+.3f} "
        f"along {ordering.axis} ({direction}), "
        f"{ordering.inversions}/{ordering.pairs} inverted -- {verdict}"
    )