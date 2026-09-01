// Points and coverage. Every tunable number in the game is in this file.
//
// Two rules for scoring, and one for coverage:
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
// whatever it is labelled, which is the live pressure ruled out above, and
// `coverage 0%` next to an accepted verdict was being read as a bug rather
// than as "you have not written anything down".

import { VALIDATION_VECTORS } from "../model/store.ts";
import type { ClaimRecord, ProvenMethod } from "./model.ts";
import { flopsOf, latest, netsOf } from "./model.ts";
import type { Notebook } from "./store.ts";

export const POINTS = {
  /** Derived over every case. The most a single claim is worth. */
  provenExhaustive: 10,
  /** Wrong, and now known to be wrong, with a witness. Nearly as valuable. */
  disproven: 8,
  /** A fact read out of the netlist. Real, but it did not cost anything.
   *  Note what this is NOT: it is the price of a claim the LIBRARY settled as a
   *  graph fact, which includes a `support` claim's leaf set and a
   *  `requirement`'s backward justification. Both are most of the work of
   *  understanding a cone. See `readOut` for the kind that really is free. */
  provenStructural: 6,
  /** A deterministic replay of one stimulus (sequential.ts's `checkTiming`).
   *  Real behavioural evidence, and quantified over nothing -- so it sits above
   *  a sampled search and below a sweep that visited every case. */
  provenReplay: 5,
  /** No counterexample found. Worth something, worth less than a proof. */
  likely: 3,
  /** A `structural`-KIND claim -- "<net> is driven by <cell>" -- under any
   *  settled verdict. The Netlist panel already shows the player this, so
   *  asserting it is reading the game's own answer back to it. Paid flat,
   *  right or wrong: pricing the wrong answer higher than the right one made
   *  deliberate nonsense the cheapest points in the game. */
  readOut: 2,
  /** Nothing was learned. */
  unknown: 0,
  /** The ordering, and the design's whole opinion: explaining the design
   *  outscores unlocking it. A model that agrees with the gate tape over every
   *  generated vector is the most valuable thing a player produces, and solving
   *  is required but cheap. Awarded by the Model Builder, not by a claim --
   *  see web/src/model/store.ts for what a run is allowed to mean.
   *
   *  Paid as a SLOPE, not as a threshold: `VALIDATION_VECTORS` agreeing
   *  vectors earn all of it, half that many earn half. The badge's cliff is
   *  right for a badge -- it is one statement, true or not -- and wrong for
   *  40% of a score, where it made 199 clean vectors worth exactly as much as
   *  a model that was never written. Total agreement is still non-negotiable:
   *  a run with one mismatch earns nothing here however long it was. */
  modelValidated: 30,
  /** The other half of the model component: how much of the design the model
   *  was actually asked about.
   *
   *  `compare()` watches whatever the MODEL returns, so a model that reports
   *  `{success}` alone can agree on every vector while explaining one bit of
   *  the design. That is a real result and it is not the whole one. This pays
   *  the fraction of the puzzle's OWN observables -- its lock, and the nets
   *  its checks read -- that the model reproduced, and only ones the design
   *  actually moved during the run: agreeing about a signal that never
   *  changed is not coverage of it. */
  modelScope: 10,
  /** Required, and low. You can brute-force your way to the flag; you cannot
   *  brute-force a good score. */
  solved: 10,
  /** The "high" tier: the full coverage fraction, scaled. Second only to a
   *  validated model, and worth more than any amount of claim-writing --
   *  explaining the whole design beats explaining five things about it
   *  very thoroughly. */
  coverageMax: 25,
  /** The "medium" tier, and a CAP rather than a weight: `pointsFor` is summed over
   *  every claim and is otherwise unbounded, so without this, twenty
   *  structural claims outscore a validated model and the stated ordering
   *  quietly inverts. Two exhaustive proofs reach it. */
  claimsMax: 20,
  /** A small bonus, capped. Full marks at or under par, nothing at twice
   *  par -- see `timeBonus`. */
  timeMax: 5,
  /** A small penalty, never blocking, per tier revealed. Charged even on
   *  a hint taken after solving: the tiers are analyses you could have run,
   *  and reading one is reading one.
   *
   *  This was re-opened and DECIDED to stay as it is. The case against it is
   *  real -- a hint read after the key is cracked cannot have helped crack it
   *  -- but the score is not only about cracking the key: most of it is for
   *  explaining the design, and every one of those points is still on the
   *  table after the solve (`rescoreIfSolved` in boot.ts exists precisely
   *  because they are). A tier read at that point is still a piece of the
   *  explanation someone else did for you. And the alternative is not free:
   *  store/progress.ts keeps `hintsTaken` as a single count with no
   *  timestamps, so "charge only pre-solve hints" needs a stored-format
   *  change plus a migration for every existing record, whose old count would
   *  have to be guessed at as all-before or all-after. Not worth it for a
   *  three-point rule that is capped by the floor below anyway. */
  hintPenalty: 3,
} as const;

