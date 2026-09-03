"""A layered DAG layout for small netlist sub-graphs, computed without
`graphviz`.

`netlist.to_dot` hands the picture to `dot`, which is not available in the
browser (see `external/graphviz.py`). This computes the same kind of
picture directly -- gates and nets arranged left to right in layers, wires
routed as straight edges between them -- with Sugiyama's algorithm:

1. break cycles (a sequential design's feedback is a real cycle at gate
   granularity; DFS back-edge reversal is the standard first step);
2. assign each node a layer by longest path from a source;
3. order each layer with a few barycenter sweeps to reduce crossings;
4. place coordinates from (layer, order).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..core.netlist import Netlist

# no sub-graph above this size gets a layout; a bigger picture helps nobody
MAX_NODES = 200

_LAYER_GAP = 160
_ROW_GAP = 60


class TooManyNodes(ValueError):
    """`layered` refuses to lay out more than `MAX_NODES` nodes"""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"{count} nodes exceeds the {MAX_NODES}-node cap for a graph "
            "view; sub-net a smaller block first (Graph.subgraph)"
        )


@dataclass(frozen=True)
class LayoutNode:
    id: str  # "g:<instance>" or "n:<net>", unique across both kinds
    label: str  # the plain instance or net name, for display
    kind: str  # "gate" | "net"
    layer: int
    order: int  # position within its layer, after crossing reduction
    x: int
    y: int


@dataclass(frozen=True)
class LayoutEdge:
    source: str  # a LayoutNode.id
    target: str  # a LayoutNode.id
    net: str
    feedback: bool  # True if breaking a cycle required reversing this edge


@dataclass(frozen=True)
class Layout:
    nodes: tuple[LayoutNode, ...]
    edges: tuple[LayoutEdge, ...]
    n_layers: int


def _graph_of(
    nl: Netlist,
) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str]]]:
    """Instance and net nodes (id -> (label, kind)), and directed edges
    (source id, target id, net name), oriented by pin direction exactly as
    `netlist.to_dot` draws them: `gate -> net` for an output pin, `net ->
    gate` for an input pin.

    Both kinds get their own node, not collapsed to gate-to-gate edges,
    so a multiply-driven or undriven net (`Netlist.conflicts`) still gets an
    honest edge list instead of a guessed single driver.
    """
    from ..functions import lookup

    cell_of = {inst.name: lookup(inst.cell) for inst in nl.instances}
    nodes: dict[str, tuple[str, str]] = {
        f"g:{inst.name}": (inst.name, "gate") for inst in nl.instances
    }
    edges: list[tuple[str, str, str]] = []
    for net in sorted(nl.nets):
        if net in nl.power_nets:
            continue
        net_id = f"n:{net}"
        nodes[net_id] = (net, "net")
        for ref in nl.nets[net]:
            inst, pin = ref.split("/")
            cell = cell_of.get(inst)
            if cell is None:  # fill/tap/decap/antenna: no behaviour, no pins
                continue
            gate_id = f"g:{inst}"
            if cell.direction(pin) == "output":
                edges.append((gate_id, net_id, net))
            else:
                edges.append((net_id, gate_id, net))
    return nodes, edges


def _break_cycles(
    node_ids: list[str], adjacency: dict[str, set[str]]
) -> dict[str, set[str]]:
    """A copy of `adjacency` with every back edge (found by DFS) reversed, so
    the result is acyclic.

    Classifies each edge by the colour of its destination when visited: white
    (unvisited) is a tree edge, grey (on the current DFS path) is a back edge
    and gets reversed, black (already finished) is a forward/cross edge and is
    kept as-is. Neither can create a cycle once combined with the tree.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in node_ids}
    forward: dict[str, set[str]] = {n: set() for n in node_ids}
    to_reverse: set[tuple[str, str]] = set()

    def visit(start: str) -> None:
        stack = [(start, iter(sorted(adjacency[start])))]
        color[start] = GREY
        while stack:
            node, neighbours = stack[-1]
            advanced = False
            for m in neighbours:
                if color[m] == WHITE:
                    forward[node].add(m)
                    color[m] = GREY
                    stack.append((m, iter(sorted(adjacency[m]))))
                    advanced = True
                    break
                if color[m] == GREY:
                    to_reverse.add((node, m))
                else:
                    forward[node].add(m)
            if not advanced:
                color[node] = BLACK
                stack.pop()

    for n in node_ids:
        if color[n] == WHITE:
            visit(n)

    for src, dst in to_reverse:
        forward[dst].add(src)
    return forward


