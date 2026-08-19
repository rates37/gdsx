// The differential: the player's model against the real gate tape.
//
// This is `gdsx.sim.diff_models` as a game loop, and it keeps that module's one
// hard rule: **a name only one side knows about is missing data, not a
// disagreement.** A model that reports an observable the design does not have
// is not wrong, it is unchecked, and reporting it as agreement or as divergence
// would both be lies. So an unknown observable stops the run and says so.
//
// Two differences from the Python original, both deliberate:
//
//   * it compares whole runs rather than cycles. A model here is
//     `evaluate(pulses) -> dict`, which answers about the end of the run, so the
//     unit of comparison is one stimulus and "first divergence" is the first
//     VECTOR that disagrees, not the first cycle.
//   * it does not stop at the first divergence. `diff_models` stops because
//     there is nothing to learn past a mismatch in a cycle-by-cycle replay;
//     here the remaining vectors are independent, and the agreement fraction is
//     the number the player watches go up. The first divergence is still
//     reported as the first one, in generation order.
//
// Nothing in this file decides whether a model is good. It reports how many
// vectors agreed out of how many, which is not a proof and is never labelled as
// one -- see `badge.ts`.

import { GateTapeExecutor } from "../sim/executor.ts";
import type { GateTape } from "../sim/tape.ts";
import { bitsOf, type Pulses } from "./vectors.ts";

export interface DesignContext {
  tape: GateTape;
  resetVector: Record<string, number>;
  inputPorts: string[];
  cycles: number;
  /** The port the pulses drive. */
  keyPort: string;
  /** Every other port's level, held for the whole run. */
  levels: Record<string, number>;
}

export interface Divergence {
  /** Index into the generated vector list -- the FIRST one that disagreed. */
  index: number;
  pulses: Pulses;
  signal: string;
  model: number;
  design: number;
}

export interface DiffReport {
  /** The observables actually compared: the names the model reported. */
  watch: string[];
  vectors: number;
  agreed: number;
  first: Divergence | null;
  /** Mismatched vectors per observable, so "which part of my model is wrong" is answerable. */
  mismatches: Record<string, number>;
  /**
   * Observables the DESIGN held at one value across every vector.
   *
   * Agreeing about one of these tested nothing: if no generated key ever
   * latched `success`, then a model that always answers 0 agrees perfectly and
   * has been asked nothing. This is the same refusal the notebook makes for a
   * sequential claim whose precondition never held -- "no counterexample in 400
   * vectors" is a lie when none of the 400 reached the situation the claim is
   * about -- and it is why `store.ts` will not award the badge on a run where
   * every watched observable is in this list.
   */
  constant: string[];
  ms: number;
  at: number;
}

/** A model that reported something uncheckable. Not a wrong model: an unchecked one. */
export class ObservableError extends Error {
  /** The names that could not be checked. Written out rather than declared as a
   *  constructor parameter property: this module is loaded directly by Node's
   *  type stripping in web/scripts/test-model-diff.mjs, which does not support
   *  parameter properties. */
  readonly names: string[];

  constructor(names: string[], message: string) {
    super(message);
    this.names = names;
  }
}

/** Run one stimulus on the real design and read the named observables at the end. */
export function observe(
  ctx: DesignContext,
  pulses: Pulses,
  watch: readonly string[],
): Record<string, number> {
  const executor = new GateTapeExecutor(ctx.tape);
  executor.reset();
  if (Object.keys(ctx.resetVector).length > 0) executor.step(ctx.resetVector);

  const high = new Set(pulses);
  const inputs: Record<string, number> = { ...ctx.levels };
  for (let c = 0; c < ctx.cycles; c++) {
    inputs[ctx.keyPort] = high.has(c) ? 1 : 0;
    executor.step(inputs);
  }

  const found: Record<string, number> = {};
  const flops = ctx.tape.header.flop_names;
  for (const name of watch) {
    const flop = flops.indexOf(name);
    if (flop >= 0) {
      found[name] = executor.state[flop];
      continue;
    }
    const net = ctx.tape.header.names[name];
    if (net === undefined) continue;
    found[name] = executor.values[net];
  }
  return found;
}

/** Which of the model's observable names the design does not have. */
export function unknownObservables(ctx: DesignContext, names: readonly string[]): string[] {
  const flops = new Set(ctx.tape.header.flop_names);
  return names.filter((name) => !flops.has(name) && ctx.tape.header.names[name] === undefined);
}

/**
 * Compare `results[i]` (the model's answer for `vectors[i]`) against the design.
 *
 * `results` is the model's output for every vector, already collected -- running
 * the model is the host's job (`host.ts`), and it happens in a worker that may
 * be a whole second Pyodide. This function only does the comparison and the
 * design side, so it stays synchronous, testable, and free of any opinion about
 * what language the model was written in.
 */
export function compare(
  ctx: DesignContext,
  vectors: readonly Pulses[],
  results: readonly Record<string, number>[],
): DiffReport {
  const started = performance.now();
  if (results.length !== vectors.length) {
    throw new Error(`the model answered ${results.length} of ${vectors.length} vectors`);
  }
  if (vectors.length === 0) throw new Error("no vectors to run");

  const watch = Object.keys(results[0]);
  if (watch.length === 0) {
    throw new ObservableError(
      [],
      "the model returned no observables — return at least {'success': 0 or 1}",
    );
  }
  const unknown = unknownObservables(ctx, watch);
  if (unknown.length) {
    throw new ObservableError(
      unknown,
      `the design has no signal named ${unknown.join(", ")}, so it cannot be ` +
        `compared — every observable must be a real net or flop`,
    );
  }

  const mismatches: Record<string, number> = {};
  for (const name of watch) mismatches[name] = 0;
  // What the design itself did, per observable: agreement about a signal that
  // never moved is agreement about nothing.
  const seen: Record<string, Set<number>> = {};
  for (const name of watch) seen[name] = new Set();
  let agreed = 0;
  let first: Divergence | null = null;

  for (let i = 0; i < vectors.length; i++) {
    const model = results[i];
    for (const name of watch) {
      if (!(name in model)) {
        throw new ObservableError(
          [name],
          `the model reported ${name} for vector 0 but not for vector ${i}; ` +
            `an observable that comes and goes cannot be compared`,
        );
      }
    }
    const design = observe(ctx, vectors[i], watch);
    let ok = true;
    for (const name of watch) seen[name].add(design[name]);
    for (const name of watch) {
      if (model[name] === design[name]) continue;
      ok = false;
      mismatches[name]++;
      if (first === null) {
        first = {
          index: i,
          pulses: [...vectors[i]],
          signal: name,
          model: model[name],
          design: design[name],
        };
      }
    }
    if (ok) agreed++;
  }

  return {
    watch,
    vectors: vectors.length,
    agreed,
    first,
    mismatches,
    constant: watch.filter((name) => seen[name].size <= 1),
    ms: performance.now() - started,
    at: Date.now(),
  };
}

/** A divergence as sequence-editor tracks: the one-click "load into the waveform". */
export function tracksOf(ctx: DesignContext, pulses: Pulses): Record<string, string> {
  const tracks: Record<string, string> = {};
  for (const port of ctx.inputPorts) {
    tracks[port] =
      port === ctx.keyPort
        ? bitsOf(pulses, ctx.cycles)
        : (ctx.levels[port] === 1 ? "1" : "0").repeat(ctx.cycles);
  }
  return tracks;
}