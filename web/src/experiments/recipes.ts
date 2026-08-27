// The Experiment Runner's engine (game-plan.md §4.7): a baseline, a set of
// perturbations, a watch set, and the matrix that falls out.
//
// This is `gdsx.sim.sensitivity` run in the browser, and it is deliberately a
// port rather than a reimplementation: one baseline trace, then one perturbed
// trace per row, comparing the FINAL state of the watched elements. The Python
// module's `_run` holds a base vector every cycle and overrides it at the
// cycles named in `perturbations`, so that is exactly what `trace` below does,
// which is what lets `web/scripts/test-experiments.mjs` check the two against
// the same golden the way the executors are checked against each other.
//
// Two consequences of that shape are worth stating because they are what make
// the numbers mean something:
//
//   * **A stimulus is expressed as perturbations of a constant baseline.** A
//     sweep over "the key I am holding" is not a second code path -- it is the
//     same call with the key's own high cycles already in `perturbations`. That
//     keeps every recipe expressible as one `sensitivity.probe` call a player
//     can paste into the REPL.
//   * **Every result is conditional on its baseline.** "Cycle 7 writes flop 50"
//     is a fact about this baseline, not about the design, in the same way a
//     role claim is conditional on what was held fixed. Every result carries the
//     baseline it was measured under and the panel prints it.
//
// The two self-checks from `sensitivity.map` are computed here rather than left
// to the panel (§4.10: they must be surfaced as warnings): an element that
// reacts to nothing, and a run that moves nothing. Both usually mean the watch
// set or the window is wrong, and finding that out immediately is worth more
// than a correct-looking matrix.

import { GateTapeExecutor } from "../sim/executor.ts";
import type { GateTape } from "../sim/tape.ts";

/** One clock cycle's worth of primary-input assignments. */
export type Vector = Record<string, number>;

export interface ExperimentContext {
  tape: GateTape;
  /** Applied once before cycle 0, if the puzzle has a reset protocol. */
  resetVector: Record<string, number>;
  /** Every primary input the sequence editor has a track for. */
  inputPorts: string[];
  /** How long every trace in a sweep runs. */
  cycles: number;
  /** Held every cycle unless a perturbation overrides it that cycle. */
  baseVector: Vector;
  /** What the runs are measured against, in words -- printed with every result. */
  baselineLabel: string;
  /** The baseline's own perturbations: empty for an idle baseline, the key's
   *  high cycles when the baseline is the sequence the player is holding. */
  baseline: PerturbationSet;
  /** The port a pulse recipe pulses. */
  keyPort: string;
}

/** cycle -> the input overrides that apply for that one cycle only. */
export type PerturbationSet = ReadonlyMap<number, Vector>;

/** What a cell means. The two are not interchangeable and the legend says which. */
export type CellKind =
  /** 1 = this run moved that element's final value away from the baseline's. */
  | "changed"
  /** the element's value itself, at the cycle the row names. */
  | "value";

export interface MatrixRow {
  label: string;
  /** The perturbation this row ran, for "load into the waveform". */
  perturbations: PerturbationSet;
  cells: Uint8Array;
  /** How many cells are marked -- the row summary, and the silent-row check. */
  marked: number;
}

export interface ExperimentResult {
  recipe: RecipeId;
  title: string;
  baselineLabel: string;
  /** The watch set, in the order the columns appear. */
  columns: string[];
  rows: MatrixRow[];
  cellKind: CellKind;
  /** The two self-checks from `sensitivity.map`, plus a value-scan equivalent. */
  warnings: string[];
  /** The `gdsx` call this view is, for the `{ }` button. */
  call: string;
  /** Wall clock, so "this is interactive" is something you can see. */
  ms: number;
  /** How many traces were run, baseline included. */
  traces: number;
  at: number;
}

// ---- running ------------------------------------------------------------

interface Final {
  flops: Int32Array;
  values: Int32Array;
}

/**
 * One full trace: `baseVector` held every cycle, overridden at the cycles named
 * in `perturbations`, and the final state after `cycles` steps.
 *
 * The Python original is `gdsx.sim.sensitivity._run`, down to the optional
 * one-time reset step before cycle 0.
 */
