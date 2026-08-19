// Points and coverage. Every tunable number in the game is in this file.
//
// Two rules from game-plan.md §8, and one from §5:
//
//   * Score is computed continuously and SHOWN only after solving. Live points
//     turn an investigation into a timed exam, which is the opposite of what
//     this is for.
//   * A disproof scores. Finding that a previous conclusion was wrong is the
//     most valuable thing that happens in a real session, and a scoring model
//     that ignored it would be teaching the wrong lesson.
//   * Every PROVEN outscores every LIKELY. That ordering is the requirement;
//     the exact numbers are a starting point and are meant to be tuned here.
//
// Coverage IS shown live (§3's title bar, §5's "this is the real progress
// bar"), because it measures how much of the design you have explained rather
// than how well you are doing.

import type { ClaimRecord } from "./model.ts";
import { flopsOf, latest, netsOf } from "./model.ts";
import type { Notebook } from "./store.ts";

export const POINTS = {
  /** Derived over every case. The most a single claim is worth. */
  provenExhaustive: 10,
  /** Wrong, and now known to be wrong, with a witness. Nearly as valuable. */
  disproven: 8,
  /** A fact read out of the netlist. Real, but it did not cost anything. */
  provenStructural: 6,
  /** No counterexample found. Worth something, worth less than a proof. */
  likely: 3,
  /** Nothing was learned. */
  unknown: 0,
} as const;

export function pointsFor(record: ClaimRecord): number {
  const current = latest(record);
  if (!current) return 0;
  switch (current.verdict.kind) {
    case "PROVEN":
      return current.verdict.method === "structural"
        ? POINTS.provenStructural
        : POINTS.provenExhaustive;
    case "DISPROVEN":
      return POINTS.disproven;
    case "LIKELY":
      return POINTS.likely;
    case "UNKNOWN":
      return POINTS.unknown;
  }
}

export function score(notebook: Notebook): number {
  return notebook.all().reduce((total, record) => total + pointsFor(record), 0);
}

export interface Coverage {
  /** 0..1, what the title bar shows. */
  fraction: number;
  /** Flops with a proven role claim, over all flops. */
  roles: { done: number; total: number };
  /** Nets of the success cone named by a settled claim, over the cone. */
  cone: { done: number; total: number };
}

/**
 * §5's definition: half the fraction of flops with a proven role claim, half
 * the fraction of the success cone explained.
 *
 * "Explained" counts claims that were *settled* -- proven or disproven -- not
 * claims that were merely made. Finding out a net does not do what you thought
 * is understanding it; writing a guess down is not.
 */
export function coverage(
  notebook: Notebook,
  allFlops: readonly string[],
  successCone: readonly string[],
): Coverage {
  const rolesDone = new Set<string>();
  const coneDone = new Set<string>();
  const cone = new Set(successCone);

  for (const record of notebook.all()) {
    const current = latest(record);
    if (!current) continue;
    const settled =
      current.verdict.kind === "PROVEN" || current.verdict.kind === "DISPROVEN";
    if (!settled) continue;

    if (record.claim.kind === "role" && current.verdict.kind === "PROVEN") {
      for (const flop of flopsOf(record.claim)) rolesDone.add(flop);
    }
    for (const net of netsOf(record.claim)) if (cone.has(net)) coneDone.add(net);
  }

  const roles = { done: rolesDone.size, total: allFlops.length };
  const coneScore = { done: coneDone.size, total: cone.size };
  const half = (part: { done: number; total: number }) =>
    part.total === 0 ? 0 : part.done / part.total;
  return {
    fraction: 0.5 * half(roles) + 0.5 * half(coneScore),
    roles,
    cone: coneScore,
  };
}

export function asPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}