/**
 * What a cone net named ONLY by a read-out is worth against the cone.
 *
 * Not in `POINTS` on purpose: it is a fraction of a net, not a number of
 * points, and summing it with the entries above would be meaningless. Naming a
 * net you have not explained is worth something -- you did have to find it --
 * but it is not the same as having explained it.
 */
export const READ_OUT_COVERAGE = 0.25;

/**
 * A claim whose evidence the player could have read off the Netlist panel.
 *
 * Keyed on the claim's KIND, never on `verdict.method`. Those are different
 * words for different things: the library stamps `method: "structural"` on
 * every graph fact it settles, which includes `support` and `requirement`
 * proofs, and those are not read-outs -- stating a D-cone's leaf set correctly
 * is most of the work of understanding that cone. Discounting them would
 * invert the exact lesson this game is for.
 */
function isReadOut(record: ClaimRecord): boolean {
  return record.claim.kind === "structural";
}

/**
 * What each way of settling a claim is worth.
 *
 * A table rather than a switch so that a fourth PROVEN method cannot be added
 * without pricing it: `Record<ProvenMethod, number>` makes that a compile
 * error, where a switch would quietly fall through to whatever came next. That
 * is the same reason `verdictStyle` has no `default` branch.
 */
const PROVEN_POINTS: Record<ProvenMethod, number> = {
  exhaustive: POINTS.provenExhaustive,
  replay: POINTS.provenReplay,
  structural: POINTS.provenStructural,
};

export function pointsFor(record: ClaimRecord): number {
  const current = latest(record);
  if (!current) return 0;
  // A read-out is priced by its kind, not by how it came out -- but an
  // UNKNOWN still taught nobody anything, so it stays at zero like every
  // other kind. (LIKELY is unreachable here: the library settles structural
  // claims outright and never samples.)
  if (isReadOut(record)) {
    return current.verdict.kind === "UNKNOWN" ? POINTS.unknown : POINTS.readOut;
  }
  switch (current.verdict.kind) {
    case "PROVEN":
      return PROVEN_POINTS[current.verdict.method];
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
  /** Nets of the success cone named by a settled claim, over the cone.
   *
   *  `done` counts nets a claim actually explained. `partial` counts nets named
   *  ONLY by a read-out, each worth `READ_OUT_COVERAGE` of a net in `fraction`.
   *  Kept as a separate integer rather than folded into a fractional `done` so
   *  the panel reads "cone 14/57 (+6 structural)" and never "cone 9.5/57". */
  cone: { done: number; partial: number; total: number };
}

/**
 * Coverage's definition: half the fraction of flops with a proven role claim, half
 * the fraction of the success cone explained.
 *
 * "Explained" counts claims that were *settled* -- proven or disproven -- not
 * claims that were merely made. Finding out a net does not do what you thought
 * is understanding it; writing a guess down is not.
 *
 * And naming a net is not explaining it either. A cone net whose only settled
 * claim is a read-out ("<net> is driven by <cell>", which the Netlist panel
 * already showed) counts at `READ_OUT_COVERAGE`, not in full -- otherwise the
 * fastest route to a full progress bar is to transcribe the netlist.
 */
export function coverage(
  notebook: Notebook,
  allFlops: readonly string[],
  successCone: readonly string[],
): Coverage {
  const rolesDone = new Set<string>();
  const coneDone = new Set<string>();
  const conePartial = new Set<string>();
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
    const into = isReadOut(record) ? conePartial : coneDone;
    for (const net of netsOf(record.claim)) if (cone.has(net)) into.add(net);
  }

  // A net named by both a read-out and a real claim was explained: full credit,
  // counted once. Done after the loop rather than during it, because the two
  // claims can be settled in either order.
  for (const net of coneDone) conePartial.delete(net);

  const roles = { done: rolesDone.size, total: allFlops.length };
  const coneScore = {
    done: coneDone.size,
    partial: conePartial.size,
    total: cone.size,
  };
  const half = (part: { done: number; total: number }) =>
    part.total === 0 ? 0 : part.done / part.total;
  const coneHalf =
    coneScore.total === 0
      ? 0
      : (coneScore.done + READ_OUT_COVERAGE * coneScore.partial) / coneScore.total;
  return {
    fraction: 0.5 * half(roles) + 0.5 * coneHalf,
    roles,
    cone: coneScore,
  };
}

/**
 * The coverage line, in the one place that decides how it reads.
 *
 * Shared by the Notebook panel's live line and the score card's note so the
 * two can never word the same measurement differently. The `(+N structural)`
 * tail appears only when there is one -- a player with no read-outs is not
 * shown an empty parenthetical explaining a rule they did not hit.
 */
export function coverageText(found: Coverage): string {
  const tail = found.cone.partial > 0 ? ` (+${found.cone.partial} structural)` : "";
  return (
    `roles ${found.roles.done}/${found.roles.total} · ` +
    `cone ${found.cone.done}/${found.cone.total}${tail}`
  );
}

export function asPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