export function trace(ctx: ExperimentContext, perturbations: PerturbationSet): Final {
  const executor = new GateTapeExecutor(ctx.tape);
  executor.reset();
  if (Object.keys(ctx.resetVector).length > 0) executor.step(ctx.resetVector);

  for (let cycle = 0; cycle < ctx.cycles; cycle++) {
    const override = perturbations.get(cycle);
    executor.step(override ? { ...ctx.baseVector, ...override } : ctx.baseVector);
  }
  return { flops: executor.state.slice(), values: executor.values.slice() };
}

/** Where a watched name lives: a flop's state, or a net's settled value. */
interface Probe {
  name: string;
  index: number;
  kind: "flop" | "net";
}

function resolve(ctx: ExperimentContext, watch: readonly string[]): Probe[] {
  const flops = new Map(ctx.tape.header.flop_names.map((n, i) => [n, i]));
  const found: Probe[] = [];
  for (const name of watch) {
    const flop = flops.get(name);
    if (flop !== undefined) {
      found.push({ name, index: flop, kind: "flop" });
      continue;
    }
    const net = ctx.tape.header.names[name];
    if (net !== undefined) found.push({ name, index: net, kind: "net" });
    // A name that is neither is dropped rather than silently read as 0 -- see
    // `unknownWatched`, which reports it instead of pretending it was measured.
  }
  return found;
}

/** Watched names the design does not have, so the panel can say so. */
export function unknownWatched(ctx: ExperimentContext, watch: readonly string[]): string[] {
  const known = new Set(resolve(ctx, watch).map((p) => p.name));
  return watch.filter((name) => !known.has(name));
}

function read(probe: Probe, final: Final): number {
  return probe.kind === "flop" ? final.flops[probe.index] : final.values[probe.index];
}

// ---- the recipes --------------------------------------------------------

export type RecipeId = "single-pulse" | "gap" | "bit-flip" | "reset-scan";

export interface RecipeParams {
  /** Inclusive lower and exclusive upper cycle bound of the sweep. */
  from: number;
  to: number;
  /** Gap recipe: where the first pulse of each pair goes, inclusive range.
   *  `firstFrom === firstTo` is the original single-first-cycle sweep. */
  firstFrom: number;
  firstTo: number;
  /** Gap recipe: the gaps tried, inclusive. */
  minGap: number;
  maxGap: number;
  watch: string[];
}

export interface Recipe {
  id: RecipeId;
  label: string;
  /** What it measures, in one line, shown under the picker. */
  blurb: string;
  cellKind: CellKind;
}

export const RECIPES: readonly Recipe[] = [
  {
    id: "single-pulse",
    label: "single-pulse sweep",
    blurb:
      "one extra pulse at each cycle in turn, against the baseline. " +
      "Which cycle writes which element.",
    cellKind: "changed",
  },
  {
    id: "gap",
    label: "gap sweep",
    blurb:
      "a pair of pulses at every starting cycle and every spacing, against " +
      "the baseline. Which separations the design distinguishes, and whether " +
      "that depends on where the pair starts.",
    cellKind: "changed",
  },
  {
    id: "bit-flip",
    label: "bit-flip sensitivity",
    blurb:
      "flip each cycle of the key you are holding, one at a time. " +
      "Which cycles of your own key matter, pulses and gaps alike.",
    cellKind: "changed",
  },
  {
    id: "reset-scan",
    label: "reset-value scan",
    blurb:
      "no perturbation at all: what each element holds at each cycle from reset. " +
      "Which flops come out of reset high, and when each first moves.",
    cellKind: "value",
  },
] as const;

