"""Mutual-dependency register grouping.

Two flops belong to the same multi-bit unit if each appears in the
other's D cone: each half of a 2-bit counter feeds the other's next-state
equation. That relation is symmetric, assumption-free, and derived from the
circuit rather than from names or list adjacency.

Pairing by adjacent list position or by cell type is exactly the mistake
`work/pairs.py` made in the original investigation: it produced a wrong
answer, caught only because it contradicted an unrelated count. Pairing by
structure removes that failure mode by construction.

This is genuinely distinct from `analysis.registers.find_registers`, which
groups by shared support rather than by mutual reference, and produces
coarser groups.
"""

from __future__ import annotations

from ..core.graph import Graph


def mutual(graph: Graph, flops: list[str]) -> list[tuple[str, ...]]:
    """Split `flops` into groups linked by mutual D-cone reference.

    An edge joins `f` and `g` when each is in the other's `d_support`,
    `pair_sensitivity.py`'s `pair_up` only ever found isolated pairs on Two
    Stars, but the relation itself is a graph, not a pairing rule, so this
    returns its connected components. A pure pair comes back as a 2-tuple, a
    flop with no mutual partner comes back alone.
    """
    names = set(flops)
    support = {f: graph.d_support(f) & names for f in flops}

    edges: dict[str, set[str]] = {f: set() for f in flops}
    for f in flops:
        for g in support[f]:
            if g != f and f in support[g]:
                edges[f].add(g)
                edges[g].add(f)

    seen: set[str] = set()
    groups: list[tuple[str, ...]] = []
    for f in flops:
        if f in seen:
            continue
        component: set[str] = set()
        stack = [f]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(edges[current] - component)
        seen |= component
        groups.append(tuple(sorted(component)))
    return groups
