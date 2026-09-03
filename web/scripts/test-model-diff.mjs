// Checks the Model Builder's differential: the vector generator, the design
// side, and the rules that keep "my model agrees" from meaning more than it
// does (web/src/model/). Runs against samples/puzzle.tape.bin under plain Node,
// like the other two test scripts here.
//
// The properties worth protecting, in the order they are checked below:
//
//   1. a model that IS the design agrees on every vector -- if this fails the
//      design side is driving the tape differently from the panel;
//   2. one disagreeing vector is reported as the FIRST one in generation order,
//      not merely as a lower percentage;
//   3. an observable the design does not have stops the run. It is unchecked,
//      not agreed and not diverged, and scoring it either way would be a lie;
//   4. the badge needs 100% AND the full vector count, and never says "proven".
//
// Usage: node --experimental-strip-types web/scripts/test-model-diff.mjs

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseTapeBundle } from "../src/sim/tape.ts";
import { compare, observe, ObservableError } from "../src/model/diff.ts";
import { generate, bitsOf, pulsesOf } from "../src/model/vectors.ts";
import { badgeView, VALIDATION_VECTORS } from "../src/model/store.ts";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootDir = path.dirname(webDir);

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

const tape = parseTapeBundle(
  toArrayBuffer(readFileSync(path.join(rootDir, "samples", "puzzle.tape.bin"))),
);

// The same protocol main.ts drives Two Stars with: `I` pulsed, `enable` high,
// `rst_n` low for the one reset step and high afterwards.
const ctx = {
  tape,
  resetVector: { clk: 0, rst_n: 0, enable: 1, I: 0 },
  inputPorts: ["I", "enable", "rst_n"],
  cycles: 60,
  keyPort: "I",
  levels: { enable: 1, rst_n: 1 },
};

// ---- the generator ------------------------------------------------------

const baseline = [3, 9, 14, 21];
const vectors = generate(baseline, { count: 40, cycles: ctx.cycles, seed: 0x5eed });
check(vectors.length === 40, "the generator returned the wrong number of vectors");
check(
  JSON.stringify(vectors[0]) === JSON.stringify(baseline),
  "vector 0 must be the player's own key, unchanged",
);
check(
  vectors.every((v) => v.every((c) => c >= 0 && c < ctx.cycles)),
  "a generated pulse landed outside the run",
);
const densities = new Set(vectors.map((v) => v.length));
check(densities.size > 5, "the generated keys are not varied in density");
check(
  JSON.stringify(generate(baseline, { count: 40, cycles: ctx.cycles, seed: 0x5eed })) ===
    JSON.stringify(vectors),
  "generation is not deterministic — a divergence that moves cannot be debugged",
);
check(
  pulsesOf(bitsOf(baseline, ctx.cycles)).join(",") === baseline.join(","),
  "a pulse set does not survive a round trip through a bit string",
);

// ---- 1. a model that is the design --------------------------------------

const watch = ["success"];
const truth = vectors.map((pulses) => observe(ctx, pulses, watch));
const agreeing = compare(ctx, vectors, truth);
check(
  agreeing.agreed === agreeing.vectors && agreeing.first === null,
  `a model equal to the design disagreed on ${agreeing.vectors - agreeing.agreed} vectors`,
);

// ---- 2. first divergence, in generation order ---------------------------

const wrong = truth.map((row) => ({ ...row }));
wrong[7].success = 1 - wrong[7].success;
wrong[19].success = 1 - wrong[19].success;
const diverging = compare(ctx, vectors, wrong);
check(diverging.first?.index === 7, `the first divergence was reported as ${diverging.first?.index}`);
check(
  diverging.agreed === diverging.vectors - 2,
  `agreement should have dropped by exactly two, got ${diverging.agreed}/${diverging.vectors}`,
);
check(diverging.mismatches.success === 2, "per-observable mismatch counts are wrong");
check(
  diverging.first?.model !== diverging.first?.design,
  "a reported divergence must have different values on the two sides",
);
check(
  JSON.stringify(diverging.first?.pulses) === JSON.stringify(vectors[7]),
  "the divergence must carry the vector that produced it, for the waveform",
);

// ---- 3. an unchecked observable is not an agreement ---------------------

let raised = null;
try {
  compare(ctx, vectors, truth.map((row) => ({ ...row, my_counter: 1 })));
} catch (err) {
  raised = err;
}
check(
  raised instanceof ObservableError,
  "a model naming a signal the design does not have must stop the run",
);
check(
  raised?.names?.includes("my_counter"),
  "the error must name the observable that could not be checked",
);

let empty = null;
try {
  compare(ctx, vectors, vectors.map(() => ({})));
} catch (err) {
  empty = err;
}
check(empty instanceof ObservableError, "a model that returns nothing must stop the run");

let vanishing = null;
try {
  const patchy = truth.map((row, i) => (i === 5 ? {} : { ...row }));
  compare(ctx, vectors, patchy);
} catch (err) {
  vanishing = err;
}
check(
  vanishing instanceof ObservableError,
  "an observable that comes and goes must stop the run, not count as agreement",
);

// ---- 4. agreement about a signal that never moved ------------------------

// None of these random keys latches `success`, so the design answers 0 every
// time and a model that always answers 0 "agrees" perfectly. That is not a
// validated model, it is an untested one, and the report has to say which.
const alwaysZero = vectors.map(() => ({ success: 0 }));
const vacuousReport = compare(ctx, vectors, alwaysZero);
check(
  vacuousReport.constant.includes("success"),
  "an observable the design held at one value must be reported as constant",
);
check(
  vacuousReport.agreed === vacuousReport.vectors,
  "the always-zero model should agree — that is exactly why `constant` exists",
);
check(
  agreeing.constant.length === vacuousReport.constant.length,
  "the design side is the same either way; `constant` is about the design, not the model",
);

// ---- 5. what the badge is allowed to say --------------------------------

function storeWith(runs) {
  return { badge: () => runs.find((r) => r.validated) ?? null, latest: () => runs.at(-1) ?? null };
}
const short = {
  at: Date.now(),
  language: "javascript",
  vectors: 10,
  agreed: 10,
  seed: 1,
  validated: false,
  watch,
  constant: [],
};
const full = { ...short, vectors: VALIDATION_VECTORS, agreed: VALIDATION_VECTORS, validated: true };
const broken = { ...full, agreed: VALIDATION_VECTORS - 3, validated: false };

check(
  badgeView(storeWith([short])).tone === "amber",
  "100% of a short run must not read as a validated model",
);
check(
  badgeView(storeWith([full])).tone === "green" &&
    badgeView(storeWith([full])).label.includes(String(VALIDATION_VECTORS)),
  "the badge must quote the number of vectors it was earned over",
);
check(
  badgeView(storeWith([full, broken])).tone === "amber",
  "a model that has since diverged must not keep a green badge",
);
const untested = { ...full, validated: false, constant: [...watch] };
check(
  badgeView(storeWith([untested])).tone === "amber" &&
    badgeView(storeWith([untested])).label === "nothing was tested",
  "100% agreement about a signal the design never moved must not read as progress",
);
for (const runs of [[short], [full], [full, broken], [broken]]) {
  const view = badgeView(storeWith(runs));
  check(
    !`${view.label} ${view.detail}`.toLowerCase().includes("proven"),
    "no badge may claim a proof: agreement over vectors is not a proof",
  );
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} model-differential checks`);