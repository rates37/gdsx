// Runs the notebook's bit-parallel evaluator against the golden slice tables
// recorded in tests/golden/slice-*.json (see scripts/slice_golden.py at the
// repo root) -- the contract that keeps the Python and TypeScript evaluators
// from ever drifting, exactly as trace-*.json does for the two tape executors.
//
// Deliberately outside web/src/: it runs under plain Node against the goldens
// on disk, not bundled by Vite, and has no business inside the browser source
// tree's tsconfig project. Modelled on web/scripts/test-sim-golden.mjs.
//
// Usage: node --experimental-strip-types web/scripts/test-notebook-verify.mjs

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  LANES,
  SliceRunner,
  budget,
  decode,
  exhaustiveColumns,
  firstLane,
  checkCombinational,
  checkEssential,
  checkRole,
} from "../src/notebook/verify.ts";
import { verdictStyle } from "../src/notebook/model.ts";
import { measure, evaluatePredicate } from "../src/notebook/sequential.ts";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootDir = path.dirname(webDir);
const goldenDir = path.join(rootDir, "tests", "golden");

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function fail(message) {
  checks++;
  failures.push(message);
}

// ---- the golden tables --------------------------------------------------

/** Every row of a slice's truth table, evaluated 32 lanes at a time. */
function tabulate(slice) {
  const runner = new SliceRunner(slice);
  const width = slice.free.length;
  const columns = new Int32Array(Math.max(width, 1));
  const rows = new Array(1 << width);

  const blocks = Math.max(1, 2 ** Math.max(0, width - 5));
  const lanes = width >= 5 ? LANES : 1 << width;
  for (let block = 0; block < blocks; block++) {
    exhaustiveColumns(width, block, columns);
    runner.run(columns);
    for (let lane = 0; lane < lanes; lane++) {
      const row = block * (width >= 5 ? LANES : lanes) + lane;
      rows[row] = slice.targets
        .map((id) => (runner.values[id] >>> lane) & 1)
        .join("");
    }
  }
  return rows;
}

function checkSample(name) {
  const document = JSON.parse(
    readFileSync(path.join(goldenDir, `slice-${name}.json`), "utf8"),
  );
  for (const entry of document.slices) {
    const label = `${name}: ${entry.target_names.join(",")}`;
    const rows = tabulate(entry);
    if (rows.length !== entry.table.length) {
      fail(`${label}: produced ${rows.length} rows, golden has ${entry.table.length}`);
      continue;
    }
    const wrong = rows.findIndex((row, i) => row !== entry.table[i]);
    check(
      wrong === -1,
      wrong === -1
        ? ""
        : `${label}: row ${wrong} is ${rows[wrong]}, golden says ${entry.table[wrong]} ` +
          `(free = ${entry.free.join(", ")})`,
    );
  }
  return document.slices.length;
}

// ---- the rule the whole mechanic rests on -------------------------------

function checkVerdictsStayApart() {
  const proven = verdictStyle({ kind: "PROVEN", method: "exhaustive", cases: 1048576 });
  const likely = verdictStyle({ kind: "LIKELY", method: "sampled", cases: 10000 });
  const disproven = verdictStyle({
    kind: "DISPROVEN",
    counterexample: null,
    observed: [],
    expected: [],
  });
  const unknown = verdictStyle({ kind: "UNKNOWN", reason: "too large" });

  check(proven.tone !== likely.tone, "PROVEN and LIKELY must not share a tone");
  check(proven.label !== likely.label, "PROVEN and LIKELY must not share a label");
  check(
    !likely.label.toLowerCase().includes("proven"),
    `LIKELY must never say "proven"; it says ${JSON.stringify(likely.label)}`,
  );
  check(
    likely.label.includes("10,000"),
    "LIKELY must say how many vectors found nothing",
  );
  check(
    proven.label.includes("1,048,576"),
    "PROVEN must say how many cases it covered",
  );
  check(disproven.strike, "a disproven claim is kept struck through, not removed");
  check(!proven.strike && !likely.strike && !unknown.strike, "only DISPROVEN strikes");
  check(unknown.label.includes("too large"), "UNKNOWN must carry its reason");
}

function checkBudget() {
  check(budget(20).method === "exhaustive" && budget(20).inline, "<=20 runs inline");
  check(budget(20).clean === "PROVEN", "an exhaustive sweep may be proven");
  check(budget(24).method === "exhaustive" && !budget(24).inline, "21-24 wants a bar");
  check(budget(24).cases === 2 ** 24, "an exhaustive sweep visits every case");
  check(budget(25).method === "sampled", ">24 samples");
  check(
    budget(25).clean === "LIKELY",
    "sampling must never be allowed to produce PROVEN",
  );
  check(budget(25).cases === 10000, "sampling is 10k vectors, as the table says");
}

// ---- the checks, on hand-built slices -----------------------------------

const AND2 = 4;
const OR2 = 5;
const XOR2 = 6;
const NOT = 3;
const CONST1 = 1;
const U = -1;