/** The rows a recipe runs, before any of them is simulated. */
function plan(ctx: ExperimentContext, recipe: RecipeId, params: RecipeParams): MatrixRow[] {
  const rows: MatrixRow[] = [];
  const base = ctx.baseline;
  const high = ctx.baseVector[ctx.keyPort] === 1 ? 0 : 1;

  if (recipe === "single-pulse") {
    for (let c = params.from; c < params.to; c++) {
      const perturbations = new Map(base);
      perturbations.set(c, { ...(base.get(c) ?? {}), [ctx.keyPort]: high });
      rows.push({ label: `cycle ${c}`, perturbations, cells: new Uint8Array(0), marked: 0 });
    }
    return rows;
  }

  if (recipe === "gap") {
    // A single `first` reproduces the original one-row-per-gap sweep. A range
    // makes each row one (first, gap) pair, so the matrix that falls out is
    // the same one the walkthrough assembles by hand from 24 separate runs
    // (docs/game/original-puzzle-walkthrough.md §7).
    const single = params.firstFrom === params.firstTo;
    for (let first = params.firstFrom; first <= params.firstTo; first++) {
      for (let gap = params.minGap; gap <= params.maxGap; gap++) {
        const second = first + gap;
        if (second >= ctx.cycles) break;
        const perturbations = new Map(base);
        for (const at of [first, second]) {
          perturbations.set(at, { ...(base.get(at) ?? {}), [ctx.keyPort]: high });
        }
        rows.push({
          label: single
            ? `gap ${gap} (${first}, ${second})`
            : `first ${first}, gap ${gap} (${first}, ${second})`,
          perturbations,
          cells: new Uint8Array(0),
          marked: 0,
        });
      }
    }
    return rows;
  }

  if (recipe === "bit-flip") {
    // The one recipe whose rows are not "add a pulse": a cycle the key already
    // drives high is flipped LOW. A key's gaps carry as much information as its
    // pulses, and a sweep that could only add pulses would never test them.
    for (let c = params.from; c < params.to; c++) {
      const perturbations = new Map(base);
      const now = base.get(c)?.[ctx.keyPort] ?? ctx.baseVector[ctx.keyPort] ?? 0;
      perturbations.set(c, { ...(base.get(c) ?? {}), [ctx.keyPort]: now === 1 ? 0 : 1 });
      rows.push({
        label: `cycle ${c} ${now === 1 ? "1→0" : "0→1"}`,
        perturbations,
        cells: new Uint8Array(0),
        marked: 0,
      });
    }
    return rows;
  }

  for (let c = params.from; c < params.to; c++) {
    rows.push({ label: `cycle ${c}`, perturbations: base, cells: new Uint8Array(0), marked: 0 });
  }
  return rows;
}

export interface RunOptions {
  /** Called with 0..1 between chunks, so a long sweep is a progress bar. */
  onProgress?: (fraction: number) => void;
  /** Milliseconds of work between yields to the event loop. */
  sliceMs?: number;
}

/**
 * Run a recipe and return its matrix.
 *
 * Chunked and `await`ed rather than run in one blocking pass: a 121-cycle sweep
 * over 92 flops is ~15k tape steps and lands in well under a second, but the
 * panel still has to paint a progress bar for the recipes that do not.
 */
export async function run(
  ctx: ExperimentContext,
  recipe: RecipeId,
  params: RecipeParams,
  options: RunOptions = {},
): Promise<ExperimentResult> {
  const started = performance.now();
  const probes = resolve(ctx, params.watch);
  const columns = probes.map((p) => p.name);
  const sliceMs = options.sliceMs ?? 12;

  if (recipe === "reset-scan") {
    const result = valueScan(ctx, probes, params);
    return {
      recipe,
      title: RECIPES.find((r) => r.id === recipe)!.label,
      baselineLabel: ctx.baselineLabel,
      columns,
      rows: result.rows,
      cellKind: "value",
      warnings: result.warnings,
      call: callText(ctx, recipe, params),
      ms: performance.now() - started,
      traces: 1,
      at: Date.now(),
    };
  }

  const rows = plan(ctx, recipe, params);
  const reference = trace(ctx, ctx.baseline);
  const baselineValues = probes.map((p) => read(p, reference));

  let mark = performance.now();
  for (let i = 0; i < rows.length; i++) {
    const final = trace(ctx, rows[i].perturbations);
    const cells = new Uint8Array(probes.length);
    let marked = 0;
    for (let j = 0; j < probes.length; j++) {
      if (read(probes[j], final) !== baselineValues[j]) {
        cells[j] = 1;
        marked++;
      }
    }
    rows[i].cells = cells;
    rows[i].marked = marked;

    if (performance.now() - mark > sliceMs) {
      options.onProgress?.((i + 1) / rows.length);
      await new Promise((resolve) => setTimeout(resolve, 0));
      mark = performance.now();
    }
  }
  options.onProgress?.(1);

  return {
    recipe,
    title: RECIPES.find((r) => r.id === recipe)!.label,
    baselineLabel: ctx.baselineLabel,
    columns,
    rows,
    cellKind: "changed",
    warnings: selfChecks(columns, rows),
    call: callText(ctx, recipe, params),
    ms: performance.now() - started,
    traces: rows.length + 1,
    at: Date.now(),
  };
}

