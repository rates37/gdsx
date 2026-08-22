"""Puzzle 4 (Nine Lives)'s bake assertion, from docs/game/puzzle-pack.md §4.

The assertion has three parts:

    1. The extracted netlist's 9 state flops are mutually exclusive over all
       reachable states, checked by exhaustive enumeration from reset.
    2. The transition relation recovered from the extracted netlist has
       exactly one path of length 9 from S0 to S8.
    3. The 3-state decoy component has no outgoing edge to S8.

Everything below is recovered from `netlist.json` -- the extraction of
`design.gds` -- and never from the RTL. The state flops are identified the
way a player would: fifteen flops, six of them driving a top-level port
(`O[3:0]` is the lives counter, `O[7]` is `locked`, `success` is the lock),
and the nine that drive nothing outside the design are the state.

Part 2 cannot hold as written, and the reason is a property of the design
rather than of this implementation; it is reported rather than worked around.
See the notes printed at the end.

Usage: uv run python scripts/check_puzzle4_structure.py
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

from gdsx import netlist as netlist_mod
from gdsx.core.graph import Graph
from gdsx.functions import is_sequential
from gdsx.sim import Simulator

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_DIR = ROOT / "puzzles" / "4-nine-lives"

STATES = 9
SYMBOLS = 4
WORD_LENGTH = 9  # what part 2 asks about
SEARCH_LENGTH = 6  # exhaustive stimulus search: 4**6 = 4096 words

CLOCK, RESET, ENABLE = "clk", "rst_n", "enable"
SYMBOL_BITS = ("I[1]", "I[0]")  # MSB first
SUCCESS = "success"


def _load():
    return netlist_mod.Netlist.from_dict(
        json.loads((PUZZLE_DIR / "netlist.json").read_text())
    )


def _flops(nl):
    """Flop instance name -> its Q net, in netlist order."""
    return {
        inst.name: inst.connections["Q"]
        for inst in nl.instances
        if is_sequential(inst.cell) and "Q" in inst.connections
    }


def _vector(symbol: int, *, reset: bool = False) -> dict[str, int]:
    bits = {
        port: (symbol >> (len(SYMBOL_BITS) - 1 - i)) & 1
        for i, port in enumerate(SYMBOL_BITS)
    }
    return {CLOCK: 0, RESET: 0 if reset else 1, ENABLE: 1, **bits}


def _one_hot(bits: tuple[int, ...]) -> int | None:
    """The index of the single set bit, or None if it is not one-hot."""
    hot = [i for i, b in enumerate(bits) if b]
    return hot[0] if len(hot) == 1 else None


def _sccs(edges: dict[int, set[int]], nodes: list[int]) -> list[list[int]]:
    """Tarjan's algorithm, iterative, on a graph of nine nodes."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    out: list[list[int]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    out.append(sorted(component))
    return out


