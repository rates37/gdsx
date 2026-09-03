// Turning a rule the player has stated in words into the constraint rows
// that say the same thing, for the Constraints drawer's two generators.
//
// These exist because of a size problem, not a knowledge problem. "Exactly
// two pulses per epoch" is eleven rows of eleven cycles and "no two pulses
// closer than one position in neighbouring epochs" is four hundred and
// twenty forbidden pairs; nobody is going to type either, and a drawer that
// demanded it would be a text box rather than a tool. What they are not is a
// short cut past the investigation: every argument below is a number the
// player measured, the functions know nothing about any particular design,
// and neither has a default. Pure combinatorics over cycle numbers -- no
// DOM, no design handle -- so the browser and a node test can both run them.

import type { ConstraintView } from "../gdsx-types.ts";

/**
 * Cycles `from…to` split into classes by `cycle div n` or `cycle mod n`,
 * with one "exactly `k` of these" row per class.
 *
 * "Every epoch holds exactly two pulses" over a 121-cycle window of 11-cycle
 * epochs is `partitionRows(0, 120, 11, "div", 2)`; the same window counted
 * by position within the epoch is the `"mod"` form.
 */
export function partitionRows(
  from: number,
  to: number,
  n: number,
  mode: "div" | "mod",
  k: number,
): ConstraintView[] {
  const classes = new Map<number, number[]>();
  for (let c = from; c <= to; c++) {
    const key = mode === "div" ? Math.floor(c / n) : c % n;
    const bucket = classes.get(key);
    if (bucket) bucket.push(c);
    else classes.set(key, [c]);
  }
  return [...classes.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([key, elements]) => ({
      name: `${mode} ${n} = ${key}`,
      elements,
      lb: k,
      ub: k,
    }));
}

/**
 * One "at most one of these two" row per pair of cycles that sit too close
 * together, reading each cycle as `(epoch, position)` with
 * `epoch = cycle div epochLen` and `position = cycle mod epochLen`.
 *
 * There is deliberately no wraparound. In an 11-long epoch, positions 0 and
 * 10 are ten apart, not one; a version that wrapped them would be asserting
 * a different measurement from the one the gap sweep actually produces, and
 * it is precisely the un-trapped wide pairs that make the rule convincing.
 */
export function spacingRows(
  from: number,
  to: number,
  epochLen: number,
  dp: number,
  de: number,
): ConstraintView[] {
  const rows: ConstraintView[] = [];
  for (let a = from; a <= to; a++) {
    for (let b = a + 1; b <= to; b++) {
      const dPosition = Math.abs((a % epochLen) - (b % epochLen));
      const dEpoch = Math.abs(Math.floor(a / epochLen) - Math.floor(b / epochLen));
      if (dPosition <= dp && dEpoch <= de) {
        rows.push({ name: `spacing ${a},${b}`, elements: [a, b], lb: 0, ub: 1 });
      }
    }
  }
  return rows;
}

/** Whether a chosen set of cycles satisfies one row -- the check the solver
 *  does, exposed so a caller can say why a candidate failed without a round
 *  trip to Python. */
export function satisfies(row: ConstraintView, chosen: ReadonlySet<number>): boolean {
  let hit = 0;
  for (const e of row.elements) if (chosen.has(e)) hit++;
  return row.lb <= hit && hit <= row.ub;
}