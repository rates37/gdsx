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
// Coverage is measured continuously and shown in the Notebook panel, where
// the claims that move it live. It is deliberately NOT in the title bar any
// more: a number pinned beside the submit button is received as a score
// whatever it is labelled, which is the live pressure §8 rules out, and
// `coverage 0%` next to an accepted verdict was being read as a bug rather
// than as "you have not written anything down".

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
  /** §8's ordering, and the design's whole opinion: explaining the design
   *  outscores unlocking it. A model that agrees with the gate tape over every
   *  generated vector is the most valuable thing a player produces, and solving
   *  is required but cheap. Awarded by the Model Builder, not by a claim --
   *  see web/src/model/store.ts for what "validated" is allowed to mean. */
  modelValidated: 40,
  /** Required, and low. You can brute-force your way to the flag; you cannot
   *  brute-force a good score. */
  solved: 10,
  /** §8's "high": the full coverage fraction, scaled. Second only to a
   *  validated model, and worth more than any amount of claim-writing --
   *  explaining the whole design beats explaining five things about it
   *  very thoroughly. */
  coverageMax: 25,
  /** §8's "medium", and a CAP rather than a weight: `pointsFor` is summed over
   *  every claim and is otherwise unbounded, so without this, twenty
   *  structural claims outscore a validated model and §8's stated ordering
   *  quietly inverts. Two exhaustive proofs reach it. */
  claimsMax: 20,
  /** §8's "small bonus, capped". Full marks at or under par, nothing at twice
   *  par -- see `timeBonus`. */
  timeMax: 5,
  /** §8's "small penalty, never blocking", per tier revealed. Charged even on
   *  a hint taken after solving: the tiers are analyses you could have run,
   *  and reading one is reading one. */
  hintPenalty: 3,
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

// ---------------------------------------------------------------------------
// The score (§8). Assembled here, shown only after a solve -- see
// workspace/toolbar.ts's accepted verdict and notebook/writeup.ts.
// ---------------------------------------------------------------------------

/** One row of the breakdown. `available` is what the component was worth, so
 *  the write-up can say "12 of 25" rather than a bare number the player has no
 *  scale for. Negative `earned` (hints) has `available: 0`. */
export interface ScoreLine {
  name: string;
  earned: number;
  available: number;
  /** Why it came out that way, in the player's terms. Always present: a
   *  breakdown that says "coverage 6" and nothing else is a grade, not an
   *  explanation. */
  note: string;
}

export interface ScoreCard {
  /** 0..`available`. Floored at `POINTS.solved` for a solved puzzle, so hints
   *  can never take back the points solving earned (§8: "never blocking"). */
  total: number;
  /** 100, or 95 for a puzzle that declares no par time. */
  available: number;
  lines: ScoreLine[];
  /** False produces an empty card: §8 shows a score only after solving. */
  solved: boolean;
  /** True when the puzzle was solved with nothing in the notebook -- no
   *  settled claims and no coverage. Not a penalty and not an error: the
   *  presentation uses it to say what the rest of the points are FOR, rather
   *  than leaving a 10/100 looking like a failure. */
  bare: boolean;
}

export interface ScoreInput {
  solved: boolean;
  /** Null when coverage has not been measured -- the Notebook panel owns the
   *  cone denominator and only computes it while it is open, so a player who
   *  closed the panel and then submitted has no measurement to score. The
   *  breakdown says exactly that rather than reporting a 0% nobody measured. */
  coverage: Coverage | null;
  /** `score(notebook)` -- the raw, uncapped sum. */
  claimPoints: number;
  /** The Model Builder's badge, not its latest run: a model that was validated
   *  and has since been edited was still validated. */
  modelValidated: boolean;
  /** Engaged milliseconds at the solve, or null when unsolved or unrecorded. */
  solveMs: number | null;
  /** The puzzle's par, or null if it declares none -- the time component is
   *  then omitted entirely rather than scored zero. */
  parMinutes: number | null;
  hintsTaken: number;
}

