// Owns one running gate-tape simulation: the per-input-port sequences the
// Sequence Editor edits, and the full cycle-by-cycle history both the
// Sequence Editor and the Waveform panel read from. A full rerun (tape.ops
// evaluated once per cycle) is well under a millisecond even at a few
// hundred cycles, so every edit just reruns from scratch rather than trying
// to patch history incrementally -- that keeps "scrubbing is instant" true
// without a cache-invalidation story to get wrong.
//
// Puzzle-specific knowledge (which port is the clock, what the reset pulse
// looks like) does not live here -- this only knows "primary inputs" and
// "an optional one-time step before cycle 0". The puzzle-specific values are
// supplied by whoever constructs the store (see main.ts).

import type { GateTape } from "./tape.ts";
import { GateTapeExecutor } from "./executor.ts";

type Listener = () => void;

export class SimStore {
  readonly tape: GateTape;
  readonly executor: GateTapeExecutor;
  /** Every primary input except `clk` -- the tape ignores its value, so it is never a track. */
  readonly inputPorts: string[];
  cycles: number;
  /** When true (the default), every edit reruns immediately. When false,
   *  edits only mark the run `dirty` -- call `recompute()` (the Sequence
   *  Editor's Run button) to apply them. */
  autoRun = true;
  /** Wall-clock cost of the last actual run, for the "why would I need a
   *  Run button, this is instant" answer to be something you can see. */
  lastRunMs = 0;

  /** The one-time step before cycle 0, if the puzzle has a reset protocol.
   *  Public because anything replaying a stimulus outside this store -- the
   *  notebook's sequential checks, for one -- has to drive the design exactly
   *  the way the waveform does, and re-deriving the protocol at each such site
   *  is how two parts of the app come to disagree about cycle 0. */
  readonly resetVector: Readonly<Record<string, number>>;

  private dirty = false;
  /** True once the player has edited a track through the Sequence Editor.
   *  Separate from bit content: a puzzle can start a port at a nonzero
   *  `initialLevels` value, which reads identically to a driven stimulus if
   *  you only look at the bits. Anything reporting "against the current
   *  stimulus" needs this, not a bit scan. */
  private edited = false;
  private readonly tracks = new Map<string, Uint8Array>();
  private valueHistory: Int32Array[] = [];
  private stateHistory: Int32Array[] = [];
  private readonly flopIndex = new Map<string, number>();
  private readonly listeners = new Set<Listener>();

  constructor(
    tape: GateTape,
    cycles: number,
    opts: { resetVector?: Record<string, number>; initialLevels?: Record<string, 0 | 1> } = {},
  ) {
    this.tape = tape;
    this.executor = new GateTapeExecutor(tape);
    this.cycles = cycles;
    this.resetVector = opts.resetVector ?? {};

    const idToName = new Map(Object.entries(tape.header.names).map(([n, i]) => [i, n]));
    this.inputPorts = tape.header.inputs
      .map((id) => idToName.get(id))
      .filter((name): name is string => name !== undefined && name !== "clk")
      .sort();
    for (const name of this.inputPorts) {
      const bits = new Uint8Array(cycles);
      bits.fill(opts.initialLevels?.[name] ?? 0);
      this.tracks.set(name, bits);
    }
    tape.header.flop_names.forEach((name, i) => this.flopIndex.set(name, i));

    this.run();
  }

  /** Grow or shrink every track, preserving whatever fits, then rerun (subject to `autoRun`). */
  setCycles(cycles: number): void {
    for (const [name, bits] of this.tracks) {
      const grown = new Uint8Array(cycles);
      grown.set(bits.subarray(0, Math.min(cycles, bits.length)));
      this.tracks.set(name, grown);
    }
    this.cycles = cycles;
    this.requestRun();
  }

  bitsOf(port: string): Uint8Array {
    return this.tracks.get(port) ?? new Uint8Array(this.cycles);
  }

  setBit(port: string, cycle: number, value: 0 | 1): void {
    const bits = this.tracks.get(port);
    if (!bits || cycle < 0 || cycle >= bits.length || bits[cycle] === value) return;
    bits[cycle] = value;
    this.edited = true;
    this.requestRun();
  }

  setRange(port: string, from: number, to: number, value: 0 | 1): void {
    const bits = this.tracks.get(port);
    if (!bits) return;
    const lo = Math.max(0, Math.min(from, to));
    const hi = Math.min(bits.length - 1, Math.max(from, to));
    let changed = false;
    for (let i = lo; i <= hi; i++) {
      if (bits[i] !== value) {
        bits[i] = value;
        changed = true;
      }
    }
    if (changed) {
      this.edited = true;
      this.requestRun();
    }
  }

  /** Replace a whole track from a bit string ("0101...") or array of 0/1, for import. */
  setPattern(port: string, bits: string | ArrayLike<number>): void {
    const arr = this.tracks.get(port);
    if (!arr) return;
    const src = typeof bits === "string" ? bits.trim() : bits;
    for (let i = 0; i < arr.length; i++) {
      const v = i < src.length ? Number(src[i]) : 0;
      arr[i] = v ? 1 : 0;
    }
    this.edited = true;
    this.requestRun();
  }

