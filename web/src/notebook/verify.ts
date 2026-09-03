// The evaluator: a `SliceView` run over many assignments at once.
//
// Every opcode in the tape's table is bitwise (gdsx.sim.tape.SEMANTICS), so
// nothing stops us running 32 assignments in parallel by putting one assignment
// in each bit of an Int32. That is what makes the verification budget in
// game-plan.md §5 reachable: 2**20 assignments become 32,768 word-iterations
// over a slice of a few dozen ops, not 1,048,576 passes over 900.
//
// Two things this file owns, and nothing else may hold a second copy of:
//
//   * `budget()` -- how many assignments to visit and whether the result may be
//     called PROVEN. The Python side deliberately does not decide this
//     (gdsx/analysis/claims.py says so), so there is exactly one copy.
//   * The opcode semantics, in bit-parallel form. They mirror
//     `web/src/sim/executor.ts` opcode for opcode, and the golden tables in
//     tests/golden/slice-*.json hold the two evaluators together the same way
//     tests/golden/trace-*.json holds the two tape executors together.
//
// The one place bit-parallel differs from scalar and it matters: CONST1 is
// `~0`, every lane set, not `1`. A `1` here would set lane 0 and clear the
// other 31, which is a different circuit that happens to agree on one lane.

import type { SliceView } from "../gdsx-types.ts";
import type { Verdict, Vector } from "./model.ts";

const STRIDE = 6;

export const LANES = 32;

/** At most this many free variables and we can still visit every assignment. */
export const EXHAUSTIVE_CEILING = 24;
/** Up to here it is fast enough to run without a progress bar. */
export const INLINE_CEILING = 20;
/** Above the ceiling: how many random vectors buy a LIKELY. */
export const SAMPLE_VECTORS = 10000;

export interface Budget {
  method: "exhaustive" | "sampled";
  cases: number;
  /** false = worth a progress bar and a worker; see game-plan.md §5's table. */
  inline: boolean;
  /** The verdict when no counterexample turns up. Never PROVEN for sampling. */
  clean: "PROVEN" | "LIKELY";
}

/**
 * game-plan.md §5's verification budget table, implemented literally.
 *
 * | free vars | method                 | clean verdict |
 * |-----------|------------------------|---------------|
 * | <= 20     | exhaustive, inline     | PROVEN        |
 * | 21-24     | exhaustive, w/ progress| PROVEN        |
 * | > 24      | 10k sampled            | LIKELY        |
 *
 * A counterexample at any size is DISPROVEN: sampling cannot prove a claim, but
 * it absolutely can disprove one, and that asymmetry is the useful half.
 */
export function budget(freeCount: number): Budget {
  if (freeCount > EXHAUSTIVE_CEILING) {
    return { method: "sampled", cases: SAMPLE_VECTORS, inline: false, clean: "LIKELY" };
  }
  return {
    method: "exhaustive",
    cases: 2 ** freeCount,
    inline: freeCount <= INLINE_CEILING,
    clean: "PROVEN",
  };
}

// ---- the machine --------------------------------------------------------

type Word = number;
type Dispatch = (v: Int32Array, a: number, b: number, c: number, d: number) => Word;