// ---------------------------------------------------------------------------
// The score. Assembled here, shown only after a solve -- see
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
   *  can never take back the points solving earned -- never blocking. */
  total: number;
  /** 100, less any component this puzzle cannot offer: 5 for one that declares
   *  no par time, 10 for one that names no observable of its own. */
  available: number;
  lines: ScoreLine[];
  /** False produces an empty card: a score is shown only after solving. */
  solved: boolean;
  /** True when the puzzle was solved with nothing in the notebook -- no
   *  settled claims and no coverage. Not a penalty and not an error: the
   *  presentation uses it to say what the rest of the points are FOR, rather
   *  than leaving a 10/100 looking like a failure. */
  bare: boolean;
}

/**
 * What the Model Builder produced, reduced to the two things the score reads.
 *
 * A "clean" run is one that agreed with the design on EVERY vector and was
 * asked something real (at least one observable the design actually moved).
 * Anything less is not partial credit here -- a model that is wrong about one
 * vector in a thousand is a wrong model, and the panel already shows exactly
 * which vector, which is the interesting part.
 */
export interface ModelEvidence {
  /** How many vectors that run compared. */
  vectors: number;
  /** The observables it actually tested: what the model reported, minus the
   *  ones the design held at a single value for the whole run. */
  tested: readonly string[];
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
  /** The best CLEAN run the Model Builder recorded, or null if there has
   *  never been one. Not the badge and not the latest run: a model that
   *  agreed on everything and has since been edited still agreed, and a model
   *  that never reached the badge's vector count still learned something.
   *  Shaped by boot.ts from the ModelStore -- this file stays store-free. */
  model: ModelEvidence | null;
  /** What this puzzle asks a model to reproduce: its lock, plus the nets its
   *  checks read. Derived in boot.ts from the descriptor the player is
   *  already shown as the objective. Empty for a puzzle that declares none --
   *  the scope component is then dropped and `available` falls with it, the
   *  same way `parMinutes` drops the time bonus. */
  requiredObservables: readonly string[];
  /** Engaged milliseconds at the solve, or null when unsolved or unrecorded. */
  solveMs: number | null;
  /** The puzzle's par, or null if it declares none -- the time component is
   *  then omitted entirely rather than scored zero. */
  parMinutes: number | null;
  hintsTaken: number;
}

/**
 * A small bonus, capped: full marks at or under par, falling linearly to
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
 * file. The ordering is in the weights, not here: model (30 + 10) beats
 * coverage (25) beats claims (20) beats solving (10) beats the time bonus (5).
 */
export function scoreCard(input: ScoreInput): ScoreCard {
  if (!input.solved) {
    return { total: 0, available: 0, lines: [], solved: false, bare: false };
  }

  const lines: ScoreLine[] = [];

  // The model, in two halves: how far your best clean run got, and how much
  // of the design it was asked about. Split because they fail independently
  // -- a thousand vectors over `success` alone is one of them without the
  // other, and so is a five-vector run over every observable the puzzle names.
  const model = input.model;
  const agreementFraction = model === null ? 0 : Math.min(1, model.vectors / VALIDATION_VECTORS);
  lines.push({
    name: "Model agreement",
    earned: Math.round(POINTS.modelValidated * agreementFraction),
    available: POINTS.modelValidated,
    note:
      model === null
        ? "no model agreed with the gate tape all the way through — the " +
          "highest-scoring thing you can build"
        : `your model agreed with the gate tape on all ${model.vectors} vectors of ` +
          `its best run, against ${VALIDATION_VECTORS} for full marks`,
  });

  // A puzzle that names no observable of its own has nothing to measure scope
  // against, so the component is dropped rather than scored zero -- the same
  // rule, for the same reason, as the time bonus on a puzzle with no par.
  const required = input.requiredObservables;
  let scopeEarned = 0;
  if (required.length > 0) {
    const tested = new Set(model?.tested ?? []);
    const covered = required.filter((name) => tested.has(name));
    scopeEarned = Math.round((POINTS.modelScope * covered.length) / required.length);
    lines.push({
      name: "Model scope",
      earned: scopeEarned,
      available: POINTS.modelScope,
      note:
        model === null
          ? `nothing to measure — the puzzle's own signals are ${required.join(", ")}`
          : `${covered.length} of the puzzle's ${required.length} own signals ` +
            `(${required.join(", ")}) were modelled and moved`,
    });
  }

  const found = input.coverage;
  const coverageEarned = found === null ? 0 : Math.round(POINTS.coverageMax * found.fraction);
  lines.push({
    name: "Coverage",
    earned: coverageEarned,
    available: POINTS.coverageMax,
    note:
      found === null
        ? "not measured — open the Notebook to see it"
        : `${asPercent(found.fraction)} explained — ${coverageText(found)}`,
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
  if (required.length > 0) available += POINTS.modelScope;
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
    // Hints are "never blocking". Taking every tier can cost a player the
    // points they earned by explaining the design, but never the ones they
    // earned by solving it.
    total: Math.max(POINTS.solved, raw),
    available,
    lines,
    solved: true,
    bare: claimsEarned === 0 && coverageEarned === 0,
  };
}