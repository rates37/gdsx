"""Conformance between the gate tape and the simulator it replaces

The tape only earns the right to exist if it is indistinguishable from
`Simulator`, so the central test here drives both engines with the same 10,000
random vectors and compares **every net and every flop after every cycle**, not
just the ports at the end.

The other tests guard the two ways a tape can be wrong without any vector
noticing: an opcode renumbered (a tape is a versioned binary artifact, and an
old tape read against a new table is silently a different circuit), and a cell
whose decomposition does not actually reproduce its Liberty function.
"""

from __future__ import annotations

import random

import pytest

from gdsx.core.netlist import Instance, Netlist
from gdsx.liberty import library, variables
from gdsx.sim import Simulator, TapeExecutor, compile
from gdsx.sim.tape import ARITY, DISPATCH, SEMANTICS, STRIDE, Op, _recipe_plan, match

# The opcode numbers, frozen.
# Written out longhand on purpose: this test exists to fail if a number ever
# moves. New opcodes are APPENDED and this dict grows; an existing entry
# changing value means every recorded tape and every cached bundle now decodes
# to a different circuit.
FROZEN = {
    "CONST0": 0,
    "CONST1": 1,
    "BUF": 2,
    "NOT": 3,
    "AND2": 4,
    "OR2": 5,
    "XOR2": 6,
    "NAND2": 7,
    "NOR2": 8,
    "XNOR2": 9,
    "AND3": 10,
    "OR3": 11,
    "AND4": 12,
    "OR4": 13,
    "MUX2": 14,
    "AOI21": 15,
    "OAI21": 16,
    "AO21": 17,
    "OA21": 18,
    "AOI22": 19,
    "OAI22": 20,
    "AO22": 21,
    "OA22": 22,
}


def test_opcode_numbers_are_frozen():
    assert {op.name: int(op) for op in Op} == FROZEN


def test_every_opcode_has_arity_and_semantics():
    assert set(ARITY) == set(Op)
    assert set(SEMANTICS) == set(Op)
    assert len(DISPATCH) == len(Op)
    assert all(ARITY[op] <= STRIDE - 2 for op in Op)


#! every cell in the library, against its Liberty function


def single_cell_netlist(base: str, cell) -> Netlist:
    """A netlist of one instance of `base`, every pin brought out to a port"""
    connections = {pin: f"in_{pin}" for pin in cell.inputs}
    connections |= {pin: f"out_{pin}" for pin in cell.functions}
    nl = Netlist(top=f"only_{base}")
    nl.instances = [Instance("u0", f"sky130_fd_sc_hd__{base}_1", connections)]
    for pin, net in connections.items():
        nl.nets.setdefault(net, []).append(f"u0/{pin}")
    nl.ports = {f"in_{pin}": "input" for pin in cell.inputs}
    nl.ports |= {f"out_{pin}": "output" for pin in cell.functions}
    return nl


def combinational_cells():
    return sorted(
        (name, cell)
        for name, cell in library().items()
        if cell.has_behaviour and not cell.is_sequential and cell.inputs
    )


@pytest.mark.parametrize("base,cell", combinational_cells(), ids=lambda x: x)
def test_compiled_ops_reproduce_the_liberty_function(base, cell):
    """Run the emitted ops over every input combination and compare

    This checks what the tape actually contains, not what the recipe table
    claims: a mis-emitted operand order fails here even when the recipe itself
    is right. Cells have at most six inputs, so 64 rows is exhaustive.
    """
    executor = TapeExecutor(compile(single_cell_netlist(base, cell)))
    pins = cell.inputs
    for row in range(1 << len(pins)):
        bits = {pin: (row >> i) & 1 for i, pin in enumerate(pins)}
        executor.settle({f"in_{pin}": value for pin, value in bits.items()})
        values = executor.values_by_name()
        got = {pin: values[f"out_{pin}"] for pin in cell.functions}
        assert got == cell.evaluate(bits), f"{base} at {bits}"