/** Mirrors DISPATCH in web/src/sim/executor.ts, one lane per bit. */
const DISPATCH: Dispatch[] = [
  /* CONST0 */ () => 0,
  /* CONST1 */ () => ~0,
  /* BUF    */ (v, a) => v[a],
  /* NOT    */ (v, a) => ~v[a],
  /* AND2   */ (v, a, b) => v[a] & v[b],
  /* OR2    */ (v, a, b) => v[a] | v[b],
  /* XOR2   */ (v, a, b) => v[a] ^ v[b],
  /* NAND2  */ (v, a, b) => ~(v[a] & v[b]),
  /* NOR2   */ (v, a, b) => ~(v[a] | v[b]),
  /* XNOR2  */ (v, a, b) => ~(v[a] ^ v[b]),
  /* AND3   */ (v, a, b, c) => v[a] & v[b] & v[c],
  /* OR3    */ (v, a, b, c) => v[a] | v[b] | v[c],
  /* AND4   */ (v, a, b, c, d) => v[a] & v[b] & v[c] & v[d],
  /* OR4    */ (v, a, b, c, d) => v[a] | v[b] | v[c] | v[d],
  // `s ? b : a`, lane by lane. The scalar executor writes this as a comparison
  // on `s`; with 32 different `s` in one word there is nothing to compare, so
  // it is the select written out.
  /* MUX2   */ (v, a, b, c) => (v[a] & ~v[c]) | (v[b] & v[c]),
  /* AOI21  */ (v, a, b, c) => ~((v[a] & v[b]) | v[c]),
  /* OAI21  */ (v, a, b, c) => ~((v[a] | v[b]) & v[c]),
  /* AO21   */ (v, a, b, c) => (v[a] & v[b]) | v[c],
  /* OA21   */ (v, a, b, c) => (v[a] | v[b]) & v[c],
  /* AOI22  */ (v, a, b, c, d) => ~((v[a] & v[b]) | (v[c] & v[d])),
  /* OAI22  */ (v, a, b, c, d) => ~((v[a] | v[b]) & (v[c] | v[d])),
  /* AO22   */ (v, a, b, c, d) => (v[a] & v[b]) | (v[c] & v[d]),
  /* OA22   */ (v, a, b, c, d) => (v[a] | v[b]) & (v[c] | v[d]),
];

/** Column patterns for the first five variables of an exhaustive sweep: in
 *  block `b`, lane `j` is row `b * 32 + j`, so bit `i` of that row is
 *  `(j >> i) & 1` for i < 5 and depends only on `b` above that. */
const MAGIC = [0xaaaaaaaa | 0, 0xcccccccc | 0, 0xf0f0f0f0 | 0, 0xff00ff00 | 0, 0xffff0000 | 0];

/** Runs one slice over blocks of 32 assignments. Reusable across blocks. */
export class SliceRunner {
  readonly slice: SliceView;
  readonly values: Int32Array;
  private readonly ops: Int32Array;
  private readonly consts: readonly (readonly number[])[];

  // Fields written out longhand rather than as constructor parameter
  // properties: the conformance test runs this file under Node's strip-only
  // type stripping, which does not support them. Same reason web/src/sim/
  // does it this way.
  constructor(slice: SliceView) {
    this.slice = slice;
    this.values = new Int32Array(slice.n_values);
    this.ops = Int32Array.from(slice.ops);
    this.consts = slice.consts;
  }

  /** Evaluate one block. `columns[i]` is the 32-lane word for `slice.free[i]`. */
  run(columns: Int32Array): Int32Array {
    const values = this.values;
    values.fill(0);
    for (const [id, value] of this.consts) values[id] = value ? ~0 : 0;
    const freeIds = this.slice.free_ids;
    for (let i = 0; i < freeIds.length; i++) values[freeIds[i]] = columns[i];

    const ops = this.ops;
    for (let i = 0; i < ops.length; i += STRIDE) {
      const run = DISPATCH[ops[i]];
      values[ops[i + 1]] = run(values, ops[i + 2], ops[i + 3], ops[i + 4], ops[i + 5]);
    }
    return values;
  }

  target(index = 0): number {
    return this.values[this.slice.targets[index]];
  }
}

/** The 32 assignments of block `b` of an exhaustive sweep, as columns. */
export function exhaustiveColumns(freeCount: number, block: number, into: Int32Array): void {
  for (let i = 0; i < freeCount; i++) {
    into[i] = i < 5 ? MAGIC[i] : (block >> (i - 5)) & 1 ? ~0 : 0;
  }
}

/** 32 random assignments as columns. Each word is 32 independent coin flips. */
export function randomColumns(freeCount: number, into: Int32Array, rng: () => number): void {
  for (let i = 0; i < freeCount; i++) into[i] = (rng() * 4294967296) | 0;
}

