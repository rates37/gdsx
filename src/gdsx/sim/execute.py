"""The Python executor for a `GateTape` -- the ground truth of the two

`tape.py` compiles; this runs. The TypeScript executor in `web/src/sim/` will be
the same loop over the same `Int32Array`, which is why the rules in `tape.py`'s
docstring are repeated here rather than assumed:

* **Values are 0 or 1 only.** Every test below is an explicit `== 1`, never a
  truthiness test. Python and JavaScript disagree about what is truthy, and a
  tape that runs differently in the two is worse than one that does not run.
* **An unused operand slot is `-1` and is never read.** The dispatch arm for
  each opcode touches only the slots its arity covers.

`settle` and `step` reproduce `Simulator.settle` and `Simulator.step` exactly,
including the two settles per step and the async-override precedence (clear
beats preset beats the clock). `tests/test_gate_tape.py` holds them to that over
10,000 random vectors.
"""

from __future__ import annotations

from .tape import DISPATCH, STRIDE, UNUSED, GateTape


class TapeExecutor:
    """Runs a `GateTape`. One design's worth of state, reusable across runs."""

    def __init__(self, tape: GateTape) -> None:
        self.tape = tape
        self.values = [0] * tape.n_nets
        self.state = [0] * tape.n_flops
        # The flat stream decoded once into (function, out, in0..in3) records.
        # The tape itself is still the artifact -- this is only the shape the
        # Python loop can run without slicing, which would allocate per op.
        ops = tape.ops
        self._program = [
            (
                DISPATCH[ops[i]],
                ops[i + 1],
                ops[i + 2],
                ops[i + 3],
                ops[i + 4],
                ops[i + 5],
            )
            for i in range(0, len(ops), STRIDE)
        ]
        self._flops = tuple((f.d, f.q, f.rst, f.set) for f in tape.flops)

    def reset(self) -> None:
        """Every flop to 0, as `Simulator.reset` does"""
        self.state = [0] * self.tape.n_flops

    def settle(self, inputs: dict[str, int]) -> list[int]:
        """Evaluate all combinational logic for the current inputs and state

        Returns the live value array, which the next `settle` overwrites. Copy
        it if you need to keep it.

        A name in `inputs` that is not a net of this design is ignored, and a
        net absent from `inputs` reads 0 -- both matching `Simulator`, where an
        undriven net is a free input defaulting to 0.
        """
        values = self.values
        for i in range(len(values)):
            values[i] = 0
        for net, value in self.tape.consts:
            values[net] = value
        names = self.tape.names
        for name, value in inputs.items():
            index = names.get(name)
            if index is not None:
                values[index] = value
        state = self.state
        for i, (_, q, _, _) in enumerate(self._flops):
            values[q] = state[i]

        # The hot loop. One indexed dispatch per op, no allocation.
        for run, out, a, b, c, d in self._program:
            values[out] = run(values, a, b, c, d)
        return values

    def step(self, inputs: dict[str, int]) -> list[int]:
        """One clock edge. Returns the settled values afterwards.

        Every flop is clocked, exactly as `Simulator.step` does -- the tape's
        `clk` net is recorded but not consulted here. Async clear wins over
        preset, and preset over the data input.
        """
        values = self.settle(inputs)
        nxt = []
        for d, _, rst, preset in self._flops:
            if rst != UNUSED and values[rst] == 1:
                nxt.append(0)
            elif preset != UNUSED and values[preset] == 1:
                nxt.append(1)
            else:
                nxt.append(values[d])
        self.state = nxt
        return self.settle(inputs)

    def run(self, vectors: list[dict[str, int]]) -> list[list[int]]:
        """One `step` per vector; the settled values after each, snapshotted"""
        return [list(self.step(vector)) for vector in vectors]

    #! name-keyed views, for the CLI, the UI and the conformance test

    def state_by_name(self) -> dict[str, int]:
        return dict(zip(self.tape.flop_names, self.state))

    def values_by_name(self) -> dict[str, int]:
        return {name: self.values[i] for name, i in self.tape.names.items()}