/** ids 0..free-1 are the free variables; ops write above that. */
function slice(free, ops, targets, targetNames, nValues) {
  return {
    ops,
    free,
    free_ids: free.map((_, i) => i),
    targets,
    target_names: targetNames,
    consts: [],
    n_values: nValues,
  };
}

function checkTheChecks() {
  // a & b, twice: identical slices must be indistinguishable everywhere.
  const and = (names) => slice(names, [AND2, 2, 0, 1, U, U], [2], ["t"], 3);
  const same = checkCombinational("equal", and(["a", "b"]), and(["a", "b"]));
  check(same.kind === "PROVEN", `identical functions must be proven, got ${same.kind}`);
  check(same.cases === 4, "two variables is four cases");

  // a & b versus a | b: disproven, with a decodable counterexample.
  const or = slice(["a", "b"], [OR2, 2, 0, 1, U, U], [2], ["t"], 3);
  const differ = checkCombinational("equal", and(["a", "b"]), or);
  check(differ.kind === "DISPROVEN", "and != or must be disproven");
  if (differ.kind === "DISPROVEN") {
    const leaves = differ.counterexample?.leaves ?? {};
    check(
      (leaves.a ^ leaves.b) === 1,
      `the counterexample must actually differ, got ${JSON.stringify(leaves)}`,
    );
  }

  // CONST1 must be every lane, not lane zero. A `1` here passes on lane 0 and
  // fails on the other 31, which is the whole reason this case exists.
  const one = slice(["a"], [CONST1, 1, U, U, U, U], [1], ["one"], 2);
  // a | ~a, which is 1 for every assignment -- so it must be indistinguishable
  // from the CONST1 slice above on all 32 lanes, not just lane 0.
  const notNotA = slice(
    ["a"],
    [NOT, 1, 0, U, U, U, OR2, 2, 0, 1, U, U],
    [2],
    ["a|~a"],
    3,
  );
  check(
    checkCombinational("equal", one, notNotA).kind === "PROVEN",
    "CONST1 must set every lane",
  );

  // implies: `a` implies `a | b` is 1; `a` does not imply `a & b`.
  const holds = checkCombinational(
    "implies",
    slice(["a", "b"], [OR2, 2, 0, 1, U, U], [2], ["t"], 3),
    slice(["a", "b"], [], [0], ["a"], 2),
    { value: 1 },
  );
  check(holds.kind === "PROVEN", `a -> (a|b) must be proven, got ${holds.kind}`);
  const breaks = checkCombinational(
    "implies",
    and(["a", "b"]),
    slice(["a", "b"], [], [0], ["a"], 2),
    { value: 1 },
  );
  check(breaks.kind === "DISPROVEN", "a -> (a&b) must be disproven");

  // essential: in `a & (b | ~b)`, b is in the cone and never matters.
  const vacuous = slice(
    ["a", "b"],
    [NOT, 2, 1, U, U, U, OR2, 3, 1, 2, U, U, AND2, 4, 0, 3, U, U],
    [4],
    ["t"],
    5,
  );
  const onlyA = checkEssential(vacuous, { claimed: ["a"] });
  check(onlyA.kind === "PROVEN", `only a matters, got ${onlyA.kind}`);
  const bothClaimed = checkEssential(vacuous, { claimed: ["a", "b"] });
  check(bothClaimed.kind === "DISPROVEN", "claiming b matters must be disproven");
  if (bothClaimed.kind === "DISPROVEN") {
    check(
      bothClaimed.observed.join(",") === "a",
      `observed should be just a, got ${bothClaimed.observed}`,
    );
  }

  // requirement: `a & b` cannot be 1 with b at 0, but can with b at 1.
  const probe = slice(["a", "b"], [AND2, 2, 0, 1, U, U], [2, 1], ["out", "b"], 3);
  check(
    checkCombinational("requirement", probe, null, { value: 1, flopValue: 1 }).kind ===
      "PROVEN",
    "a&b == 1 does require b == 1",
  );
  check(
    checkCombinational("requirement", probe, null, { value: 1, flopValue: 0 }).kind ===
      "DISPROVEN",
    "a&b == 1 does not require b == 0",
  );
}