/** A deterministic PRNG, so a re-run reproduces the same counterexample.
 *  mulberry32 -- small, fast, and good enough for choosing test vectors. */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Which lane of `word` is set first, or -1. `Math.clz32` on the low bit. */
export function firstLane(word: number): number {
  if (word === 0) return -1;
  return 31 - Math.clz32(word & -word);
}

/** Decode one lane of a block back into a named assignment. */
export function decode(free: readonly string[], columns: Int32Array, lane: number): Vector {
  const leaves: Record<string, number> = {};
  for (let i = 0; i < free.length; i++) leaves[free[i]] = (columns[i] >>> lane) & 1;
  return { kind: "assignment", leaves };
}

// ---- the checks ---------------------------------------------------------

export interface Progress {
  (done: number, total: number): void;
}

export interface CheckOptions {
  /** For `implies`: the value the design side must take when the condition holds. */
  value?: number;
  /** For `requirement`: the value the probed flop is claimed to be forced to. */
  flopValue?: number;
  /** For `essential`: the leaves the player claimed the output depends on. */
  claimed?: string[];
  seed?: number;
  onProgress?: Progress;
}

/**
 * Run a combinational job and return a verdict.
 *
 * `design` and `claim` (when present) were cut over the same free variables in
 * the same order by `gdsx.analysis.claims.align`, which is what lets one column
 * block feed both and a counterexample decode to the same nets on either side.
 */
export function checkCombinational(
  check: "equal" | "implies" | "requirement",
  design: SliceView,
  claim: SliceView | null,
  options: CheckOptions = {},
): Verdict {
  const free = design.free;
  const plan = budget(free.length);
  const left = new SliceRunner(design);
  const right = claim ? new SliceRunner(claim) : null;
  const columns = new Int32Array(Math.max(free.length, 1));
  const rng = seeded(options.seed ?? 0x9e3779b9);
  const blocks =
    plan.method === "exhaustive"
      ? Math.max(1, 2 ** Math.max(0, free.length - 5))
      : Math.ceil(plan.cases / LANES);

  for (let block = 0; block < blocks; block++) {
    if (plan.method === "exhaustive") exhaustiveColumns(free.length, block, columns);
    else randomColumns(free.length, columns, rng);

    left.run(columns);
    if (right) right.run(columns);

    let bad = 0;
    if (check === "equal") {
      bad = left.target() ^ right!.target();
    } else if (check === "implies") {
      // Violated where the condition holds and the design does not take `value`.
      const condition = right!.target();
      const actual = left.target();
      bad = condition & (options.value === 0 ? actual : ~actual);
    } else {
      // requirement: the output takes `value` while the flop does not take the
      // value it was claimed to be forced to. Both come off ONE slice, so the
      // two readings are of the same assignment rather than of two runs.
      const output = options.value === 0 ? ~left.target(0) : left.target(0);
      const flop = options.flopValue === 0 ? ~left.target(1) : left.target(1);
      bad = output & ~flop;
    }

    if (bad !== 0) {
      const lane = firstLane(bad);
      return {
        kind: "DISPROVEN",
        counterexample: decode(free, columns, lane),
        observed: describeLane(design, claim, left, right, lane, check, options),
        expected: expectedOf(check, options),
      };
    }
    if (options.onProgress && (block & 0x3ff) === 0) options.onProgress(block, blocks);
  }

  options.onProgress?.(blocks, blocks);
  return plan.clean === "PROVEN"
    ? { kind: "PROVEN", method: "exhaustive", cases: plan.cases }
    : { kind: "LIKELY", method: "sampled", cases: plan.cases };
}

function lane(word: number, at: number): number {
  return (word >>> at) & 1;
}

