// Claims about what happens over time, rather than within one clock frame.
//
// Three of the eight claim types are sequential -- timing, the "over time"
// reading of an invariant, and constraint -- and they share one honest
// limitation worth stating in one place rather than three: **the space of input
// sequences is 2**cycles, so it cannot be exhausted.** A 121-cycle track has
// more keys than there are atoms worth counting. So except for the deliberately
// tiny case handled below, a sequential claim's best clean verdict is `LIKELY`,
// and the form says so before the player presses verify rather than after.
//
// The exception is timing, which is not quantified over sequences at all: it is
// a claim about ONE stimulus, snapshotted into the claim when it was made. That
// is a deterministic replay: settled, and settled about that stimulus only. It
// gets its own verdict method, `replay`, rather than borrowing `exhaustive` --
// nothing was exhausted -- and is always rendered "under sequence ⟨name⟩".
//
// This runs on the main thread, unlike the combinational evaluator. A few
// hundred stimuli of a few hundred cycles is tens of milliseconds against the
// tape the page already has loaded, and moving it would mean shipping the tape
// to a second worker to save less time than that costs.

import type { GateTape } from "../sim/tape.ts";
import { GateTapeExecutor } from "../sim/executor.ts";
import type { PredicateView, SliceView } from "../gdsx-types.ts";
import type { Stimulus, TimingEvent, Verdict } from "./model.ts";
import { SliceRunner, seeded } from "./verify.ts";

/** What a sequential check needs to drive the design, from the live SimStore. */
export interface SimContext {
  tape: GateTape;
  /** Applied once before cycle 0, if the puzzle has a reset protocol. */
  resetVector: Record<string, number>;
  /** Every primary input the sequence editor has a track for. */
  inputPorts: string[];
}

/** How many stimuli a sampled sequential search tries. Fewer than the
 *  combinational `SAMPLE_VECTORS` because each one costs a whole run, not a
 *  word of a sweep -- and the honest label says exactly how many. */
export const SAMPLE_STIMULI = 400;

/** Below this many cycles the key space is small enough to actually exhaust,
 *  and a clean constraint search may say PROVEN. Above it, never. */
export const ENUMERABLE_CYCLES = 12;

/** One stimulus run: the settled value array after each cycle. */
export function replay(
  ctx: SimContext,
  tracks: Record<string, string>,
  cycles: number,
): Int32Array[] {
  const executor = new GateTapeExecutor(ctx.tape);
  executor.reset();
  if (Object.keys(ctx.resetVector).length > 0) executor.step(ctx.resetVector);

  const inputs: Record<string, number> = {};
  const history: Int32Array[] = new Array(cycles);
  for (let c = 0; c < cycles; c++) {
    for (const port of ctx.inputPorts) {
      const bits = tracks[port];
      inputs[port] = bits && c < bits.length ? (bits[c] === "1" ? 1 : 0) : 0;
    }
    executor.step(inputs);
    history[c] = executor.values.slice();
  }
  return history;
}

/**
 * The clean verdict for a search whose precondition may never have been met.
 *
 * "No counterexample in 400 vectors" is a lie when none of those 400 vectors
 * even reached the situation the claim is about -- an implication is vacuously
 * true wherever its antecedent is false, and reporting that as evidence would be
 * the most misleading thing in the whole mechanic. So a search that never armed
 * comes back UNKNOWN saying so, and the player goes and finds a stimulus that
 * arms it. `LIKELY` here means the claim was genuinely put at risk and survived.
 */
function vacuous(hits: number, why: string, cases: number): Verdict {
  if (hits === 0) return { kind: "UNKNOWN", reason: `${why}, so nothing was tested` };
  return { kind: "LIKELY", method: "sampled", cases };
}

function valueAt(ctx: SimContext, history: Int32Array[], cycle: number, net: string): number {
  const id = ctx.tape.header.names[net];
  const snapshot = history[cycle];
  return id !== undefined && snapshot ? snapshot[id] : 0;
}

// ---- timing -------------------------------------------------------------

/** The cycles at which `event` holds for `net` in this run. */
export function eventCycles(
  ctx: SimContext,
  history: Int32Array[],
  net: string,
  event: TimingEvent,
): number[] {
  const values: number[] = [];
  for (let c = 0; c < history.length; c++) values.push(valueAt(ctx, history, c, net));

  // "Latches high" is high here AND high for the rest of the run, not high
  // once -- a flag that pulses and drops has not latched. Walked backwards so
  // it stays linear rather than quadratic.
  const staysHigh: boolean[] = new Array(values.length).fill(false);
  let holding = true;
  for (let c = values.length - 1; c >= 0; c--) {
    holding = holding && values[c] === 1;
    staysHigh[c] = holding;
  }

  const found: number[] = [];
  for (let c = 0; c < values.length; c++) {
    const now = values[c];
    const before = c > 0 ? values[c - 1] : 0;
    const hit =
      event === "high"
        ? now === 1
        : event === "low"
          ? now === 0
          : event === "rises"
            ? before === 0 && now === 1
            : event === "falls"
              ? before === 1 && now === 0
              : staysHigh[c];
    if (hit) found.push(c);
  }
  return found;
}