def _assign_layers(node_ids: list[str], forward: dict[str, set[str]]) -> dict[str, int]:
    """Layer = longest path from a source, by relaxing edges in Kahn order
    over the acyclic graph `_break_cycles` produced."""
    indeg = {n: 0 for n in node_ids}
    for n in node_ids:
        for m in forward[n]:
            indeg[m] += 1

    layer = {n: 0 for n in node_ids}
    queue = deque(sorted(n for n in node_ids if indeg[n] == 0))
    remaining = dict(indeg)
    while queue:
        n = queue.popleft()
        for m in sorted(forward[n]):
            layer[m] = max(layer[m], layer[n] + 1)
            remaining[m] -= 1
            if remaining[m] == 0:
                queue.append(m)
    return layer


def _order_layers(
    node_ids: list[str], forward: dict[str, set[str]], layer_of: dict[str, int]
) -> dict[str, int]:
    """Reduce edge crossings with a handful of barycenter sweeps.

    Exact crossing minimisation is NP-hard. A few median sweeps up and down
    the layers gets close for graphs this small (`MAX_NODES` sized) and stays
    fast and deterministic: ties keep the previous sweep's relative order,
    Python's sort being stable.
    """
    n_layers = max(layer_of.values(), default=-1) + 1
    layers: list[list[str]] = [[] for _ in range(n_layers)]
    for n in sorted(node_ids):
        layers[layer_of[n]].append(n)

    predecessors: dict[str, set[str]] = {n: set() for n in node_ids}
    for n in node_ids:
        for m in forward[n]:
            predecessors[m].add(n)

    position = {n: i for layer in layers for i, n in enumerate(layer)}

    def barycenter(n: str, neighbours: set[str]) -> float:
        return (
            sum(position[m] for m in neighbours) / len(neighbours)
            if neighbours
            else position[n]
        )

    for sweep in range(4):
        going_down = sweep % 2 == 0
        indices = range(1, n_layers) if going_down else range(n_layers - 2, -1, -1)
        neighbours_of = predecessors if going_down else forward
        for i in indices:
            layers[i].sort(key=lambda n: barycenter(n, neighbours_of[n]))
            for pos, n in enumerate(layers[i]):
                position[n] = pos

    return {n: i for layer in layers for i, n in enumerate(layer)}


def layered(nl: Netlist) -> Layout:
    """A left-to-right layered layout of `nl`'s instances and nets.

    Raises `TooManyNodes` above `MAX_NODES`. `nl` is usually a sub-netlist
    (`Graph.subgraph`, exposed as `api.sub_netlist`); the whole point of
    the cap is that this only ever runs on something small enough to look at.
    """
    nodes, edges = _graph_of(nl)
    if len(nodes) > MAX_NODES:
        raise TooManyNodes(len(nodes))

    node_ids = list(nodes)
    adjacency: dict[str, set[str]] = {n: set() for n in node_ids}
    for src, dst, _ in edges:
        if src != dst:
            adjacency[src].add(dst)

    forward = _break_cycles(node_ids, adjacency)
    layer_of = _assign_layers(node_ids, forward)
    order_of = _order_layers(node_ids, forward, layer_of)

    feedback = {
        (src, dst)
        for src in node_ids
        for dst in adjacency[src]
        if dst not in forward[src]
    }

    layout_nodes = tuple(
        LayoutNode(
            id=n,
            label=nodes[n][0],
            kind=nodes[n][1],
            layer=layer_of[n],
            order=order_of[n],
            x=layer_of[n] * _LAYER_GAP,
            y=order_of[n] * _ROW_GAP,
        )
        for n in sorted(node_ids, key=lambda n: (layer_of[n], order_of[n]))
    )
    layout_edges = tuple(
        LayoutEdge(source=src, target=dst, net=net, feedback=(src, dst) in feedback)
        for src, dst, net in edges
        if src != dst
    )
    return Layout(
        nodes=layout_nodes,
        edges=layout_edges,
        n_layers=max(layer_of.values(), default=-1) + 1,
    )