function describeLane(
  design: SliceView,
  claim: SliceView | null,
  left: SliceRunner,
  right: SliceRunner | null,
  at: number,
  check: string,
  options: CheckOptions,
): string[] {
  if (check === "equal") {
    return [`${design.target_names[0]} = ${lane(left.target(), at)}`];
  }
  if (check === "implies") {
    return [
      `${claim!.target_names[0]} = ${lane(right!.target(), at)}`,
      `${design.target_names[0]} = ${lane(left.target(), at)}`,
    ];
  }
  return [
    `${design.target_names[0]} = ${lane(left.target(0), at)}`,
    `${design.target_names[1]} = ${lane(left.target(1), at)}`,
  ];
}

function expectedOf(check: string, options: CheckOptions): string[] {
  if (check === "equal") return ["the two sides agree"];
  if (check === "implies") return [`the condition implies ${options.value ?? 1}`];
  return ["no assignment satisfies the output without the flop"];
}

// ---- roles --------------------------------------------------------------

/** One reading of a role. A role like "shift register" does not name a single
 *  transition function -- direction and fill bit are both free -- so each
 *  reading is checked and the verdict says which one survived. */
interface Candidate {
  label: string;
  next(state: number): number;
}

/** The state of a group as a number, `group[i]` at weight `2**i` -- the same
 *  positional embedding `gdsx.analysis.decode` uses, and the same one an
 *  exhaustive sweep's row index already has, which is why no conversion is
 *  needed between a sweep row and a state. */
function stateOf(targets: readonly number[], values: Int32Array, at: number): number {
  let state = 0;
  for (let i = 0; i < targets.length; i++) state |= ((values[targets[i]] >>> at) & 1) << i;
  return state >>> 0;
}

/** Evaluate the group's next state for one starting state. */
function step(runner: SliceRunner, width: number, state: number, columns: Int32Array): number {
  for (let i = 0; i < width; i++) columns[i] = (state >>> i) & 1 ? ~0 : 0;
  runner.run(columns);
  return stateOf(runner.slice.targets, runner.values, 0);
}

function candidatesFor(
  role: string,
  width: number,
  runner: SliceRunner,
  columns: Int32Array,
): Candidate[] | { fail: string[] } {
  const mask = width >= 32 ? 0xffffffff : (1 << width) - 1;
  if (role === "counter") {
    return [{ label: "increment", next: (s) => (s + 1) & mask }];
  }
  if (role === "shift-reg") {
    const out: Candidate[] = [];
    for (const fill of [0, 1]) {
      out.push({
        label: `shift right, fill ${fill}`,
        next: (s) => ((s >>> 1) | (fill << (width - 1))) & mask,
      });
      out.push({
        label: `shift left, fill ${fill}`,
        next: (s) => ((s << 1) | fill) & mask,
      });
    }
    return out;
  }
  // An LFSR is a linear map over GF(2). Read its matrix off the transitions of
  // the unit vectors, then verify `next(s) = M s` for EVERY state below -- that
  // is a real proof of linearity, not a spot check, which is the only reason
  // this may come back PROVEN.
  const zero = step(runner, width, 0, columns);
  if (zero !== 0) {
    return { fail: [`next(0) = ${zero}, so the transition is not linear`] };
  }
  const basis: number[] = [];
  for (let i = 0; i < width; i++) basis.push(step(runner, width, 1 << i, columns));
  return [
    {
      label: "linear over GF(2)",
      next: (s) => {
        let out = 0;
        for (let i = 0; i < width; i++) if ((s >>> i) & 1) out ^= basis[i];
        return out & mask;
      },
    },
  ];
}

/**
 * A role claim, checked over the group's whole state space.
 *
 * `gdsx.analysis.decode.orbit` walks one trajectory, which cannot establish a
 * claim quantified over all states -- it is the form's suggestion and the
 * human-readable label, never the verdict. The verdict comes from here: every
 * state's transition, compared against a model of the claimed role.
 */
