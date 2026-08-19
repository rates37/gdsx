"""Cutting a `GateTape` down to the ops one question actually needs

The notebook's claim verification evaluates a cone over every assignment of its
leaves -- up to 2**24 of them. Running the whole design's op stream that many
times is not close to affordable, and it is also not what the question asks: a
claim about one flop's D pin depends on a few dozen ops out of a few thousand.

`of()` returns those few dozen as a **self-contained mini-tape**: its own dense
net numbering, its own constants, and the exact list of free variables it reads.
`run()` executes one, and is the ground truth the TypeScript evaluator in
`web/src/notebook/` is held to by `tests/golden/slice-*.json`, the same way
`execute.TapeExecutor` is the ground truth for the TypeScript tape executor.

Two things about this module are deliberate and worth not undoing:

**The frontier comes from the op stream, not from the netlist.** `Graph.support`
answers "what does this net depend on" for the *netlist*; the tape is a compiled
artifact that folds constants and invents temporaries, so the set of variables it
genuinely reads is not always the same set. Since the tape is what gets
evaluated, deriving the leaves from anywhere else would let a claim be reported
"proven over 22 variables" while the evaluator actually read 21 of them and one
constant. Callers that want to compare the two answers can -- `analysis.claims`
does, and reports the difference rather than reconciling it silently.

**A constant is not a free variable.** A net seeded by `GateTape.consts` (or
pinned by the caller) stops the walk as a constant, never as a leaf. Treating a
tied-off net as free would double the assignment space and invent counterexamples
that cannot occur.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .tape import DISPATCH, STRIDE, UNUSED, GateTape


class UnknownNet(KeyError):
    """A net name that this tape does not have an id for"""

    def __init__(self, net: str) -> None:
        self.net = net
        super().__init__(f"no net named {net!r} in this tape")


@dataclass(frozen=True)
class Slice:
    """The ops needed to compute `targets` from `free`, renumbered to itself

    Every id in `ops`, `free_ids`, `targets` and `consts` indexes a value array
    of length `n_values`, dense and local to this slice -- nothing here indexes
    the tape it came from. `free[i]` is the name of the variable at `free_ids[i]`
    and `i` is its bit position in an assignment, so the order is the contract
    between whoever built the slice and whoever evaluates it.
    """

    ops: tuple[int, ...]  # flat, stride 6, same encoding and order as GateTape
    free: tuple[str, ...]  # free variable names, in assignment-bit order
    free_ids: tuple[int, ...]  # parallel to `free`
    targets: tuple[int, ...]  # parallel to `target_names`
    target_names: tuple[str, ...]
    consts: tuple[tuple[int, int], ...]  # (id, 0|1), seeded before the ops run
    n_values: int

    @property
    def n_ops(self) -> int:
        return len(self.ops) // STRIDE

    @property
    def n_free(self) -> int:
        return len(self.free)

    @property
    def cases(self) -> int:
        """How many assignments an exhaustive sweep would visit"""
        return 1 << len(self.free)

    def to_dict(self) -> dict:
        return {
            "ops": list(self.ops),
            "free": list(self.free),
            "free_ids": list(self.free_ids),
            "targets": list(self.targets),
            "target_names": list(self.target_names),
            "consts": [list(pair) for pair in self.consts],
            "n_values": self.n_values,
        }


def of(
    tape: GateTape,
    targets: Sequence[str],
    *,
    free: Sequence[str] = (),
    pinned: Mapping[str, int] | None = None,
) -> Slice:
    """The slice of `tape` that computes `targets`

    `free` names variables the walk must stop at even when the tape drives them
    -- how a claim about a register group asks for its next state as a function
    of its *own* current state, by naming the group's Q nets. They take the first
    bit positions, in the order given. Anything else the walk finds unwritten is
    a free variable too and follows, in net-name order; a caller that wanted
    those held instead should pass them in `pinned`.

    `pinned` holds nets at a fixed value, folded in as constants exactly like the
    tape's own. `free` wins over `pinned` if a net is somehow in both, since one
    is a request to vary it and the other only a default.
    """
    names = tape.names
    by_id: dict[int, str] = {}
    for name, ident in names.items():
        # Deterministic when two names share an id, which the compiler does not
        # currently produce but which must not make a slice order-dependent.
        if ident not in by_id or name < by_id[ident]:
            by_id[ident] = name

    def resolve(net: str) -> int:
        ident = names.get(net)
        if ident is None:
            raise UnknownNet(net)
        return ident

    target_ids = [resolve(net) for net in targets]
    free_requested = [resolve(net) for net in free]
    frontier = set(free_requested)

    held: dict[int, int] = {ident: value for ident, value in tape.consts}
    for net, value in (pinned or {}).items():
        held[resolve(net)] = value
    for ident in frontier:
        held.pop(ident, None)

    # out id -> index into the op records. The tape settles in one forward pass,
    # so where a net is written more than once the last write is the one that
    # survives, and that is the op the slice must keep.
    ops = tape.ops
    writer: dict[int, int] = {}
    for record in range(len(ops) // STRIDE):
        writer[ops[record * STRIDE + 1]] = record

    keep: set[int] = set()
    reached: set[int] = set(target_ids)
    discovered_free: set[int] = set()
    stack = list(target_ids)
    while stack:
        ident = stack.pop()
        if ident in frontier or ident in held:
            continue
        record = writer.get(ident)
        if record is None:
            # Never written and not held: a primary input, or a net the layout
            # left undriven. Either way the evaluator has to be told its value.
            discovered_free.add(ident)
            continue
        if record in keep:
            continue
        keep.add(record)
        base = record * STRIDE
        for slot in range(2, STRIDE):
            operand = ops[base + slot]
            if operand == UNUSED:
                continue
            reached.add(operand)
            stack.append(operand)

    # `free` first, in the order asked for, so bit positions stay where the
    # caller put them; then whatever else turned out to be free, by name.
    ordered_free = list(free_requested)
    seen_free = set(ordered_free)
    for ident in sorted(discovered_free, key=lambda i: by_id.get(i, f"#{i}")):
        if ident not in seen_free:
            ordered_free.append(ident)
            seen_free.add(ident)

    # A requested free variable the walk never reached is still a variable of
    # this slice: it just does not happen to matter. Keeping it preserves the
    # caller's bit positions, which is the whole reason `free` is ordered.
    for ident in ordered_free:
        reached.add(ident)
    used_consts = sorted((i, v) for i, v in held.items() if i in reached)
    for ident, _ in used_consts:
        reached.add(ident)

    dense: dict[int, int] = {}
    for record in sorted(keep):
        base = record * STRIDE
        for slot in range(1, STRIDE):
            operand = ops[base + slot]
            if operand != UNUSED and operand not in dense:
                dense[operand] = len(dense)
    for ident in ordered_free + [i for i, _ in used_consts] + target_ids:
        if ident not in dense:
            dense[ident] = len(dense)

    out: list[int] = []
    for record in sorted(keep):
        base = record * STRIDE
        out.append(ops[base])
        for slot in range(1, STRIDE):
            operand = ops[base + slot]
            out.append(UNUSED if operand == UNUSED else dense[operand])

    return Slice(
        ops=tuple(out),
        free=tuple(by_id.get(i, f"#{i}") for i in ordered_free),
        free_ids=tuple(dense[i] for i in ordered_free),
        targets=tuple(dense[i] for i in target_ids),
        target_names=tuple(targets),
        consts=tuple((dense[i], v) for i, v in used_consts),
        n_values=len(dense),
    )


def run(sliced: Slice, assignment: Sequence[int]) -> tuple[int, ...]:
    """Evaluate `sliced` for one assignment of its free variables

    `assignment[i]` is the value of `sliced.free[i]`, 0 or 1. The Python side of
    the two-evaluator contract: deliberately the plain indexed loop
    `execute.TapeExecutor.settle` is, so that the only thing the TypeScript
    evaluator has to reproduce is arithmetic, not a different shape of program.
    """
    if len(assignment) != len(sliced.free):
        raise ValueError(
            f"expected {len(sliced.free)} values, got {len(assignment)}"
        )
    values = [0] * sliced.n_values
    for ident, value in sliced.consts:
        values[ident] = value
    for i, ident in enumerate(sliced.free_ids):
        values[ident] = assignment[i]
    ops = sliced.ops
    for base in range(0, len(ops), STRIDE):
        values[ops[base + 1]] = DISPATCH[ops[base]](
            values, ops[base + 2], ops[base + 3], ops[base + 4], ops[base + 5]
        )
    return tuple(values[ident] for ident in sliced.targets)


def table(sliced: Slice) -> tuple[tuple[int, ...], ...]:
    """`run` over every assignment, low bit of the row index being `free[0]`

    Only sane for a slice with few free variables; the callers that need 2**20 of
    these use the bit-parallel evaluator in the browser. This exists for tests
    and for the golden.
    """
    rows = []
    width = len(sliced.free)
    for row in range(1 << width):
        rows.append(run(sliced, [(row >> i) & 1 for i in range(width)]))
    return tuple(rows)


def flop_q_nets(tape: GateTape, instances: Sequence[str]) -> tuple[str, ...]:
    """The Q net name of each named flop instance

    `Graph.support` reports flop leaves as **instance** names while a slice is
    keyed on **net** names, and going between the two by hand is a reliable way
    to end up with a cone whose leaves are half instances. This is that mapping,
    in one place: instance -> index in `flop_names` -> `flops[i].q` -> net name.
    """
    index = {name: i for i, name in enumerate(tape.flop_names)}
    by_id: dict[int, str] = {}
    for name, ident in tape.names.items():
        if ident not in by_id or name < by_id[ident]:
            by_id[ident] = name
    found = []
    for instance in instances:
        position = index.get(instance)
        if position is None:
            raise UnknownNet(instance)
        q = tape.flops[position].q
        if q not in by_id:
            raise UnknownNet(f"{instance} (its Q net is unnamed)")
        found.append(by_id[q])
    return tuple(found)