  /** Turn automatic rerunning on or off. Turning it back on while dirty
   *  catches up immediately, so nothing is silently left stale. */
  setAutoRun(auto: boolean): void {
    this.autoRun = auto;
    if (auto && this.dirty) this.run();
    else this.notify();
  }

  /** True when a track has changed since the last actual run -- only
   *  possible while `autoRun` is off. What the Run button answers. */
  isDirty(): boolean {
    return this.dirty;
  }

  /** Run now regardless of `autoRun` -- the Sequence Editor's Run button. */
  recompute(): void {
    this.run();
  }

  /** True once the player has driven a stimulus through the Sequence Editor
   *  (paint, drag-range, or import) -- as opposed to every track still
   *  sitting at the puzzle's starting `initialLevels`. */
  hasStimulus(): boolean {
    return this.edited;
  }

  private requestRun(): void {
    this.dirty = true;
    if (this.autoRun) this.run();
    else this.notify();
  }

  private run(): void {
    const t0 = performance.now();
    this.executor.reset();
    if (Object.keys(this.resetVector).length > 0) this.executor.step(this.resetVector);

    const cycles = this.cycles;
    this.valueHistory = new Array(cycles);
    this.stateHistory = new Array(cycles);
    const inputs: Record<string, number> = {};
    for (let c = 0; c < cycles; c++) {
      for (const [name, bits] of this.tracks) inputs[name] = bits[c];
      this.executor.step(inputs);
      this.valueHistory[c] = this.executor.values.slice();
      this.stateHistory[c] = this.executor.state.slice();
    }
    this.dirty = false;
    this.lastRunMs = performance.now() - t0;
    this.notify();
  }

  private notify(): void {
    for (const l of this.listeners) l();
  }

  netValueAt(cycle: number, net: string): number {
    const idx = this.tape.header.names[net];
    const snap = this.valueHistory[cycle];
    return idx !== undefined && snap ? snap[idx] : 0;
  }

  flopValueAt(cycle: number, flopName: string): number {
    const idx = this.flopIndex.get(flopName);
    const snap = this.stateHistory[cycle];
    return idx !== undefined && snap ? snap[idx] : 0;
  }

  /** The first cycle where `net` reads 1 and stays 1 through the end of the
   *  run, or null if it never does -- "latches high", not just "is high
   *  once". Used to report when a sticky flag like `success` has actually
   *  taken hold, not just pulsed. */
  firstLatchedHigh(net: string): number | null {
    for (let c = 0; c < this.cycles; c++) {
      if (this.netValueAt(c, net) !== 1) continue;
      let staysHigh = true;
      for (let k = c; k < this.cycles; k++) {
        if (this.netValueAt(k, net) !== 1) {
          staysHigh = false;
          break;
        }
      }
      if (staysHigh) return c;
    }
    return null;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

/** An unsigned little-endian bus value at one cycle, from bit0..bitN net names, e.g. ["O[0]", ..., "O[7]"]. */
export function busValueAt(store: SimStore, cycle: number, bitNets: readonly string[]): number {
  let value = 0;
  for (let i = 0; i < bitNets.length; i++) value |= store.netValueAt(cycle, bitNets[i]) << i;
  return value >>> 0;
}

/**
 * Every bus in the tape: a prefix with contiguous `[0]`..`[n]` bits, and its
 * bit nets in that order.
 *
 * Two panels used to look for the literal name `"O"` and give up if the
 * design did not have one — which is a fact about the puzzles that happen to
 * be baked, not about the app. Discovering buses from the tape means a level
 * whose output word is called something else shows one, and a level with no
 * bus at all shows none, without either being written down anywhere.
 *
 * `exclude` drops the driver's own input ports, so a puzzle whose key is a
 * bus does not have the stimulus offered back as if it were a result.
 */
export function discoverBuses(
  store: SimStore,
  exclude: readonly string[] = [],
): { prefix: string; bits: string[] }[] {
  const skip = new Set(exclude.map((name) => name.replace(/\[\d+\]$/, "")));
  const prefixes = new Set<string>();
  for (const name of Object.keys(store.tape.header.names)) {
    const m = /^(.+)\[0\]$/.exec(name);
    if (m && !skip.has(m[1])) prefixes.add(m[1]);
  }

  const buses: { prefix: string; bits: string[] }[] = [];
  for (const prefix of [...prefixes].sort()) {
    const bits: string[] = [];
    // Contiguous from bit 0. A gap ends the bus rather than being skipped:
    // the value is little-endian over these names and a hole would shift
    // every bit above it.
    for (let i = 0; ; i++) {
      const bit = `${prefix}[${i}]`;
      if (!(bit in store.tape.header.names)) break;
      bits.push(bit);
    }
    if (bits.length > 1) buses.push({ prefix, bits });
  }
  return buses;
}