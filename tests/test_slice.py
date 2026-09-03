"""Conformance between a slice and the tape it was cut from

A slice only earns the right to exist if evaluating it is indistinguishable from
evaluating the whole design and reading one net off the end. So the central test
here drives the full `TapeExecutor` and the slice with the same values for the
slice's own free variables and compares the target, over every assignment where
that is affordable and over random ones where it is not.

The rest guard the three ways a slice can be wrong without any assignment
noticing: a constant promoted to a free variable (which doubles the assignment
space and invents impossible counterexamples), a free variable dropped because
nothing downstream happened to read it (which silently renumbers every later bit
position), and the flop-instance-to-Q-net mapping, which is the one place a
caller has to cross between the two ways this codebase names a flop.
"""

from __future__ import annotations

import random

import pytest

from gdsx.core.graph import Graph
from gdsx.sim import TapeExecutor, compile
from gdsx.sim import slice as sl
from gdsx.sim.tape import STRIDE, UNUSED


@pytest.fixture(scope="module")
def puzzle_tape(puzzle_netlist):
    return compile(puzzle_netlist)


def net_names(tape):
    """id -> name, resolved the way `slice` resolves it"""
    by_id = {}
    for name, ident in tape.names.items():
        if ident not in by_id or name < by_id[ident]:
            by_id[ident] = name
    return by_id


def drive_full(tape, assignment, free):
    """Inputs and flop state that give the full tape `assignment` on `free`

    A slice's free variables are a mix of primary inputs and flop Q nets. The
    first are set through `settle`'s inputs; the second only through flop state,
    since the prologue overwrites the value array from state on every settle.
    """
    q_to_flop = {f.q: i for i, f in enumerate(tape.flops)}
    inputs = {}
    state = [0] * tape.n_flops
    for name, value in zip(free, assignment):
        ident = tape.names[name]
        position = q_to_flop.get(ident)
        if position is None:
            inputs[name] = value
        else:
            state[position] = value
    return inputs, state


def test_slice_agrees_with_the_full_tape(puzzle_tape):
    """The whole point: same free values in, same target value out"""
    tape = puzzle_tape
    by_id = net_names(tape)
    executor = TapeExecutor(tape)

    narrow = [s for s in (sl.of(tape, [by_id[f.d]]) for f in tape.flops) if s.n_free <= 10]
    assert len(narrow) >= 10, "expected some cones small enough to exhaust"

    checked = 0
    for sliced in narrow[:20]:
        target = sliced.target_names[0]
        for row in range(sliced.cases):
            assignment = [(row >> i) & 1 for i in range(sliced.n_free)]
            inputs, state = drive_full(tape, assignment, sliced.free)
            executor.state = state
            values = executor.settle(inputs)
            assert sl.run(sliced, assignment) == (values[tape.names[target]],)
            checked += 1
    assert checked > 500


def test_slice_agrees_on_a_wide_cone(puzzle_tape):
    """The same claim where exhaustive is not an option -- sampled instead"""
    tape = puzzle_tape
    by_id = net_names(tape)
    executor = TapeExecutor(tape)
    rng = random.Random(4)

    wide = max(
        (sl.of(tape, [by_id[f.d]]) for f in tape.flops),
        key=lambda s: s.n_free,
    )
    assert wide.n_free >= 8, "expected at least one non-trivial cone in the puzzle"

    target = wide.target_names[0]
    for _ in range(2000):
        assignment = [rng.randint(0, 1) for _ in wide.free]
        inputs, state = drive_full(tape, assignment, wide.free)
        executor.state = state
        values = executor.settle(inputs)
        assert sl.run(wide, assignment) == (values[tape.names[target]],)