/** The reset-value scan: one trace, sampled at every cycle in the window. */
function valueScan(
  ctx: ExperimentContext,
  probes: Probe[],
  params: RecipeParams,
): { rows: MatrixRow[]; warnings: string[] } {
  const executor = new GateTapeExecutor(ctx.tape);
  executor.reset();
  if (Object.keys(ctx.resetVector).length > 0) executor.step(ctx.resetVector);

  const rows: MatrixRow[] = [];
  const moved = new Uint8Array(probes.length);
  let first: number[] | null = null;

  for (let cycle = 0; cycle < params.to; cycle++) {
    const override = ctx.baseline.get(cycle);
    executor.step(override ? { ...ctx.baseVector, ...override } : ctx.baseVector);
    if (cycle < params.from) continue;

    const cells = new Uint8Array(probes.length);
    let marked = 0;
    for (let j = 0; j < probes.length; j++) {
      const value =
        probes[j].kind === "flop"
          ? executor.state[probes[j].index]
          : executor.values[probes[j].index];
      cells[j] = value;
      if (value) marked++;
      if (first !== null && value !== first[j]) moved[j] = 1;
    }
    if (first === null) first = Array.from(cells);
    rows.push({ label: `cycle ${cycle}`, perturbations: ctx.baseline, cells, marked });
  }

  // The value-scan reading of `sensitivity.map`'s unreactive check: an element
  // that holds its reset value for the whole window learned nothing here, which
  // is a fact about the window as often as it is about the element.
  const stuck = probes.filter((_, j) => !moved[j]).map((p) => p.name);
  const warnings: string[] = [];
  if (stuck.length) {
    warnings.push(
      `${stuck.length} watched element(s) never left their value at cycle ` +
        `${params.from} in this window: ${preview(stuck)}`,
    );
  }
  return { rows, warnings };
}

/**
 * `sensitivity.map`'s two self-checks, which are part of the result rather than
 * an optional extra (§4.10): a watched element that reacts to no run, and a run
 * that moves no watched element. Either usually means the watch set or the
 * window is wrong.
 */
export function selfChecks(columns: readonly string[], rows: readonly MatrixRow[]): string[] {
  const warnings: string[] = [];
  const reacted = new Uint8Array(columns.length);
  for (const row of rows) {
    for (let j = 0; j < columns.length; j++) if (row.cells[j]) reacted[j] = 1;
  }

  const unreactive = columns.filter((_, j) => !reacted[j]);
  if (unreactive.length) {
    warnings.push(
      `${unreactive.length} watched element(s) never reacted to any run: ` +
        `${preview(unreactive)} — the wrong elements are being watched, or the ` +
        `perturbation never reaches them`,
    );
  }
  const silent = rows.filter((row) => row.marked === 0).map((row) => row.label);
  if (silent.length) {
    warnings.push(
      `${silent.length} run(s) moved no watched element: ${preview(silent)} — an ` +
        `element was missed, or the window is shorter than the design's write window`,
    );
  }
  return warnings;
}

function preview(names: readonly string[], limit = 8): string {
  return names.length <= limit
    ? names.join(", ")
    : `${names.slice(0, limit).join(", ")}, … (${names.length - limit} more)`;
}

// ---- what a player could have typed instead -----------------------------

function pyDict(vector: Vector): string {
  const parts = Object.entries(vector).map(([k, v]) => `${JSON.stringify(k)}: ${v}`);
  return `{${parts.join(", ")}}`;
}

/**
 * The `{ }` text: a real `gdsx.sim.sensitivity` call, not a paraphrase.
 *
 * A single-pulse sweep from an idle baseline IS `sensitivity.map`, so that is
 * what it shows. Every other recipe is a sweep of `probe` calls, because
 * `map` fixes the perturbation to one cycle at a time -- so those show the loop
 * that runs them, which is what the player would actually write.
 */
