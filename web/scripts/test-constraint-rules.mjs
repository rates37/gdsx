// The Constraints drawer's two row generators, checked against the rule they
// are supposed to encode.
//
// The drawer's job is to let a player state the four rules of a puzzle like
// the reference one and find the key without leaving the app. Three of those
// rules are "exactly two of this set of cycles" and the fourth is a pairwise
// exclusion; the generators are what turn the second and fourth from
// hundreds of hand-typed rows into two lines of parameters. If a generator
// emits rows that mean something other than what its label says, the drawer
// quietly answers a different question from the one asked, so this pins the
// meaning rather than the row count alone.
//
// The Python half of the same contract -- that these rules together admit
// exactly one solution -- lives in tests/test_constraints.py, which is where
// the solver is.
//
// Usage: node --experimental-strip-types web/scripts/test-constraint-rules.mjs

import { partitionRows, spacingRows, satisfies } from "../src/experiments/constraint-rules.ts";

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function same(actual, expected, message) {
  check(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${message}\n    expected ${JSON.stringify(expected)}\n    actual   ${JSON.stringify(actual)}`,
  );
}

const WINDOW_END = 120;
const EPOCH = 11;

// The reference puzzle's answer, as the walkthrough reaches it. Used only as
// a known-good and known-bad probe against the generated rows -- nothing here
// searches for it.
const KEY = [
  7, 9, 11, 16, 29, 31, 33, 35, 48, 50, 57,
  63, 70, 76, 78, 83, 91, 98, 104, 107, 111, 113,
];

//! partition

const epochs = partitionRows(0, WINDOW_END, EPOCH, "div", 2);
check(epochs.length === EPOCH, `div 11 over 0..120 should give 11 classes, got ${epochs.length}`);
same(epochs[0].elements, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "epoch 0 is cycles 0..10");
same(epochs[10].elements, [110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120], "epoch 10 is cycles 110..120");
check(
  epochs.every((r) => r.lb === 2 && r.ub === 2),
  "every partition row is an exact bound, not a one-sided one",
);

const positions = partitionRows(0, WINDOW_END, EPOCH, "mod", 2);
check(positions.length === EPOCH, `mod 11 over 0..120 should give 11 classes, got ${positions.length}`);
same(positions[0].elements, [0, 11, 22, 33, 44, 55, 66, 77, 88, 99, 110], "position 0 is the residue class");

// A partition really partitions: every cycle in the range lands in exactly
// one class, under either mode. A generator that dropped or duplicated a
// cycle would still produce plausible-looking rows and a wrong answer.
for (const [label, rows] of [["div", epochs], ["mod", positions]]) {
  const seen = rows.flatMap((r) => r.elements).sort((a, b) => a - b);
  same(seen, [...Array(WINDOW_END + 1).keys()], `${label} rows cover 0..120 exactly once`);
}

// A range that does not divide evenly still partitions -- the last class is
// simply short, and that is a truthful statement about the range given.
const ragged = partitionRows(0, 4, 3, "div", 1);
same(
  ragged.map((r) => r.elements),
  [[0, 1, 2], [3, 4]],
  "a range that does not divide evenly leaves a short final class",
);

//! spacing

const spacing = spacingRows(0, WINDOW_END, EPOCH, 1, 1);
check(
  spacing.every((r) => r.lb === 0 && r.ub === 1 && r.elements.length === 2),
  "every spacing row is a two-element upper bound",
);

const forbidden = new Set(spacing.map((r) => r.elements.join(",")));
// The cases the gap sweep turns on, from the walkthrough's own table.
check(forbidden.has("0,1"), "0 and 1 are adjacent positions in one epoch — forbidden");
check(forbidden.has("0,11"), "0 and 11 are the same position in neighbouring epochs — forbidden");
check(forbidden.has("0,12"), "0 and 12 are adjacent positions in neighbouring epochs — forbidden");
check(forbidden.has("10,21"), "10 and 21 are both at position 10 one epoch apart — forbidden");
check(!forbidden.has("0,10"), "0 and 10 are ten positions apart — there is no wraparound");
check(!forbidden.has("0,22"), "0 and 22 are two epochs apart — allowed");
check(!forbidden.has("0,2"), "0 and 2 are two positions apart — allowed");

//! the rules, against a key and against a near miss

const chosen = new Set(KEY);
const allRows = [...epochs, ...positions, ...spacing];
check(
  allRows.every((r) => satisfies(r, chosen)),
  "the reference key satisfies every generated row",
);

// Move one pulse next door and the spacing rule must be the thing that
// objects: if it does not, the generator is emitting rows that forbid
// nothing in particular.
const nudged = new Set(KEY);
nudged.delete(7);
nudged.add(8);
const broken = allRows.filter((r) => !satisfies(r, nudged));
check(broken.length > 0, "moving a pulse from cycle 7 to 8 must break something");
check(
  broken.some((r) => r.name === "spacing 8,9"),
  `cycles 8 and 9 are adjacent positions in one epoch and must be forbidden, broke: ${broken.map((r) => r.name).join(", ")}`,
);

console.log(`${checks} checks, ${failures.length} failed`);
for (const failure of failures) console.error(`  FAIL ${failure}`);
process.exit(failures.length ? 1 : 0);