def test_free_variables_match_the_structural_support(puzzle_tape, puzzle_netlist):
    """The tape's frontier and the graph's, compared rather than assumed equal

    They agree on this design, which is worth knowing. They are still not the
    same question -- `Graph.support` answers for the netlist and a slice answers
    for the compiled tape -- so `analysis.claims` reports any difference instead
    of picking one.
    """
    tape = puzzle_tape
    graph = Graph.of(puzzle_netlist)
    by_id = net_names(tape)

    for instance, flop in list(zip(tape.flop_names, tape.flops))[:20]:
        sliced = sl.of(tape, [by_id[flop.d]])
        from_tape = set(sliced.free)
        from_graph = set()
        for leaf in graph.d_support(instance):
            if leaf in tape.flop_names:
                from_graph |= set(sl.flop_q_nets(tape, [leaf]))
            else:
                from_graph.add(leaf)
        assert from_tape == from_graph, instance


def test_constants_are_not_free_variables(puzzle_tape):
    """A net the tape ties off is a constant to the slice, never a leaf"""
    tape = puzzle_tape
    by_id = net_names(tape)
    if not tape.consts:
        pytest.skip("this design has no tied-off nets")

    tied = {by_id[ident] for ident, _ in tape.consts if ident in by_id}
    for flop in tape.flops:
        sliced = sl.of(tape, [by_id[flop.d]])
        assert not (set(sliced.free) & tied)


def test_pinned_nets_become_constants(puzzle_tape):
    """Pinning drops a variable and fixes its value, both at once"""
    tape = puzzle_tape
    by_id = net_names(tape)
    sliced = next(
        s
        for s in (sl.of(tape, [by_id[f.d]]) for f in tape.flops)
        if 2 <= s.n_free <= 16
    )
    target = sliced.target_names[0]
    held = sliced.free[0]

    for value in (0, 1):
        pinned = sl.of(tape, [target], pinned={held: value})
        assert held not in pinned.free
        assert pinned.n_free == sliced.n_free - 1

        for row in range(pinned.cases):
            partial = [(row >> i) & 1 for i in range(pinned.n_free)]
            full = []
            spare = iter(partial)
            for name in sliced.free:
                full.append(value if name == held else next(spare))
            assert sl.run(pinned, partial) == sl.run(sliced, full)


def test_requested_free_nets_keep_their_bit_positions(puzzle_tape):
    """`free` is ordered because a claim's assignment order is a contract

    A requested variable is kept even when the walk never reaches it -- dropping
    it would shift every later bit position, and a counterexample decoded against
    a shifted order names the wrong nets.
    """
    tape = puzzle_tape
    by_id = net_names(tape)
    sliced = next(
        s for s in (sl.of(tape, [by_id[f.d]]) for f in tape.flops) if s.n_free >= 3
    )
    target = sliced.target_names[0]

    asked = [sliced.free[2], sliced.free[0]]
    reordered = sl.of(tape, [target], free=asked)
    assert reordered.free[:2] == tuple(asked)
    assert set(reordered.free) == set(sliced.free)

    unreached = sl.of(tape, [target], free=["clk", *asked])
    assert unreached.free[0] == "clk"
    assert unreached.n_free == sliced.n_free + ("clk" not in sliced.free)


def test_stopping_at_a_driven_net_makes_it_free(puzzle_tape):
    """Naming a driven net in `free` cuts the walk there

    This is what a claim about a register group needs: the group's next state as
    a function of its own current state, with the logic above the Q nets excluded
    rather than expanded through.
    """
    tape = puzzle_tape
    by_id = net_names(tape)
    written = {tape.ops[i * STRIDE + 1] for i in range(tape.n_ops)}

    deep, cut = _cone_with_an_internal_net(tape, by_id, written)
    target = deep.target_names[0]

    stopped = sl.of(tape, [target], free=[cut])
    below = sl.of(tape, [cut], free=deep.free)
    assert stopped.free[0] == cut
    assert stopped.n_ops < deep.n_ops, "cutting the walk must remove ops"
    assert set(stopped.free) <= set(deep.free) | {cut}

    # The two agree wherever the cut net takes the value the full cone gives it:
    # a slice stopped at an internal net is not a different circuit, only a
    # differently parameterised one.
    rng = random.Random(77)
    for _ in range(500):
        assignment = [rng.randint(0, 1) for _ in deep.free]
        cut_value = sl.run(below, assignment)[0]
        outer = [
            cut_value if name == cut else assignment[deep.free.index(name)]
            for name in stopped.free
        ]
        assert sl.run(stopped, outer) == sl.run(deep, assignment)