function checkRoles() {
  // A 3-bit increment, written out as its next-state functions:
  //   n0 = ~q0;  n1 = q1 ^ q0;  n2 = q2 ^ (q0 & q1)
  const counter = {
    ops: [
      NOT, 3, 0, U, U, U,
      XOR2, 4, 1, 0, U, U,
      AND2, 5, 0, 1, U, U,
      XOR2, 6, 2, 5, U, U,
    ],
    free: ["q0", "q1", "q2"],
    free_ids: [0, 1, 2],
    targets: [3, 4, 6],
    target_names: ["n0", "n1", "n2"],
    consts: [],
    n_values: 7,
  };
  const proven = checkRole("counter", counter);
  check(proven.kind === "PROVEN", `a real counter must be proven, got ${proven.kind}`);
  check(proven.kind === "PROVEN" && proven.cases === 8, "three flops is eight states");
  check(
    checkRole("shift-reg", counter).kind === "DISPROVEN",
    "a counter is not a shift register",
  );

  // A rotate-right: n0 = q1, n1 = q2, n2 = q0. Also a linear map, so an LFSR
  // claim about it is true -- which is worth asserting, because the LFSR check
  // is the one that reads a matrix off the unit vectors and must then verify it.
  const rotate = {
    ops: [],
    free: ["q0", "q1", "q2"],
    free_ids: [0, 1, 2],
    targets: [1, 2, 0],
    target_names: ["n0", "n1", "n2"],
    consts: [],
    n_values: 3,
  };
  check(checkRole("shift-reg", rotate).kind === "DISPROVEN", "a rotate is not a shift");
  check(checkRole("lfsr", rotate).kind === "PROVEN", "a rotate is linear over GF(2)");
  check(checkRole("counter", rotate).kind === "DISPROVEN", "a rotate is not a counter");

  // A saturating counter: each bit sets and never clears, n_i = q_i | q_{i-1},
  // with n0 held high, so it climbs to all-ones and stays.
  const saturating = {
    ops: [CONST1, 3, U, U, U, U, OR2, 4, 1, 0, U, U, OR2, 5, 2, 1, U, U],
    free: ["q0", "q1", "q2"],
    free_ids: [0, 1, 2],
    targets: [3, 4, 5],
    target_names: ["n0", "n1", "n2"],
    consts: [],
    n_values: 6,
  };
  const sat = checkRole("saturating-counter", saturating);
  check(sat.kind === "PROVEN", `a saturating counter must be proven, got ${sat.kind}`);
  const down = {
    ops: [NOT, 3, 0, U, U, U],
    free: ["q0"],
    free_ids: [0],
    targets: [3],
    target_names: ["n0"],
    consts: [],
    n_values: 4,
  };
  check(
    checkRole("saturating-counter", down).kind === "DISPROVEN",
    "a toggle counts down, so it does not saturate",
  );
}

function checkPredicates() {
  const key = "0101001";
  check(measure("count", key, null) === 3, "count is how many cycles are high");
  check(measure("count", key, [0, 2]) === 1, "a window narrows the count");
  check(measure("first", key, null) === 1, "first is the first high cycle");
  check(measure("last", key, null) === 6, "last is the last high cycle");
  check(measure("runs", key, null) === 3, "runs counts runs of high cycles");
  check(measure("gaps", key, null) === 2, "gaps are the low runs BETWEEN pulses");
  check(measure("mingap", key, null) === 1, "mingap is the shortest of them");
  check(measure("maxgap", key, null) === 2, "maxgap is the longest of them");
  check(measure("count", "000", null) === 0, "an empty key counts zero");
  check(measure("mingap", "000", null) === -1, "no pulses means no gaps to measure");

  const term = (measure_, op, value) => ({
    node: "term",
    of: [],
    measure: measure_,
    port: "I",
    window: null,
    op,
    value,
  });
  const tracks = { I: key };
  check(evaluatePredicate(term("count", "==", 3), tracks), "== compares");
  check(!evaluatePredicate(term("count", "==", 4), tracks), "== rejects");
  check(evaluatePredicate(term("count", ">=", 3), tracks), ">= compares");
  check(evaluatePredicate(term("count", "<", 4), tracks), "< compares");
  check(
    evaluatePredicate(
      { node: "and", of: [term("count", "==", 3), term("mingap", ">=", 1)], measure: "", port: "", window: null, op: "", value: 0 },
      tracks,
    ),
    "and combines",
  );
  check(
    !evaluatePredicate(
      { node: "not", of: [term("count", "==", 3)], measure: "", port: "", window: null, op: "", value: 0 },
      tracks,
    ),
    "not negates",
  );
}

function checkCounterexampleDecoding() {
  const columns = new Int32Array(3);
  exhaustiveColumns(3, 0, columns);
  const vector = decode(["a", "b", "c"], columns, 5);
  check(
    vector.kind === "assignment" &&
      vector.leaves.a === 1 &&
      vector.leaves.b === 0 &&
      vector.leaves.c === 1,
    `lane 5 of an exhaustive block is the assignment 101, got ${JSON.stringify(vector)}`,
  );
  check(firstLane(0) === -1, "no set lane is -1");
  check(firstLane(0b1000) === 3, "firstLane finds the lowest set bit");
  check(firstLane(-1) === 0, "an all-ones word starts at lane 0");
}

// ---- run ----------------------------------------------------------------

let slices = 0;
for (const sample of ["sample", "puzzle"]) slices += checkSample(sample);
checkVerdictsStayApart();
checkBudget();
checkTheChecks();
checkRoles();
checkPredicates();
checkCounterexampleDecoding();

if (failures.length > 0) {
  console.error(`${failures.length}/${checks} notebook checks FAILED:`);
  for (const message of failures) console.error(`  - ${message}`);
  process.exit(1);
}
console.log(
  `notebook: ${checks} checks pass (${slices} golden slices, both sample designs)`,
);