/**
 * §8's "small bonus, capped": full marks at or under par, falling linearly to
 * nothing at twice par, and nothing beyond.
 *
 * A bonus and never a penalty, which is the whole shape of it -- a slow solve
 * loses five points it never had, and no clock is ever shown while playing.
 */
function timeBonus(solveMs: number, parMinutes: number): number {
  const minutes = solveMs / 60_000;
  if (minutes <= parMinutes) return POINTS.timeMax;
  const overrun = (minutes - parMinutes) / parMinutes; // 0 at par, 1 at twice par
  return Math.max(0, Math.round(POINTS.timeMax * (1 - overrun)));
}

/** A duration a player can read. "0 min" is a wrong-looking way to write a
 *  fast solve, and the sub-minute case is real -- a replayed key is submitted
 *  within seconds. */
export function asDuration(ms: number): string {
  const mins = Math.round(ms / 60_000);
  return mins < 1 ? "under a minute" : `${mins} min`;
}

/**
 * The score, as a breakdown rather than a number.
 *
 * Pure and store-free -- every input is already computed by its owner -- so
 * this is callable from a test with no browser, like everything else in this
 * file. §8's ordering is in the weights, not here: model (40) beats coverage
 * (25) beats claims (20) beats solving (10) beats the time bonus (5).
 */
export function scoreCard(input: ScoreInput): ScoreCard {
  if (!input.solved) {
    return { total: 0, available: 0, lines: [], solved: false, bare: false };
  }

  const lines: ScoreLine[] = [];

  lines.push({
    name: "Model validated",
    earned: input.modelValidated ? POINTS.modelValidated : 0,
    available: POINTS.modelValidated,
    note: input.modelValidated
      ? "your model agreed with the gate tape over every generated vector"
      : "no validated model — the highest-scoring thing you can build",
  });

  const found = input.coverage;
  const coverageEarned = found === null ? 0 : Math.round(POINTS.coverageMax * found.fraction);
  lines.push({
    name: "Coverage",
    earned: coverageEarned,
    available: POINTS.coverageMax,
    note:
      found === null
        ? "not measured — open the Notebook to see it"
        : `${asPercent(found.fraction)} explained — ` +
          `roles ${found.roles.done}/${found.roles.total}, ` +
          `cone ${found.cone.done}/${found.cone.total}`,
  });

  const claimsEarned = Math.min(POINTS.claimsMax, input.claimPoints);
  lines.push({
    name: "Claims settled",
    earned: claimsEarned,
    available: POINTS.claimsMax,
    note:
      input.claimPoints > POINTS.claimsMax
        ? `${input.claimPoints} claim points, capped at ${POINTS.claimsMax}`
        : `${input.claimPoints} claim points — a disproof scores nearly as much as a proof`,
  });

  lines.push({
    name: "Solved",
    earned: POINTS.solved,
    available: POINTS.solved,
    note: "required, and deliberately cheap",
  });

  // A puzzle with no par is missing the yardstick, not the time -- scoring it
  // zero out of five would read as a slow solve. The component is dropped and
  // `available` falls with it, so the denominator stays honest.
  let available = POINTS.modelValidated + POINTS.coverageMax + POINTS.claimsMax + POINTS.solved;
  if (input.parMinutes !== null && input.solveMs !== null) {
    const earned = timeBonus(input.solveMs, input.parMinutes);
    available += POINTS.timeMax;
    lines.push({
      name: "Time",
      earned,
      available: POINTS.timeMax,
      note: `${asDuration(input.solveMs)} against a par of ${input.parMinutes} min`,
    });
  }

  if (input.hintsTaken > 0) {
    lines.push({
      name: "Hints taken",
      earned: -POINTS.hintPenalty * input.hintsTaken,
      available: 0,
      note: `${input.hintsTaken} revealed, ${POINTS.hintPenalty} points each`,
    });
  }

  const raw = lines.reduce((sum, line) => sum + line.earned, 0);
  return {
    // §8: hints are "never blocking". Taking every tier can cost a player the
    // points they earned by explaining the design, but never the ones they
    // earned by solving it.
    total: Math.max(POINTS.solved, raw),
    available,
    lines,
    solved: true,
    bare: claimsEarned === 0 && coverageEarned === 0,
  };
}