def _cone_with_an_internal_net(tape, by_id, written):
    """A flop-D slice plus one named net strictly inside it, for the test above"""
    for flop in tape.flops:
        target = by_id[flop.d]
        sliced = sl.of(tape, [target])
        if sliced.n_ops < 3 or sliced.n_free > 16:
            continue
        for ident in _cone_ids(tape, target):
            if ident in written and ident in by_id and by_id[ident] != target:
                return sliced, by_id[ident]
    raise AssertionError("no cone in this design has a named internal net")


def _cone_ids(tape, target):
    """Every full-tape net id the cone of `target` reads, for test setup only"""
    writer = {tape.ops[i * STRIDE + 1]: i for i in range(tape.n_ops)}
    seen, stack = set(), [tape.names[target]]
    while stack:
        ident = stack.pop()
        if ident in seen:
            continue
        seen.add(ident)
        record = writer.get(ident)
        if record is None:
            continue
        base = record * STRIDE
        stack.extend(o for o in tape.ops[base + 2 : base + STRIDE] if o != UNUSED)
    return sorted(seen)


def test_slice_of_an_undriven_target_is_empty(puzzle_tape):
    """A primary input sliced to itself: no ops, one free variable, identity"""
    tape = puzzle_tape
    by_id = net_names(tape)
    port = by_id[tape.inputs[0]]
    sliced = sl.of(tape, [port])
    assert sliced.n_ops == 0
    assert sliced.free == (port,)
    assert sl.run(sliced, [0]) == (0,)
    assert sl.run(sliced, [1]) == (1,)


def test_multiple_targets_share_one_slice(puzzle_tape):
    """A register group's next state is one slice with n targets, not n slices"""
    tape = puzzle_tape
    by_id = net_names(tape)
    group = tape.flop_names[:4]
    q_nets = sl.flop_q_nets(tape, group)
    d_nets = [by_id[tape.flops[tape.flop_names.index(f)].d] for f in group]

    together = sl.of(tape, d_nets, free=q_nets)
    assert together.free[: len(q_nets)] == q_nets
    assert len(together.targets) == len(d_nets)

    separately = [sl.of(tape, [net], free=together.free) for net in d_nets]
    rng = random.Random(11)
    for _ in range(200):
        assignment = [rng.randint(0, 1) for _ in together.free]
        expected = tuple(sl.run(one, assignment)[0] for one in separately)
        assert sl.run(together, assignment) == expected


def test_flop_q_nets_crosses_the_two_namings(puzzle_tape):
    tape = puzzle_tape
    by_id = net_names(tape)
    for instance, flop in zip(tape.flop_names[:10], tape.flops[:10]):
        assert sl.flop_q_nets(tape, [instance]) == (by_id[flop.q],)
    with pytest.raises(sl.UnknownNet):
        sl.flop_q_nets(tape, ["no_such_flop"])


def test_unknown_net_is_reported_not_guessed(puzzle_tape):
    with pytest.raises(sl.UnknownNet):
        sl.of(puzzle_tape, ["no_such_net"])


def test_slice_ops_stay_in_tape_order(puzzle_tape):
    """One forward pass has to remain correct: no operand read before it is written"""
    tape = puzzle_tape
    by_id = net_names(tape)
    for flop in tape.flops[:30]:
        sliced = sl.of(tape, [by_id[flop.d]])
        written = set(sliced.free_ids) | {i for i, _ in sliced.consts}
        for record in range(sliced.n_ops):
            base = record * STRIDE
            for operand in sliced.ops[base + 2 : base + STRIDE]:
                if operand != UNUSED:
                    assert operand in written
            written.add(sliced.ops[base + 1])


def test_slice_is_deterministic(puzzle_tape):
    """Two cuts of the same question are the same slice, bit positions included"""
    tape = puzzle_tape
    by_id = net_names(tape)
    target = by_id[tape.flops[3].d]
    assert sl.of(tape, [target]) == sl.of(tape, [target])