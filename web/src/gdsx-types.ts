// GENERATED FILE -- do not edit by hand.
// Run `uv run python scripts/gen_types.py` to regenerate.
// Source: src/gdsx/api.py (contract: docs/game/game-plan.md §4b)

export interface ChoiceOption {
  literals: LeafValue[];
}

export interface ChoiceView {
  net: string;
  value: number;
  options: ChoiceOption[];
}

export interface ClaimPlanView {
  kind: string;
  call: string;
  verdict: VerdictView | null;
  job: JobView | null;
  /** assumptions the plan was built under -- what was held at a fixed value, which cycle the claim turned out to be about. Show these: a claim checked under assumptions is only honest if it says which */
  notes: string[];
}

export interface ConeNode {
  net: string;
  gate: GateView | null;
  pins: Record<string, string>;
  leaf: string | null;
  children: ConeNode[];
  /** true = there is more below and the walk stopped. Never inferred from an empty `children` list -- treating a truncated node as a leaf is how a player wrongly concludes a net is a primary input */
  truncated: boolean;
}

export interface ConstraintView {
  name: string;
  elements: number[];
  lb: number;
  ub: number;
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

export interface JobView {
  engine: string;
  /** what to compare once the slices are evaluated. The BUDGET is not here on purpose: how many assignments to visit, and whether the answer may be called proven, is one policy that lives with the evaluator */
  check: string;
  design: SliceView | null;
  claim: SliceView | null;
  predicate: PredicateView | null;
}

export interface LeafValue {
  net: string;
  value: number;
}

export interface NetView {
  name: string;
  driver: RefView | null;
  readers: RefView[];
  is_port: string | null;
  leaf: string | null;
}

export interface OrbitView {
  states: number[][];
  kind: string;
}

export interface PredicateView {
  node: string;
  of: PredicateView[];
  measure: string;
  port: string;
  window: number[] | null;
  op: string;
  value: number;
}

export interface RefView {
  instance: string;
  pin: string;
  cell: string;
  direction: string;
}

export interface RequirementsView {
  net: string;
  value: number;
  consistent: boolean;
  leaves: LeafValue[];
  choices: ChoiceView[];
  conflicts: string[];
}

export interface SliceView {
  /** flat, stride 6, the same encoding as `TapeView.ops` -- but renumbered to this slice, so every id indexes a value array of `n_values`, NOT the design's */
  ops: number[];
  /** the free variables, in assignment-bit order: `free[i]` is the net at `free_ids[i]` and `i` is its bit position. Two slices of one claim share this list exactly, which is what makes a counterexample decode to the same nets on both sides */
  free: string[];
  free_ids: number[];
  targets: number[];
  target_names: string[];
  consts: number[][];
  n_values: number;
}

export interface StickyView {
  flop: string;
  polarity: number;
  condition: string;
}

export interface SystemView {
  variables: number[];
  watched: string[];
  constraints: ConstraintView[];
  unconstrained: string[];
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

export interface VerdictView {
  /** never "LIKELY" -- nothing on the Python side samples anything, and a sampled result is not a proof */
  kind: string;
  method: string;
  cases: number;
  reason: string;
  observed: string[];
  expected: string[];
}

export interface WeightView {
  value: number;
  confidence: string;
}
