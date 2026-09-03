// Checks web/src/notebook/scoring.ts's `scoreCard` and the scoring weights
// it implements.
//
// The numbers themselves are meant to be tuned in scoring.ts, so this file
// deliberately asserts almost nothing about their exact values. What it
// protects is the set of properties the weights exist to produce, each of
// which is a design decision that a well-meaning re-tune could silently
// reverse:
//
//   1. The stated ORDERING: model > coverage > claims > solved > time.
//   2. Claim points are capped, so writing many cheap claims cannot outscore
//      building a validated model.
//   3. Hints never block: every tier revealed still leaves a solved puzzle
//      with the points solving earned.
//   4. A puzzle with no par omits the time component instead of scoring zero
//      out of five, which would read as a slow solve.
//   5. An unsolved puzzle has no score at all -- a score is shown only after
//      solving, and "0 points so far" is exactly the live pressure that
//      rule exists to rule out.
//   6. A solve with an empty notebook is a real solve, flagged `bare` so the
//      write-up can say what the other points are for rather than presenting
//      a 10/100 as a failure.
//   7. The read-out rule: a `structural`-KIND claim restates what the Netlist
//      panel already shows, so it is worth little and its net is not fully
//      explained. Keyed on the claim's kind and NEVER on `verdict.method`,
//      which `support` and `requirement` proofs share with it.
//   8. A replay settles one stimulus. It outscores a sampled search, loses to
//      an exhaustive sweep, and still fully explains the net it is about.
//   9. The model component is a SLOPE, not a cliff: a clean run of half the
//      validation vectors is worth half the agreement points, and a run that
//      cleared the badge's count is worth all of them. What it is not is
//      partial credit for being nearly right -- a run with a single mismatch
//      scores nothing here however many vectors it had.
//  10. Model scope is measured against the PUZZLE's observables, not the
//      model's own: a model reporting one signal cannot earn what a model
//      reproducing the whole objective earns. A signal the design never moved
//      does not count, and a puzzle naming no observable at all drops the
//      component and lowers `available` with it, as `parMinutes` does.
//
// No browser and no localStorage: scoreCard is pure and takes already-computed
// values, so plain objects are enough.
//
// Usage: node --experimental-strip-types web/scripts/test-scoring.mjs

import {
  POINTS,
  READ_OUT_COVERAGE,
  coverage,
  pointsFor,
  scoreCard,
} from "../src/notebook/scoring.ts";
import { VALIDATION_VECTORS, bestCleanRun } from "../src/model/store.ts";

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
    cone: { done: Math.round(57 * fraction), partial: 0, total: 57 },
  };
}

const PAR = 120;

/** The puzzle's own observables, as boot.ts derives them from the descriptor:
 *  the lock plus whatever the win check reads. Two of them, so a model that
 *  covers half can be told from one that covers all. */
const REQUIRED = ["success", "n1"];

/** A clean run: agreed on every vector, and tested these observables. */
function run(vectors, tested = REQUIRED) {
  return { vectors, tested };
}

/** A solved puzzle with nothing else going for it, overridden per case. */
function input(over = {}) {
  return {
    solved: true,
    coverage: cov(0),
    claimPoints: 0,
    model: null,
    requiredObservables: REQUIRED,
    solveMs: PAR * 60_000, // exactly par, so the time bonus is full and stable
    parMinutes: PAR,
    hintsTaken: 0,
    ...over,
  };
}

function totalOf(over) {
  return scoreCard(input(over)).total;
}

// ---- 1. the stated ordering ---------------------------------------------

const bare = totalOf({ solveMs: 3 * PAR * 60_000 }); // no time bonus either
// "Model" is now the two components together: full agreement over the whole
// required set. It is that TOTAL that the ordering below is about.
const perfectModel = { model: run(VALIDATION_VECTORS) };
const model = totalOf(perfectModel) - totalOf({});
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
const perfect = scoreCard(input({ coverage: cov(1), claimPoints: 1000, ...perfectModel }));
check(perfect.total === 100, `a perfect solve should score 100, got ${perfect.total}`);
check(perfect.available === 100, `a perfect card is out of 100, got ${perfect.available}`);

// ---- 2. claim points are capped ----------------------------------------