export function checkRole(
  role: string,
  design: SliceView,
  options: CheckOptions = {},
): Verdict {
  const width = design.free.length;
  if (width === 0) return { kind: "UNKNOWN", reason: "the group is empty" };
  if (width >= 31) {
    return {
      kind: "UNKNOWN",
      reason: `a ${width}-flop group has more states than this can index`,
    };
  }

  const plan = budget(width);
  const runner = new SliceRunner(design);
  const columns = new Int32Array(width);
  const rng = seeded(options.seed ?? 0x9e3779b9);

  if (role === "saturating-counter") {
    return checkSaturating(runner, design, width, plan, columns, rng);
  }
  const built = candidatesFor(role, width, runner, columns);
  if ("fail" in built) {
    return {
      kind: "DISPROVEN",
      counterexample: { kind: "state", flops: stateVector(design.free, 0) },
      observed: built.fail,
      expected: [`a ${role}`],
    };
  }

  let alive = built;
  const blocks =
    plan.method === "exhaustive"
      ? Math.max(1, 2 ** Math.max(0, width - 5))
      : Math.ceil(plan.cases / LANES);
  const lanes = lanesPerBlock(width, plan.method);

  for (let block = 0; block < blocks; block++) {
    if (plan.method === "exhaustive") exhaustiveColumns(width, block, columns);
    else randomColumns(width, columns, rng);
    runner.run(columns);

    for (let at = 0; at < lanes; at++) {
      const state = stateFromColumns(width, columns, at);
      const observed = stateOf(design.targets, runner.values, at);
      const survivors = alive.filter((c) => c.next(state) === observed);
      if (survivors.length === 0) {
        // Every reading of the role is now dead, so this state is the divergence
        // to report -- and `alive` still holds the readings that were live going
        // in, which is what makes the "expected" list say something useful.
        return {
          kind: "DISPROVEN",
          counterexample: { kind: "state", flops: stateVector(design.free, state) },
          observed: [`next(${state}) = ${observed}`],
          expected: alive.map((c) => `${c.label}: next(${state}) = ${c.next(state)}`),
        };
      }
      alive = survivors;
    }
    options.onProgress?.(block, blocks);
  }

  options.onProgress?.(blocks, blocks);
  return plan.clean === "PROVEN"
    ? { kind: "PROVEN", method: "exhaustive", cases: plan.cases }
    : { kind: "LIKELY", method: "sampled", cases: plan.cases };
}

/** How many lanes of a block are distinct assignments.
 *
 * Below five variables the MAGIC columns repeat: lane `j` encodes row `j`, so
 * with three variables lanes 0-7 are every assignment and lanes 8-31 are those
 * same eight again. Harmless where a check only looks for a counterexample, and
 * not harmless where it counts something -- a saturating counter's fixed points
 * would be counted four times over. */
function lanesPerBlock(width: number, method: string): number {
  if (method !== "exhaustive" || width >= 5) return LANES;
  return 1 << width;
}

/** The state a lane of a column block encodes: `columns[i]` is variable `i`. */
function stateFromColumns(width: number, columns: Int32Array, at: number): number {
  let state = 0;
  for (let i = 0; i < width; i++) state |= ((columns[i] >>> at) & 1) << i;
  return state >>> 0;
}

/** `stateOf` for a column array, where each column is one variable's lanes. */
function stateVector(free: readonly string[], state: number): Record<string, number> {
  const flops: Record<string, number> = {};
  for (let i = 0; i < free.length; i++) flops[free[i]] = (state >>> i) & 1;
  return flops;
}

/**
 * A saturating counter is not one transition function, so it is checked as the
 * two properties that define it: the value never decreases, and there is exactly
 * one state it settles on. "Never decreases" is read in the same positional
 * embedding as everywhere else, and that is worth knowing rather than assuming:
 * it is a classification aid, not a claim about the group's real bit weights.
 */