/**
 * A timing claim: a deterministic replay of the stimulus the claim carries.
 *
 * Settled by `method: "replay"`, with `cases` counting the cycles simulated
 * rather than a space of inputs -- because the claim is not quantified over
 * inputs. It is about this sequence, so the verdict carries the sequence's name
 * and the notebook always shows it that way. Deliberately NOT "exhaustive":
 * that word is reserved for a sweep that really did visit every case.
 */
export function checkTiming(
  ctx: SimContext,
  net: string,
  event: TimingEvent,
  claimed: number[],
  stimulus: Stimulus,
): Verdict {
  const history = replay(ctx, stimulus.tracks, stimulus.cycles);
  const actual = eventCycles(ctx, history, net, event);
  const wanted = [...claimed].sort((a, b) => a - b);

  if (actual.join(",") === wanted.join(",")) {
    return {
      kind: "PROVEN",
      method: "replay",
      cases: stimulus.cycles,
      sequence: stimulus.name,
    };
  }
  return {
    kind: "DISPROVEN",
    counterexample: {
      kind: "trace",
      cycles: stimulus.cycles,
      tracks: { ...stimulus.tracks },
    },
    observed: [actual.length ? `cycles {${actual.join(", ")}}` : "never"],
    expected: [wanted.length ? `cycles {${wanted.join(", ")}}` : "never"],
  };
}

// ---- the sequential invariant ------------------------------------------

/**
 * `net` is `value` whenever `condition`, checked cycle by cycle over generated
 * stimuli. Never proven: see this module's header.
 */
export function checkSequentialInvariant(
  ctx: SimContext,
  net: string,
  value: number,
  condition: SliceView,
  baseline: Stimulus,
  seed = 0x5eed,
): Verdict {
  const runner = new SliceRunner(condition);
  const columns = new Int32Array(Math.max(condition.free.length, 1));
  const rng = seeded(seed);
  let held = 0;

  for (let attempt = 0; attempt < SAMPLE_STIMULI; attempt++) {
    const tracks = attempt === 0 ? baseline.tracks : perturb(ctx, baseline, rng);
    const history = replay(ctx, tracks, baseline.cycles);

    for (let c = 0; c < history.length; c++) {
      // One lane is enough: the condition is evaluated against what the nets
      // actually did this cycle, not swept.
      for (let i = 0; i < condition.free.length; i++) {
        columns[i] = valueAt(ctx, history, c, condition.free[i]) ? ~0 : 0;
      }
      runner.run(columns);
      if ((runner.target() & 1) === 0) continue;
      held++;
      if (valueAt(ctx, history, c, net) === value) continue;

      return {
        kind: "DISPROVEN",
        counterexample: { kind: "trace", cycles: baseline.cycles, tracks: { ...tracks } },
        observed: [`${net} = ${1 - value} at cycle ${c}, with the condition true`],
        expected: [`${net} = ${value} whenever the condition holds`],
      };
    }
  }
  return vacuous(
    held,
    `the condition never held in any of the ${SAMPLE_STIMULI} generated stimuli`,
    SAMPLE_STIMULI,
  );
}

// ---- constraint predicates ---------------------------------------------

/** One measurement of one input track. `-1` where there is nothing to measure. */
export function measure(
  name: string,
  bits: string,
  window: number[] | null,
): number {
  const from = window ? Math.max(0, window[0]) : 0;
  const to = window ? Math.min(bits.length - 1, window[1]) : bits.length - 1;
  const high: number[] = [];
  for (let i = from; i <= to; i++) if (bits[i] === "1") high.push(i);

  switch (name) {
    case "count":
      return high.length;
    case "first":
      return high.length ? high[0] : -1;
    case "last":
      return high.length ? high[high.length - 1] : -1;
    case "runs": {
      let runs = 0;
      for (let i = 0; i < high.length; i++) if (i === 0 || high[i] !== high[i - 1] + 1) runs++;
      return runs;
    }
    default: {
      // The low runs strictly between two pulses -- the gaps a key has, not the
      // dead time before the first pulse or after the last, which are not gaps.
      const gaps: number[] = [];
      for (let i = 1; i < high.length; i++) {
        const size = high[i] - high[i - 1] - 1;
        if (size > 0) gaps.push(size);
      }
      if (name === "gaps") return gaps.length;
      if (name === "mingap") return gaps.length ? Math.min(...gaps) : -1;
      if (name === "maxgap") return gaps.length ? Math.max(...gaps) : -1;
      return -1;
    }
  }
}