export function callText(
  ctx: ExperimentContext,
  recipe: RecipeId,
  params: RecipeParams,
): string {
  const watch = `[${params.watch.map((n) => JSON.stringify(n)).join(", ")}]`;
  const reset = Object.keys(ctx.resetVector).length
    ? `, reset=${pyDict(ctx.resetVector)}`
    : "";
  const header =
    "from gdsx.sim import sensitivity\n" +
    `tape = gdsx.api.sim_compile(handle)   # or: design.tape()\n`;

  if (recipe === "single-pulse" && ctx.baseline.size === 0) {
    return (
      header +
      `sensitivity.map(\n` +
      `    tape,\n` +
      `    cycles=${ctx.cycles},\n` +
      `    baseline=${pyDict(ctx.baseVector)},\n` +
      `    perturb=${pyDict({ [ctx.keyPort]: 1 })},\n` +
      `    watch=${watch}${reset},\n` +
      `)`
    );
  }

  const key = JSON.stringify(ctx.keyPort);
  const baselinePulses = [...ctx.baseline.keys()].sort((a, b) => a - b);
  const stimulus =
    ctx.baseline.size === 0
      ? "base = {}"
      : `base = {c: {${key}: 1} for c in [${baselinePulses.join(", ")}]}`;

  const singleFirst = params.firstFrom === params.firstTo;
  const perturbation =
    recipe === "gap"
      ? singleFirst
        ? `{**base, ${params.firstFrom}: {${key}: 1}, ${params.firstFrom} + gap: {${key}: 1}}`
        : `{**base, first: {${key}: 1}, first + gap: {${key}: 1}}`
      : recipe === "bit-flip"
        ? `{**base, c: {${key}: 0 if c in base else 1}}`
        : `{**base, c: {${key}: 1}}`;
  // The gap recipe's outer loop over `first` only appears once the field is a
  // range; a single value keeps the original one-loop call unchanged.
  const loop =
    recipe === "gap"
      ? singleFirst
        ? `for gap in range(${params.minGap}, ${params.maxGap + 1}):`
        : `for first in range(${params.firstFrom}, ${params.firstTo + 1}):\n` +
          `    for gap in range(${params.minGap}, ${params.maxGap + 1}):`
      : `for c in range(${params.from}, ${params.to}):`;
  const indent = recipe === "gap" && !singleFirst ? "        " : "    ";

  return (
    header +
    `${stimulus}\n` +
    `${loop}\n` +
    `${indent}result = sensitivity.probe(\n` +
    `${indent}    tape,\n` +
    `${indent}    ${pyDict(ctx.baseVector)},\n` +
    `${indent}    ${perturbation},\n` +
    `${indent}    watch=${watch},\n` +
    `${indent}    cycles=${ctx.cycles}${reset},\n` +
    `${indent})\n` +
    `${indent}print(result.changed)`
  );
}

// ---- turning the sequence editor into a baseline ------------------------

/**
 * The player's key, as perturbations of a constant baseline.
 *
 * Every cycle where a track differs from its base level becomes an override for
 * that cycle, which is the form `sensitivity.probe` takes -- so "sweep against
 * the key I am holding" and "sweep against an idle design" are the same call
 * with a different `perturbations`, not two code paths.
 */
export function perturbationsOf(
  tracks: Record<string, string>,
  baseVector: Vector,
  cycles: number,
  ports: readonly string[],
): Map<number, Vector> {
  const found = new Map<number, Vector>();
  for (let c = 0; c < cycles; c++) {
    let override: Vector | null = null;
    for (const port of ports) {
      const bits = tracks[port] ?? "";
      const value = c < bits.length && bits[c] === "1" ? 1 : 0;
      if (value !== (baseVector[port] ?? 0)) {
        override ??= {};
        override[port] = value;
      }
    }
    if (override) found.set(c, override);
  }
  return found;
}

/** A perturbation set rendered back into sequence-editor tracks, for "load into the waveform". */
export function tracksOf(
  perturbations: PerturbationSet,
  baseVector: Vector,
  cycles: number,
  ports: readonly string[],
): Record<string, string> {
  const tracks: Record<string, string> = {};
  for (const port of ports) {
    let bits = "";
    for (let c = 0; c < cycles; c++) {
      const value = perturbations.get(c)?.[port] ?? baseVector[port] ?? 0;
      bits += value === 1 ? "1" : "0";
    }
    tracks[port] = bits;
  }
  return tracks;
}