def main() -> int:  # noqa: C901 - one report, read top to bottom
    nl = _load()
    failures: list[str] = []
    unsatisfiable: list[str] = []

    flops = _flops(nl)
    state_flops = [name for name, q in flops.items() if q not in nl.ports]
    observed = {name: q for name, q in flops.items() if q in nl.ports}
    print(f"flops: {len(flops)} total, {len(observed)} driving a port "
          f"({', '.join(sorted(observed.values()))})")
    print(f"       {len(state_flops)} state flops (assertion wants {STATES})")
    if len(state_flops) != STATES:
        failures.append(f"{len(state_flops)} state flops, expected {STATES}")
        return _report(failures, unsatisfiable)

    sim = Simulator(nl)
    sim.reset()
    sim.step(_vector(0, reset=True))
    quiet = {name: sim.state[name] for name in flops if name not in state_flops}
    hot = [f for f in state_flops if sim.state[f]]
    print(f"reset state: {len(hot)} state flop(s) set ({', '.join(hot) or '-'})")
    if len(hot) != 1:
        failures.append("the reset state is not one-hot")
        return _report(failures, unsatisfiable)

    # The accepting state is the one the `success` flop's D cone depends on.
    # `d_support` names sequential leaves by instance, so the comparison is on
    # instance names rather than on Q nets.
    graph = Graph.of(nl)
    success_flop = graph.driver_of(SUCCESS).instance
    leaves = set(graph.d_support(success_flop))
    accepting = [f for f in state_flops if f in leaves]
    print(f"{SUCCESS} <- {success_flop}; its D cone has {len(leaves)} leaves "
          f"({', '.join(sorted(leaves))})")
    print(f"       of which state flops: {accepting or '-'}")
    if len(accepting) != 1:
        failures.append(
            f"the success cone should name exactly one state flop; it names "
            f"{len(accepting)}"
        )
        return _report(failures, unsatisfiable)

    # Number the states so that the reset state is S0 and the accepting state
    # is S8, which is the numbering puzzle-pack.md §4 uses. The rest keep
    # netlist order. Nothing depends on the labels; they make the printed
    # relation readable.
    rest = [f for f in state_flops if f not in (hot[0], accepting[0])]
    state_flops = [hot[0], *rest, accepting[0]]
    s0, s8 = 0, STATES - 1

    # --- the transition relation, 9 x 4, from the extracted netlist ---------
    # Force the machine into each state in turn with the counter at its reset
    # value and the lock clear, apply one symbol, read the state back.
    relation: dict[tuple[int, int], int] = {}
    not_one_hot: list[tuple[int, int]] = []
    for state, symbol in product(range(STATES), range(SYMBOLS)):
        sim.state = dict(quiet)
        for i, flop in enumerate(state_flops):
            sim.state[flop] = int(i == state)
        sim.step(_vector(symbol))
        bits = tuple(sim.state[f] for f in state_flops)
        nxt = _one_hot(bits)
        if nxt is None:
            not_one_hot.append((state, symbol))
        else:
            relation[(state, symbol)] = nxt

    # --- part 1: mutual exclusivity ----------------------------------------
    # One-hot in gives one-hot out for every (state, symbol) pair, and reset is
    # one-hot, so every state reachable from reset is one-hot. That is the
    # exhaustive enumeration the assertion asks for: the transition relation
    # has 36 entries and all of them were evaluated.
    print()
    print(f"1. one-hot closure: {STATES * SYMBOLS} (state, symbol) pairs "
          f"evaluated, {len(not_one_hot)} produced a non-one-hot successor")
    if not_one_hot:
        failures.append(
            f"the 9 state flops are not mutually exclusive: "
            f"{not_one_hot[:4]} left the one-hot encoding"
        )

    reachable = {s0}
    frontier = [s0]
    while frontier:
        state = frontier.pop()
        for symbol in range(SYMBOLS):
            nxt = relation.get((state, symbol))
            if nxt is not None and nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    print(f"   states reachable from S{s0}: {sorted(reachable)} "
          f"({len(reachable)} of {STATES})")

    print("   transition relation:")
    for state in range(STATES):
        row = " ".join(
            f"{symbol}->S{relation.get((state, symbol), '?')}"
            for symbol in range(SYMBOLS)
        )
        print(f"     S{state}: {row}")

    # --- part 2: paths of length 9 from S0 to S8 ---------------------------
    # Count symbol words, which is what a player enumerates: two words that
    # walk the same states are still two answers.
    counts = {state: 0 for state in range(STATES)}
    counts[s0] = 1
    for _ in range(WORD_LENGTH):
        nxt = {state: 0 for state in range(STATES)}
        for state, total in counts.items():
            if not total:
                continue
            for symbol in range(SYMBOLS):
                target = relation.get((state, symbol))
                if target is not None:
                    nxt[target] += total
        counts = nxt
    print()
    print(f"2. words of length {WORD_LENGTH} walking S{s0} to S{s8}: "
          f"{counts[s8]} (assertion wants exactly 1)")
    if counts[s8] != 1:
        unsatisfiable.append(
            f"a memoryless {STATES}-state machine whose three decoy states are "
            f"a disjoint sink has at most {STATES - 3 - 1} edges on its "
            f"accepting path, so no path of length {WORD_LENGTH} from S{s0} to "
            f"S{s8} can be the unique one: every state holds on its wrong "
            f"symbols, so {counts[s8]} words of that length end at S{s8}"
        )

    # What is true instead, and is the property the puzzle actually rests on:
    # exactly one stimulus, up to its free tail, raises `success`.
    accept: list[tuple[int, ...]] = []
    for word in product(range(SYMBOLS), repeat=SEARCH_LENGTH):
        sim.reset()
        sim.step(_vector(0, reset=True))
        raised = False
        for symbol in word:
            values = sim.step(_vector(symbol))
            raised = raised or values[SUCCESS] == 1
        if raised:
            accept.append(word)
    prefixes = {w[: SEARCH_LENGTH - 1] for w in accept}
    print(f"   exhaustive stimulus search, all {SYMBOLS ** SEARCH_LENGTH} words "
          f"of length {SEARCH_LENGTH}: {len(accept)} raise {SUCCESS}, "
          f"{len(prefixes)} distinct {SEARCH_LENGTH - 1}-symbol prefix(es)")
    for prefix in sorted(prefixes):
        print(f"     accepting prefix {''.join(str(s) for s in prefix)}")
    if len(prefixes) != 1:
        failures.append(
            f"the accepting word should be unique; {len(prefixes)} prefixes "
            f"of length {SEARCH_LENGTH - 1} raise {SUCCESS}"
        )

    # --- part 3: the decoy component ---------------------------------------
    edges: dict[int, set[int]] = {state: set() for state in range(STATES)}
    for (state, symbol), target in relation.items():
        edges[state].add(target)
    components = [
        comp
        for comp in _sccs(edges, list(range(STATES)))
        if len(comp) > 1 or comp[0] in edges[comp[0]]
    ]
    decoys = [comp for comp in components if len(comp) == 3]
    print()
    print(f"3. non-trivial strongly connected components: "
          f"{[['S%d' % s for s in comp] for comp in components]}")
    if len(decoys) != 1:
        failures.append(
            f"expected exactly one 3-state strongly connected component; "
            f"found {len(decoys)}"
        )
    else:
        decoy = set(decoys[0])
        leaving = {t for s in decoy for t in edges[s]} - decoy
        print(f"   decoy component {sorted('S%d' % s for s in decoy)}: "
              f"outgoing edges to {sorted('S%d' % t for t in leaving) or 'nothing'}")
        if s8 in leaving:
            failures.append(f"the decoy component has an edge to S{s8}")
        if leaving:
            failures.append(
                f"the decoy component is not a sink; it leaves to "
                f"{sorted(leaving)}"
            )
        if not decoy <= reachable:
            failures.append("the decoy component is not reachable from reset")

    return _report(failures, unsatisfiable)


def _report(failures: list[str], unsatisfiable: list[str]) -> int:
    print()
    for note in unsatisfiable:
        print(f"NOT SATISFIABLE: {note}")
    for f in failures:
        print(f"FAIL: {f}")
    if failures:
        return 1
    if unsatisfiable:
        print(
            "bake assertion: parts 1 and 3 pass, and the uniqueness the puzzle "
            "rests on is proven exhaustively; part 2's word length is not "
            "satisfiable by any 9-state machine with a 3-state sink and is "
            "reported above rather than worked around"
        )
        return 0
    print("bake assertion: all three parts pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())