def test_no_combinational_cell_needs_approximating():
    """Every cell is covered by one opcode or by a verified decomposition

    If this fails, the compiler is about to raise on a real design. The fix is
    a new recipe or a new opcode APPENDED to the table.
    """
    uncovered = []
    for base, cell in combinational_cells():
        for pin, expr in cell.functions.items():
            order = tuple(sorted(variables(expr)))
            if _recipe_plan(base, pin, expr, order) is None:
                if match(expr, order) is None:
                    uncovered.append(f"{base}.{pin}")
    assert uncovered == []


#! the samples, against the simulator


def input_ports(nl: Netlist) -> list[str]:
    return sorted(net for net, direction in nl.ports.items() if direction == "input")


def vectors_for(nl: Netlist, count: int, seed: int) -> list[dict[str, int]]:
    ports = input_ports(nl)
    rng = random.Random(seed)
    return [{port: rng.randint(0, 1) for port in ports} for _ in range(count)]


def assert_engines_agree(nl: Netlist, vectors: list[dict[str, int]]) -> None:
    """Drive both engines with `vectors`, comparing after every cycle

    Compares the full net array and the full flop state, and reports the first
    divergence with its cycle and net rather than a bare count.
    """
    simulator = Simulator(nl)
    executor = TapeExecutor(compile(nl))
    simulator.reset()
    executor.reset()

    for cycle, vector in enumerate(vectors):
        expected = simulator.step(vector)
        executor.step(vector)
        actual = executor.values_by_name()

        wrong = [net for net in nl.nets if expected.get(net, 0) != actual[net]]
        assert not wrong, (
            f"cycle {cycle}: {len(wrong)} net(s) differ, first is {wrong[0]!r} "
            f"-- simulator says {expected.get(wrong[0], 0)}, "
            f"tape says {actual[wrong[0]]}"
        )
        held = executor.state_by_name()
        drifted = [
            name for name, value in simulator.state.items() if held[name] != value
        ]
        assert not drifted, (
            f"cycle {cycle}: flop {drifted[0]!r} holds "
            f"{simulator.state[drifted[0]]} in the simulator, "
            f"{held[drifted[0]]} in the tape"
        )


def test_ten_thousand_vectors_match_the_simulator(sample_netlist):
    assert_engines_agree(sample_netlist, vectors_for(sample_netlist, 10_000, seed=11))


def test_puzzle_matches_the_simulator(puzzle_netlist):
    assert_engines_agree(puzzle_netlist, vectors_for(puzzle_netlist, 500, seed=12))


@pytest.mark.slow
def test_puzzle_ten_thousand_vectors_match_the_simulator(puzzle_netlist):
    assert_engines_agree(puzzle_netlist, vectors_for(puzzle_netlist, 10_000, seed=13))


def test_every_sample_instance_compiles(sample_netlist, puzzle_netlist):
    for nl in (sample_netlist, puzzle_netlist):
        tape = compile(nl)
        assert tape.n_flops == sum(1 for _ in Simulator(nl).flops)
        assert tape.n_ops > 0
        assert set(tape.names) == set(nl.nets)


#! the artifact itself


def test_tape_shape(sample_netlist):
    tape = compile(sample_netlist)
    assert len(tape.ops) == tape.n_ops * STRIDE
    assert len(tape.to_bytes()) == len(tape.ops) * 4
    assert all(0 <= tape.ops[i] < len(Op) for i in range(0, len(tape.ops), STRIDE))
    # every operand is a real net id or the unused marker
    assert all(-1 <= value < tape.n_nets for value in tape.ops)
    assert tape.tape_version >= 1


def test_reset_clears_state(sample_netlist):
    executor = TapeExecutor(compile(sample_netlist))
    executor.run(vectors_for(sample_netlist, 20, seed=5))
    executor.reset()
    assert executor.state == [0] * executor.tape.n_flops


def test_compilation_is_deterministic(sample_netlist):
    assert compile(sample_netlist).to_dict() == compile(sample_netlist).to_dict()