// Twenty read-outs is 40 raw claim points -- and even twenty of the most
// valuable claim in the game must not outscore everything else put together.
const manyClaims = totalOf({ claimPoints: 20 * POINTS.readOut });
const oneModel = totalOf(perfectModel);
check(
  manyClaims < oneModel,
  `20 read-outs (${manyClaims}) must not outscore a validated model (${oneModel})`,
);
const manyProofs = totalOf({ claimPoints: 20 * POINTS.provenExhaustive });
check(
  manyProofs < oneModel,
  `20 exhaustive proofs (${manyProofs}) must not outscore a validated model (${oneModel})`,
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

const unsolved = scoreCard(input({ solved: false, coverage: cov(1), ...perfectModel }));
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

// ---- 7. the read-out rule ----------------------------------------------

/** A settled claim, in the shape `pointsFor` and `coverage` read. */
function settled(claim, verdict) {
  return { id: claim.kind, claim, created: 0, history: [{ verdict, at: 0, notes: [], call: "" }] };
}

/** A notebook, as `coverage()` uses one: it only ever calls `.all()`. */
const notebookOf = (...records) => ({ all: () => records });

const STRUCTURAL_PROOF = { kind: "PROVEN", method: "structural", cases: 1 };
const A_DISPROOF = { kind: "DISPROVEN", counterexample: null, observed: [], expected: [] };

const readOut = { kind: "structural", net: "n1", cell: "nand2" };
const support = { kind: "support", flop: "f0", leaves: ["n1"], sense: "structural" };

// THE test for this rule. Both claims carry the IDENTICAL verdict -- the
// library stamps `method: "structural"` on every graph fact it settles -- so
// this fails loudly if the discount is ever re-keyed off `verdict.method`
// instead of off the claim's kind.
const readOutPoints = pointsFor(settled(readOut, STRUCTURAL_PROOF));
const supportPoints = pointsFor(settled(support, STRUCTURAL_PROOF));
check(
  readOutPoints < supportPoints,
  `under the same verdict, a structural claim (${readOutPoints}) must be worth ` +
    `less than a support claim (${supportPoints}) -- key the discount on ` +
    `claim.kind, not verdict.method`,
);
check(readOutPoints === POINTS.readOut, "a settled structural claim pays the read-out price");

// Typing the netlist back WRONG must not pay more than typing it back right.
const wrongReadOut = pointsFor(settled(readOut, A_DISPROOF));
check(
  wrongReadOut === readOutPoints,
  `a read-out pays the same right or wrong, got ${wrongReadOut} vs ${readOutPoints}`,
);
check(
  wrongReadOut < pointsFor(settled(support, A_DISPROOF)),
  "a disproof still costs real engagement on any kind that is not a read-out",
);

// An UNKNOWN still teaches nobody anything, read-out or not.
check(
  pointsFor(settled(readOut, { kind: "UNKNOWN", reason: "unsupported" })) === 0,
  "an unsettled read-out is worth nothing, as before",
);

// Coverage: a cone net named only by a read-out is not explained.
const cone = ["n1"];
const byReadOut = coverage(notebookOf(settled(readOut, STRUCTURAL_PROOF)), [], cone);
check(
  byReadOut.cone.done === 0 && byReadOut.cone.partial === 1,
  `a net named only by a read-out is partial, not done, got ` +
    JSON.stringify(byReadOut.cone),
);
const bySupport = coverage(notebookOf(settled(support, STRUCTURAL_PROOF)), [], cone);
check(
  bySupport.cone.done === 1 && bySupport.cone.partial === 0,
  `a net named by a support claim is fully explained, got ${JSON.stringify(bySupport.cone)}`,
);
check(
  byReadOut.fraction < bySupport.fraction,
  `naming a net must count for less than explaining it ` +
    `(${byReadOut.fraction} vs ${bySupport.fraction})`,
);
check(
  byReadOut.fraction === 0.5 * READ_OUT_COVERAGE,
  `a read-out is worth READ_OUT_COVERAGE of its net, got ${byReadOut.fraction}`,
);

// Both claims about the same net: explained, counted once, not 1.25 nets.
const both = coverage(
  notebookOf(settled(readOut, STRUCTURAL_PROOF), settled(support, STRUCTURAL_PROOF)),
  [],
  cone,
);
check(
  both.cone.done === 1 && both.cone.partial === 0,
  `a net named both ways counts once, at full credit, got ${JSON.stringify(both.cone)}`,
);

// ---- 8. what a replay proves -------------------------------------------

check(
  POINTS.likely < POINTS.provenReplay && POINTS.provenReplay < POINTS.provenExhaustive,
  `a replay (${POINTS.provenReplay}) sits above a sampled search (${POINTS.likely}) ` +
    `and below an exhaustive proof (${POINTS.provenExhaustive})`,
);

const timing = {
  kind: "timing",
  net: "n1",
  event: "rises",
  cycles: [3],
  stimulus: { name: "burst-3", cycles: 121, tracks: {} },
};
const replayVerdict = { kind: "PROVEN", method: "replay", cases: 121, sequence: "burst-3" };
check(
  pointsFor(settled(timing, replayVerdict)) === POINTS.provenReplay,
  "a timing claim settled by replay pays the replay price, not the exhaustive one",
);

// A replay is behavioural evidence about the net, unlike a read-out: full credit.
const byReplay = coverage(notebookOf(settled(timing, replayVerdict)), [], cone);
check(
  byReplay.cone.done === 1 && byReplay.cone.partial === 0,
  `a replayed net is fully explained, got ${JSON.stringify(byReplay.cone)}`,
);

// ---- 9. the model slope ------------------------------------------------

const agreementOf = (over) =>
  scoreCard(input(over)).lines.find((l) => l.name === "Model agreement").earned;

check(
  agreementOf(perfectModel) === POINTS.modelValidated,
  `a badge-length clean run earns the whole agreement component, got ${agreementOf(perfectModel)}`,
);
// The cliff this replaced: one vector short used to be worth nothing.
const nearMiss = agreementOf({ model: run(VALIDATION_VECTORS - 1) });
check(
  nearMiss > 0 && nearMiss <= POINTS.modelValidated,
  `a run one vector short of the badge is worth almost all of it, got ${nearMiss}`,
);
check(
  agreementOf({ model: run(Math.round(VALIDATION_VECTORS / 2)) }) ===
    Math.round(POINTS.modelValidated / 2),
  "a clean run of half the validation vectors is worth half the agreement points",
);
// Past the badge's count there is nothing more to earn -- running 10,000
// vectors is not ten times the model that ran 1,000.
check(
  agreementOf({ model: run(50 * VALIDATION_VECTORS) }) === POINTS.modelValidated,
  "the agreement component is capped at full marks",
);
// A mismatched run is not a clean run and never reaches this file: boot.ts's
// `bestCleanRun` hands over null, and null is worth nothing however many
// vectors the mismatched run had.
check(
  totalOf({ model: null }) < totalOf({ model: run(1) }),
  "a mismatched 1000-vector run (no clean run at all) scores below a clean run of one",
);
check(
  agreementOf({ model: null }) === 0,
  "no clean run earns no agreement points",
);

// ---- 10. model scope ---------------------------------------------------

const scopeOf = (over) => {
  const line = scoreCard(input(over)).lines.find((l) => l.name === "Model scope");
  return line ? line.earned : null;
};

check(
  scopeOf(perfectModel) === POINTS.modelScope,
  `covering every required observable earns the whole scope component, got ${scopeOf(perfectModel)}`,
);
// The hole this closes: a model that reports the lock and nothing else used to
// earn exactly what a model reproducing the whole design earned.
const narrow = { model: run(VALIDATION_VECTORS, ["success"]) };
check(
  scopeOf(narrow) === Math.round(POINTS.modelScope / 2),
  `a model covering one of two required signals earns half the scope, got ${scopeOf(narrow)}`,
);
check(
  totalOf(narrow) < totalOf(perfectModel),
  "reproducing the whole objective must outscore reproducing the lock alone",
);
// Observables the model watched that the puzzle never asked about are not
// scope. Padding the watch list is not covering the design.
check(
  scopeOf({ model: run(VALIDATION_VECTORS, ["success", "n9", "n8", "n7"]) }) ===
    Math.round(POINTS.modelScope / 2),
  "watching signals the puzzle never named adds no scope",
);
// A signal the design held at one value all run was compared and never tested;
// `bestCleanRun` drops it from `tested`, so it cannot count as scope either.
check(
  scopeOf({ model: run(VALIDATION_VECTORS, []) }) === 0,
  "a run whose observables never moved covers nothing",
);

// ---- 10b. what counts as a clean run -----------------------------------
//
// The shaping step boot.ts does, checked here rather than in the browser:
// `bestCleanRun` only takes a store's `runs()`, so a plain object is a store.

const fakeStore = (...runs) => ({ runs: () => runs });
const recorded = (over) => ({
  at: 0,
  language: "javascript",
  seed: 0,
  vectors: 200,
  agreed: 200,
  validated: false,
  watch: ["success", "n1"],
  constant: [],
  ...over,
});

check(
  bestCleanRun(fakeStore(recorded({ vectors: 1000, agreed: 999 }))) === null,
  "a mismatched 1000-vector run is not a clean run, so it scores nothing",
);
check(
  bestCleanRun(fakeStore(recorded({ constant: ["success", "n1"] }))) === null,
  "a run where the design moved nothing is not a clean run either",
);
check(
  bestCleanRun(fakeStore(recorded({ vectors: 50, agreed: 50 }), recorded({ vectors: 300, agreed: 299 })))
    ?.vectors === 50,
  "the best CLEAN run is the longest one that agreed throughout, not the longest run",
);
check(
  JSON.stringify(bestCleanRun(fakeStore(recorded({ constant: ["n1"] })))?.tested) ===
    JSON.stringify(["success"]),
  "an observable the design held constant is dropped from what the run tested",
);

// A puzzle that names no observable of its own drops the component rather than
// scoring zero on it -- exactly the rule `parMinutes` follows for the time
// bonus, and for the same reason.
const noRequired = scoreCard(input({ ...perfectModel, requiredObservables: [] }));
check(
  noRequired.lines.every((l) => l.name !== "Model scope"),
  "a puzzle with no declared observable omits the scope component",
);
check(
  noRequired.available === 100 - POINTS.modelScope,
  `no required observables means a card out of 90, got ${noRequired.available}`,
);

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} scoring checks`);