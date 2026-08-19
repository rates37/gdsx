// The TypeScript half of "one compiler, two executors" (gdsx.sim.tape /
// gdsx.sim.execute). Runs the same Int32Array op stream the Python
// `TapeExecutor` runs, opcode for opcode -- that module's docstring states
// the rules that keep the two from ever disagreeing, repeated here rather
// than assumed:
//
//   * Values are 0 or 1 only. Every comparison below is explicit (`=== 1`),
//     never truthiness -- Python and JavaScript disagree about what else is
//     truthy, and a tape that runs differently in the two is worse than one
//     that does not run at all.
//   * An unused operand slot is -1 and is never read; each opcode's arm only
//     touches the slots its arity covers.
//
// `settle`/`step` reproduce `Simulator.settle`/`Simulator.step` exactly: two
// settles per step, and clear beats preset beats the data input. Held to
// that -- and to the Python executor -- by the golden trace in
// tests/golden/trace-*.json; see web/scripts/test-sim-golden.mjs.

import type { FlopDef, GateTape } from "./tape.ts";

const STRIDE = 6;
const UNUSED = -1;

type Dispatch = (v: Int32Array, a: number, b: number, c: number, d: number) => number;

/** DISPATCH[opcode] -- mirrors gdsx.sim.tape.SEMANTICS exactly, same order as `Op`. */
const DISPATCH: Dispatch[] = [
  /* CONST0 */ () => 0,
  /* CONST1 */ () => 1,
  /* BUF    */ (v, a) => v[a],
  /* NOT    */ (v, a) => 1 - v[a],
  /* AND2   */ (v, a, b) => v[a] & v[b],
  /* OR2    */ (v, a, b) => v[a] | v[b],
  /* XOR2   */ (v, a, b) => v[a] ^ v[b],
  /* NAND2  */ (v, a, b) => 1 - (v[a] & v[b]),
  /* NOR2   */ (v, a, b) => 1 - (v[a] | v[b]),
  /* XNOR2  */ (v, a, b) => 1 - (v[a] ^ v[b]),
  /* AND3   */ (v, a, b, c) => v[a] & v[b] & v[c],
  /* OR3    */ (v, a, b, c) => v[a] | v[b] | v[c],
  /* AND4   */ (v, a, b, c, d) => v[a] & v[b] & v[c] & v[d],
  /* OR4    */ (v, a, b, c, d) => v[a] | v[b] | v[c] | v[d],
  // `s ? b : a`, with s in slot 2 -- an explicit comparison, not truthiness.
  /* MUX2   */ (v, a, b, c) => (v[c] === 1 ? v[b] : v[a]),
  /* AOI21  */ (v, a, b, c) => 1 - ((v[a] & v[b]) | v[c]),
  /* OAI21  */ (v, a, b, c) => 1 - ((v[a] | v[b]) & v[c]),
  /* AO21   */ (v, a, b, c) => (v[a] & v[b]) | v[c],
  /* OA21   */ (v, a, b, c) => (v[a] | v[b]) & v[c],
  /* AOI22  */ (v, a, b, c, d) => 1 - ((v[a] & v[b]) | (v[c] & v[d])),
  /* OAI22  */ (v, a, b, c, d) => 1 - ((v[a] | v[b]) & (v[c] | v[d])),
  /* AO22   */ (v, a, b, c, d) => (v[a] & v[b]) | (v[c] & v[d]),
  /* OA22   */ (v, a, b, c, d) => (v[a] | v[b]) & (v[c] | v[d]),
];

export type Inputs = Readonly<Record<string, number>>;

/** Runs a `GateTape`. One design's worth of state, reusable across runs. */
export class GateTapeExecutor {
  readonly tape: GateTape;
  readonly values: Int32Array;
  state: Int32Array;
  private readonly flops: readonly FlopDef[];

  constructor(tape: GateTape) {
    this.tape = tape;
    this.values = new Int32Array(tape.header.n_nets);
    this.state = new Int32Array(tape.header.n_flops);
    this.flops = tape.header.flops;
  }

  /** Every flop to 0, as `Simulator.reset`/`TapeExecutor.reset` do. */
  reset(): void {
    this.state.fill(0);
  }

  /**
   * Evaluate all combinational logic for the current inputs and state.
   * Returns the live value array, which the next `settle` overwrites --
   * copy it if the caller needs to keep it.
   *
   * A name in `inputs` that is not a net of this design is ignored, and a
   * net absent from `inputs` reads 0 -- both match `Simulator`, where an
   * undriven net is a free input defaulting to 0.
   */
  settle(inputs: Inputs): Int32Array {
    const { values, tape } = this;
    values.fill(0);
    for (const [net, value] of tape.header.consts) values[net] = value;
    const names = tape.header.names;
    for (const name in inputs) {
      const idx = names[name];
      if (idx !== undefined) values[idx] = inputs[name];
    }
    const flops = this.flops;
    const state = this.state;
    for (let i = 0; i < flops.length; i++) values[flops[i].q] = state[i];

    const ops = tape.ops;
    for (let i = 0; i < ops.length; i += STRIDE) {
      const run = DISPATCH[ops[i]];
      values[ops[i + 1]] = run(values, ops[i + 2], ops[i + 3], ops[i + 4], ops[i + 5]);
    }
    return values;
  }

  /**
   * One clock edge. Returns the settled values afterwards.
   *
   * Every flop is clocked, exactly as `Simulator.step` does -- the tape's
   * `clk` net is recorded but not consulted here. Async clear wins over
   * preset, and preset over the data input.
   */
  step(inputs: Inputs): Int32Array {
    const values = this.settle(inputs);
    const flops = this.flops;
    const next = new Int32Array(flops.length);
    for (let i = 0; i < flops.length; i++) {
      const f = flops[i];
      if (f.rst !== UNUSED && values[f.rst] === 1) next[i] = 0;
      else if (f.set !== UNUSED && values[f.set] === 1) next[i] = 1;
      else next[i] = values[f.d];
    }
    this.state = next;
    return this.settle(inputs);
  }

  /** One `step` per vector; the settled values after each, snapshotted. */
  run(vectors: readonly Inputs[]): Int32Array[] {
    return vectors.map((vector) => this.step(vector).slice());
  }

  // ---- name-keyed views, for the UI and the conformance test --------------

  stateByName(): Record<string, number> {
    const out: Record<string, number> = {};
    const names = this.tape.header.flop_names;
    for (let i = 0; i < names.length; i++) out[names[i]] = this.state[i];
    return out;
  }

  valuesByName(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [name, idx] of Object.entries(this.tape.header.names)) {
      out[name] = this.values[idx];
    }
    return out;
  }
}