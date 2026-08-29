// Checks web/src/notebook/scoring.ts's `scoreCard` -- game-plan.md §8's
// weights.
//
// The numbers themselves are meant to be tuned in scoring.ts, so this file
// deliberately asserts almost nothing about their exact values. What it
// protects is the set of properties the weights exist to produce, each of
// which is a design decision that a well-meaning re-tune could silently
// reverse:
//
//   1. §8's stated ORDERING: model > coverage > claims > solved > time.
//   2. Claim points are capped, so writing many cheap claims cannot outscore
//      building a validated model.
//   3. Hints never block: every tier revealed still leaves a solved puzzle
//      with the points solving earned.
//   4. A puzzle with no par omits the time component instead of scoring zero
//      out of five, which would read as a slow solve.
//   5. An unsolved puzzle has no score at all -- §8 shows one only after
//      solving, and "0 points so far" is exactly the live pressure it rules
//      out.
//   6. A solve with an empty notebook is a real solve, flagged `bare` so the
//      write-up can say what the other points are for rather than presenting
//      a 10/100 as a failure.
//
// No browser and no localStorage: scoreCard is pure and takes already-computed
// values, so plain objects are enough.
//
// Usage: node --experimental-strip-types web/scripts/test-scoring.mjs

import { POINTS, scoreCard } from "../src/notebook/scoring.ts";

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

/** A Coverage, as `coverage()` returns one. */
function cov(fraction) {
  return {
    fraction,
    roles: { done: Math.round(92 * fraction), total: 92 },
    cone: { done: Math.round(57 * fraction), total: 57 },
  };
}

const PAR = 120;

/** A solved puzzle with nothing else going for it, overridden per case. */
function input(over = {}) {
  return {
    solved: true,
    coverage: cov(0),
    claimPoints: 0,
    modelValidated: false,
    solveMs: PAR * 60_000, // exactly par, so the time bonus is full and stable
    parMinutes: PAR,
    hintsTaken: 0,
    ...over,
  };
}

function totalOf(over) {
  return scoreCard(input(over)).total;
}

// ---- 1. §8's ordering --------------------------------------------------

const bare = totalOf({ solveMs: 3 * PAR * 60_000 }); // no time bonus either
const model = totalOf({ modelValidated: true }) - totalOf({});
const fullCoverage = totalOf({ coverage: cov(1) }) - totalOf({});
const cappedClaims = totalOf({ claimPoints: 1000 }) - totalOf({});
const timeOnly = totalOf({}) - bare;

check(model > fullCoverage, `a validated model (${model}) must outscore full coverage (${fullCoverage})`);
check(fullCoverage > cappedClaims, `full coverage (${fullCoverage}) must outscore every claim (${cappedClaims})`);
check(cappedClaims > POINTS.solved, `the claim cap (${cappedClaims}) must outscore solving (${POINTS.solved})`);
check(POINTS.solved > timeOnly, `solving (${POINTS.solved}) must outscore the time bonus (${timeOnly})`);
check(timeOnly > 0, "a solve at par must earn some time bonus");

// A perfect card is out of 100, so the number a player is shown has a scale
// they already know how to read.
const perfect = scoreCard(input({ coverage: cov(1), claimPoints: 1000, modelValidated: true }));
check(perfect.total === 100, `a perfect solve should score 100, got ${perfect.total}`);
check(perfect.available === 100, `a perfect card is out of 100, got ${perfect.available}`);

// ---- 2. claim points are capped ----------------------------------------

// Twenty structural claims is 120 raw claim points -- more than everything
// else in the game put together, if it were not capped.
const manyClaims = totalOf({ claimPoints: 20 * POINTS.provenStructural });
const oneModel = totalOf({ modelValidated: true });
check(
  manyClaims < oneModel,
  `20 structural claims (${manyClaims}) must not outscore a validated model (${oneModel})`,
);
check(
  totalOf({ claimPoints: 1000 }) === totalOf({ claimPoints: POINTS.claimsMax }),
  "claim points past the cap add nothing",
);

// The cap is stated in the breakdown rather than silently applied.
const cappedLine = scoreCard(input({ claimPoints: 1000 })).lines.find((l) => l.name === "Claims settled");
check(cappedLine.earned === POINTS.claimsMax, "the claims line stops at the cap");
check(/capped/.test(cappedLine.note), "a capped claims line must say it was capped");

// ---- 3. hints never block ----------------------------------------------

const everyHint = scoreCard(input({ hintsTaken: 99 }));
check(
  everyHint.total >= POINTS.solved,
  `revealing every hint must still leave the solve's own points, got ${everyHint.total}`,
);
check(
  totalOf({ hintsTaken: 1, coverage: cov(1) }) < totalOf({ hintsTaken: 0, coverage: cov(1) }),
  "a hint must actually cost something when there are points to lose",
);
check(
  scoreCard(input({ hintsTaken: 0 })).lines.every((l) => l.name !== "Hints taken"),
  "no hints taken means no hints line -- an unpenalised player is not shown a penalty",
);

// ---- 4. a puzzle with no par -------------------------------------------

const noPar = scoreCard(input({ parMinutes: null }));
check(
  noPar.lines.every((l) => l.name !== "Time"),
  "a puzzle with no par must omit the time component, not score it zero",
);
check(noPar.available === 100 - POINTS.timeMax, `no par means a card out of 95, got ${noPar.available}`);

// The same holds when the par exists but the solve time was never recorded --
// a puzzle solved before engaged time was tracked.
const noTime = scoreCard(input({ solveMs: null }));
check(noTime.lines.every((l) => l.name !== "Time"), "an unrecorded solve time omits the component too");

// A replayed key is submitted within seconds, and "0 min" is a wrong-looking
// way to write that.
const fast = scoreCard(input({ solveMs: 15_000 })).lines.find((l) => l.name === "Time");
check(
  fast.note.includes("under a minute"),
  `a sub-minute solve must not read as "0 min", got "${fast.note}"`,
);

// Slower than twice par earns nothing, and is never negative.
const verySlow = scoreCard(input({ solveMs: 10 * PAR * 60_000 })).lines.find((l) => l.name === "Time");
check(verySlow.earned === 0, `a very slow solve earns no time bonus, got ${verySlow.earned}`);

// ---- 5. an unsolved puzzle has no score --------------------------------

const unsolved = scoreCard(input({ solved: false, coverage: cov(1), modelValidated: true }));
check(unsolved.solved === false, "an unsolved card says so");
check(unsolved.total === 0, `an unsolved puzzle scores nothing, got ${unsolved.total}`);
check(unsolved.lines.length === 0, "an unsolved puzzle has no breakdown to show");

// ---- 6. a solve with an empty notebook ---------------------------------

const noNotebook = scoreCard(input({ solveMs: 3 * PAR * 60_000 }));
check(noNotebook.bare === true, "a solve with no coverage and no claims is flagged bare");
check(
  noNotebook.total === POINTS.solved,
  `a bare solve scores exactly the solve points, got ${noNotebook.total}`,
);
check(
  scoreCard(input({ claimPoints: POINTS.likely })).bare === false,
  "one settled claim is enough to stop being bare",
);
check(
  scoreCard(input({ coverage: cov(0.5) })).bare === false,
  "coverage alone is enough to stop being bare",
);

// Every line explains itself -- a breakdown of bare numbers is a grade.
for (const line of perfect.lines) {
  check(line.note.length > 0, `the ${line.name} line must carry a note`);
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} scoring checks`);