// GENERATED FILE -- do not edit by hand.
// Run `uv run python scripts/gen_types.py` to regenerate.
// Source: src/gdsx/api.py (contract: docs/game/game-plan.md §4b)

export interface ConeNode {
  net: string;
  gate: GateView | null;
  pins: Record<string, string>;
  leaf: string | null;
  children: ConeNode[];
  /** true = there is more below and the walk stopped. Never inferred from an empty `children` list -- treating a truncated node as a leaf is how a player wrongly concludes a net is a primary input */
  truncated: boolean;
}

export interface FlopView {
  d: number;
  q: number;
  clk: number;
  /** always ACTIVE HIGH whatever the cell's polarity -- the compiler emits the inversion as a NOT op. -1 = the cell has no such pin */
  rst: number;
  set: number;
  kind: number;
}

export interface GateView {
  instance: string;
  cell: string;
  fn: string;
}

export interface InstanceView {
  name: string;
  /** the FULL sky130 name, e.g. "sky130_fd_sc_hd__dfrtp_2" */
  cell: string;
  /** what the Liberty library is keyed on, e.g. "dfrtp" -- a DIFFERENT string from `cell`, confusing them produces "cell not found" for every instance */
  base_cell: string;
  generic: string;
  /** pin -> net. Pins the layout did not connect are ABSENT, not zero -- always guard with `pin in connections` */
  connections: Record<string, string>;
  is_sequential: boolean;
  functions: Record<string, string>;
  bbox: number[] | null;
}

export interface NetView {
  name: string;
  driver: RefView | null;
  readers: RefView[];
  is_port: string | null;
  leaf: string | null;
}

export interface RefView {
  instance: string;
  pin: string;
  cell: string;
  direction: string;
}

export interface TapeView {
  tape_version: number;
  n_nets: number;
  n_flops: number;
  n_ops: number;
  inputs: number[];
  /** flat, stride 6: [opcode, out, in0, in1, in2, in3] per op, topologically ordered. -1 in an operand slot means the opcode does not read it. Values are 0 or 1 ONLY -- never test truthiness */
  ops: number[];
  flops: FlopView[];
  /** [net id, 0|1] pairs seeded into the value array BEFORE the caller's inputs and before the op stream runs */
  consts: number[][];
  names: Record<string, number>;
  flop_names: string[];
}