function checkSaturating(
  runner: SliceRunner,
  design: SliceView,
  width: number,
  plan: Budget,
  columns: Int32Array,
  rng: () => number,
): Verdict {
  let fixedPoints = 0;
  const blocks =
    plan.method === "exhaustive"
      ? Math.max(1, 2 ** Math.max(0, width - 5))
      : Math.ceil(plan.cases / LANES);
  const lanes = lanesPerBlock(width, plan.method);

  for (let block = 0; block < blocks; block++) {
    if (plan.method === "exhaustive") exhaustiveColumns(width, block, columns);
    else randomColumns(width, columns, rng);
    runner.run(columns);

    for (let at = 0; at < lanes; at++) {
      const state = stateFromColumns(width, columns, at);
      const observed = stateOf(design.targets, runner.values, at);
      if (observed < state) {
        return {
          kind: "DISPROVEN",
          counterexample: { kind: "state", flops: stateVector(design.free, state) },
          observed: [`next(${state}) = ${observed}, which is lower`],
          expected: ["a saturating counter never counts down"],
        };
      }
      if (observed === state) fixedPoints++;
    }
  }

  // Only meaningful when every state was visited; a sample cannot count them.
  if (plan.method === "exhaustive" && fixedPoints !== 1) {
    return {
      kind: "DISPROVEN",
      counterexample: null,
      observed: [`${fixedPoints} states map to themselves`],
      expected: ["exactly one, the value it saturates at"],
    };
  }
  return plan.clean === "PROVEN"
    ? { kind: "PROVEN", method: "exhaustive", cases: plan.cases }
    : { kind: "LIKELY", method: "sampled", cases: plan.cases };
}

/**
 * Functional support: which free variables can actually change the target.
 *
 * A net can sit in the structural cone and never affect the output -- `a & ~a`
 * is the small case, a reconverging tree the real one. This is the check that
 * tells the two apart, and it is why the support claim's form makes you pick a
 * sense: answering the functional question with the structural fact would let a
 * player call a graph fact a functional one.
 *
 * Each variable is swept twice, pinned low and pinned high, and the results
 * XORed: any set bit is an assignment of the others where this variable matters.
 */
export function checkEssential(design: SliceView, options: CheckOptions = {}): Verdict {
  const free = design.free;
  const plan = budget(free.length);
  const runner = new SliceRunner(design);
  const columns = new Int32Array(Math.max(free.length, 1));
  const rng = seeded(options.seed ?? 0x9e3779b9);
  const blocks =
    plan.method === "exhaustive"
      ? Math.max(1, 2 ** Math.max(0, free.length - 5))
      : Math.ceil(plan.cases / LANES);

  const essential: boolean[] = free.map(() => false);
  const witness: (Vector | null)[] = free.map(() => null);

  for (let variable = 0; variable < free.length; variable++) {
    for (let block = 0; block < blocks && !essential[variable]; block++) {
      if (plan.method === "exhaustive") exhaustiveColumns(free.length, block, columns);
      else randomColumns(free.length, columns, rng);

      columns[variable] = 0;
      runner.run(columns);
      const low = runner.target();
      columns[variable] = ~0;
      runner.run(columns);
      const differs = low ^ runner.target();

      if (differs !== 0) {
        essential[variable] = true;
        columns[variable] = 0;
        witness[variable] = decode(free, columns, firstLane(differs));
      }
    }
    options.onProgress?.(variable + 1, free.length);
  }

  const found = free.filter((_, i) => essential[i]);
  const wanted = [...(options.claimed ?? [])].sort();
  if (found.slice().sort().join(" ") === wanted.join(" ")) {
    return plan.clean === "PROVEN"
      ? { kind: "PROVEN", method: "exhaustive", cases: plan.cases }
      : { kind: "LIKELY", method: "sampled", cases: plan.cases };
  }

  // A variable claimed but not essential has no witness -- its absence is what
  // was shown, and only an exhaustive sweep shows it at all.
  const surprise = free.findIndex((net, i) => essential[i] && !wanted.includes(net));
  return {
    kind: "DISPROVEN",
    counterexample: surprise >= 0 ? witness[surprise] : null,
    observed: found,
    expected: wanted,
  };
}