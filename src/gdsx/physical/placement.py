"""Reading the floorplan as evidence

Extracts:

* rows: standard cells sit on a fixed pitch. The pitch and the occupancy
  per row say how the die was used, and a row with nothing in it is a gap
  between blocks.

* bands: project every cell onto one axis and the design usually falls
  into clusters separated by routing channels. Those clusters tend to be the
  functional blocks, in dataflow order.

* ordered arrays: when a design holds an indexed array of registers, the
  placer usually lays entry 0, 1, 2 ... out in order along an axis, because
  that is what keeps each entry near its own decode. So if a set of registers
  carries an index, geometry can confirm the index independently of the
  netlist
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

# A gap this many times the typical spacing is treated as a block boundary.
# Tuned to be permissive: splitting too little is recoverable by looking at the
# report, splitting too much invents blocks that are not there.
BAND_GAP = 4.0

# Below this many members, "they are in order" is not evidence of anything.
MIN_ORDERED = 4


@dataclass(frozen=True)
class Row:
    y: float
    count: int


@dataclass
class Band:
    axis: str
    lo: float
    hi: float
    members: list[str] = field(default_factory=list)

    @property
    def span(self) -> float:
        return self.hi - self.lo

    @property
    def centre(self) -> float:
        return (self.lo + self.hi) / 2


@dataclass
class Ordering:
    """How well an indexed group's layout agrees with its index"""

    name: str
    axis: str
    tau: float  # between index and position, -1..1
    members: int
    inversions: int
    pairs: int

    @property
    def ordered(self) -> bool:
        return self.members >= MIN_ORDERED and abs(self.tau) >= 0.9


def rows(points: dict[str, tuple[float, float]], tolerance: float = 0.05) -> list[Row]:
    """Distinct cell rows, by clustering y within a tolerance

    The row's y is the mean of its members, not the bucket it landed in
    """
    buckets: dict[int, list[float]] = defaultdict(list)
    for _, y in points.values():
        buckets[round(y / tolerance)].append(y)
    return [Row(round(sum(v) / len(v), 6), len(v)) for _, v in sorted(buckets.items())]


def pitch(found: list[Row]) -> float | None:
    """The dominant row-to-row spacing, or None if the rows are irregular"""
    if len(found) < 2:
        return None
    gaps = [round(found[i + 1].y - found[i].y, 3) for i in range(len(found) - 1)]
    if not gaps:
        return None
    counts: dict[float, int] = defaultdict(int)
    for g in gaps:
        counts[g] += 1
    return max(counts, key=lambda g: counts[g])


def bands(
    points: dict[str, tuple[float, float]], axis: str = "x", gap: float = BAND_GAP
) -> list[Band]:
    """Clusters along one axis, split wherever the spacing jumps

    The threshold is relative to the median spacing, so it does not care about
    the size of the die or the units
    """
    index = 0 if axis == "x" else 1
    ordered = sorted(points, key=lambda n: points[n][index])
    values = [points[n][index] for n in ordered]
    if len(values) < 2:
        return [
            Band(
                axis, min(values, default=0.0), max(values, default=0.0), list(ordered)
            )
        ]

    gaps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    typical = median([g for g in gaps if g > 0] or [1.0])
    limit = typical * gap

    found: list[Band] = []
    start = 0
    for i, g in enumerate(gaps):
        if g > limit:
            found.append(Band(axis, values[start], values[i], ordered[start : i + 1]))
            start = i + 1
    found.append(Band(axis, values[start], values[-1], ordered[start:]))
    return found


def kendall(pairs: list[tuple[float, float]]) -> tuple[float, int, int]:
    """Kendall tau between two sequences, plus the raw inversion counts

    Rank correlation rather than Pearson because the question is only whether
    the order agrees, not whether the spacing is even.
    """
    concordant = discordant = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a = (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1])
            if a > 0:
                concordant += 1
            elif a < 0:
                discordant += 1
    total = concordant + discordant
    tau = (concordant - discordant) / total if total else 0.0
    return tau, discordant, total


def ordering(
    name: str, indexed: dict[str, int], points: dict[str, tuple[float, float]]
) -> Ordering | None:
    """Does this indexed group lie in index order on the die?"""
    have = {n: i for n, i in indexed.items() if n in points}
    if len(have) < 2:
        return None

    xs = [points[n][0] for n in have]
    ys = [points[n][1] for n in have]
    axis = "x" if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else "y"
    at = 0 if axis == "x" else 1

    # One position per index: entries are several cells wide, so use the mean.
    grouped: dict[int, list[float]] = defaultdict(list)
    for n, i in have.items():
        grouped[i].append(points[n][at])
    pairs = [(float(i), sum(v) / len(v)) for i, v in grouped.items()]

    tau, inversions, total = kendall(pairs)
    return Ordering(name, axis, tau, len(grouped), inversions, total)


def compactness(members: list[str], points: dict[str, tuple[float, float]]) -> float:
    """Mean distance to the group's centroid. Smaller means tighter"""
    here = [points[n] for n in members if n in points]
    if not here:
        return 0.0
    cx = sum(p[0] for p in here) / len(here)
    cy = sum(p[1] for p in here) / len(here)
    return sum(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in here) / len(here)


