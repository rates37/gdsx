// The vectors a model is judged on: random sets of varying
// density, plus structured near-misses.
//
// Both halves matter and they find different bugs:
//
//   * **Random keys at varying density** explore. A model that is right about
//     the design's shape but wrong about, say, saturation only fails at
//     densities the player never types by hand.
//   * **Near-misses of the key the player is holding** are where a model is
//     actually wrong, because that is the region it was written from. A model
//     built to explain one working key usually agrees with the design on that
//     key and disagrees one flipped cycle away.
//
// Seeded, so a re-run reproduces the same first divergence rather than a
// different one -- chasing a divergence that moves every time you press the
// button is not debugging.

import { seeded } from "../notebook/verify.ts";

/** A stimulus, as a model sees it: the cycles at which the key port is high. */
export type Pulses = number[];

export interface VectorPlan {
  /** Total vectors generated, the number the badge is quoted over. */
  count: number;
  cycles: number;
  seed: number;
}

/**
 * `count` pulse sets over `cycles` cycles.
 *
 * Vector 0 is always the player's own key unchanged, so the first thing a model
 * is asked is whether it explains the case the player already understands.
 * After that the generator alternates between near-misses of that key and fresh
 * random keys.
 */
export function generate(baseline: Pulses, plan: VectorPlan): Pulses[] {
  const rng = seeded(plan.seed);
  const vectors: Pulses[] = [normalise(baseline, plan.cycles)];
  const base = new Set(vectors[0]);

  while (vectors.length < plan.count) {
    // Alternating rather than coin-flipped: an even split of the two kinds is
    // guaranteed, so a small run is never accidentally all-random.
    vectors.push(
      vectors.length % 2 === 1 ? nearMiss(base, plan.cycles, rng) : random(plan.cycles, rng),
    );
  }
  return vectors;
}

function normalise(pulses: Pulses, cycles: number): Pulses {
  return [...new Set(pulses.filter((c) => c >= 0 && c < cycles))].sort((a, b) => a - b);
}

/** The player's key with one to three cycles flipped, in either direction. */
function nearMiss(base: ReadonlySet<number>, cycles: number, rng: () => number): Pulses {
  const pulses = new Set(base);
  const flips = 1 + Math.floor(rng() * 3);
  for (let i = 0; i < flips; i++) {
    const at = Math.floor(rng() * cycles);
    if (pulses.has(at)) pulses.delete(at);
    else pulses.add(at);
  }
  return [...pulses].sort((a, b) => a - b);
}

/** A fresh key at a density drawn from 5% to 50%. */
function random(cycles: number, rng: () => number): Pulses {
  const density = 0.05 + rng() * 0.45;
  const pulses: Pulses = [];
  for (let c = 0; c < cycles; c++) if (rng() < density) pulses.push(c);
  return pulses;
}

/** A pulse set as a sequence-editor bit string. */
export function bitsOf(pulses: Pulses, cycles: number): string {
  const high = new Set(pulses);
  let bits = "";
  for (let c = 0; c < cycles; c++) bits += high.has(c) ? "1" : "0";
  return bits;
}

/** A bit string back into a pulse set. */
export function pulsesOf(bits: string): Pulses {
  const pulses: Pulses = [];
  for (let c = 0; c < bits.length; c++) if (bits[c] === "1") pulses.push(c);
  return pulses;
}