export function evaluatePredicate(
  node: PredicateView,
  tracks: Record<string, string>,
): boolean {
  switch (node.node) {
    case "term": {
      const found = measure(node.measure, tracks[node.port] ?? "", node.window);
      switch (node.op) {
        case "==":
          return found === node.value;
        case "!=":
          return found !== node.value;
        case "<":
          return found < node.value;
        case "<=":
          return found <= node.value;
        case ">":
          return found > node.value;
        default:
          return found >= node.value;
      }
    }
    case "not":
      return !evaluatePredicate(node.of[0], tracks);
    case "or":
      return node.of.some((child) => evaluatePredicate(child, tracks));
    default:
      return node.of.every((child) => evaluatePredicate(child, tracks));
  }
}

/**
 * "Every key that latches `output` satisfies the predicate."
 *
 * Disproved by one key that latches it and does not -- which is loadable
 * straight into the sequence editor, and is by far the most useful thing this
 * check produces. Proved only when the key space was small enough to walk in
 * full, which for a real puzzle it is not, so this normally lands on `LIKELY`
 * with the number of keys tried.
 *
 * The Model Builder plugs into this later as another source of candidate keys;
 * nothing here needs to change for that.
 */
export function checkConstraint(
  ctx: SimContext,
  output: string,
  predicate: PredicateView,
  baseline: Stimulus,
  seed = 0x5eed,
): Verdict {
  const rng = seeded(seed);
  const exhaustive =
    baseline.cycles <= ENUMERABLE_CYCLES && ctx.inputPorts.length === 1;
  const total = exhaustive ? 2 ** baseline.cycles : SAMPLE_STIMULI;
  let latchedCount = 0;

  for (let attempt = 0; attempt < total; attempt++) {
    const tracks = exhaustive
      ? enumerated(ctx, baseline, attempt)
      : attempt === 0
        ? baseline.tracks
        : perturb(ctx, baseline, rng);
    const history = replay(ctx, tracks, baseline.cycles);
    const latched = valueAt(ctx, history, baseline.cycles - 1, output) === 1;
    if (!latched) continue;
    latchedCount++;
    if (evaluatePredicate(predicate, tracks)) continue;

    return {
      kind: "DISPROVEN",
      counterexample: { kind: "trace", cycles: baseline.cycles, tracks: { ...tracks } },
      observed: [`a key that latches ${output} and breaks the predicate`],
      expected: ["every such key satisfies it"],
    };
  }

  if (exhaustive && latchedCount > 0) {
    return { kind: "PROVEN", method: "exhaustive", cases: total };
  }
  return vacuous(
    latchedCount,
    `none of the ${total} keys tried latched ${output}`,
    total,
  );
}

function enumerated(ctx: SimContext, baseline: Stimulus, index: number): Record<string, string> {
  const port = ctx.inputPorts[0];
  let bits = "";
  for (let c = 0; c < baseline.cycles; c++) bits += (index >> c) & 1 ? "1" : "0";
  return { ...baseline.tracks, [port]: bits };
}

/**
 * A candidate key: sometimes a fresh random one at a random density, sometimes
 * a small perturbation of what the player is holding.
 *
 * Both matter. Random keys explore; near-misses of the player's own sequence are
 * where a constraint claim is actually wrong, because that is the region they
 * derived it from.
 */
function perturb(
  ctx: SimContext,
  baseline: Stimulus,
  rng: () => number,
): Record<string, string> {
  const tracks: Record<string, string> = { ...baseline.tracks };
  const near = rng() < 0.5;
  for (const port of ctx.inputPorts) {
    const bits = (baseline.tracks[port] ?? "").padEnd(baseline.cycles, "0").split("");
    if (near) {
      const flips = 1 + Math.floor(rng() * 3);
      for (let i = 0; i < flips; i++) {
        const at = Math.floor(rng() * baseline.cycles);
        bits[at] = bits[at] === "1" ? "0" : "1";
      }
    } else {
      const density = 0.05 + rng() * 0.45;
      for (let c = 0; c < baseline.cycles; c++) bits[c] = rng() < density ? "1" : "0";
    }
    tracks[port] = bits.join("");
  }
  return tracks;
}

