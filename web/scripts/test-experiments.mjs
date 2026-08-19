// Runs the Experiment Runner's engine against the golden sweeps recorded in
// tests/golden/sensitivity-*.json (see scripts/sensitivity_golden.py) -- the
// contract that keeps the browser's sweeps and `gdsx.sim.sensitivity` from ever
// drifting, exactly as trace-*.json does for the two tape executors.
//
// Every recipe is checked against the measurement L33 itself produced:
// single-pulse against `sensitivity.map`, gap and bit-flip against `probe`, and
// the reset-value scan against the flop state after each cycle of one trace.
//
// Usage: node --experimental-strip-types web/scripts/test-experiments.mjs

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseTapeBundle } from "../src/sim/tape.ts";
import { run, selfChecks } from "../src/experiments/recipes.ts";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootDir = path.dirname(webDir);
const goldenDir = path.join(rootDir, "tests", "golden");

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

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

/** The columns a row marked, as names -- the shape the golden records. */
function marked(result, row) {
  return result.columns.filter((_, c) => row.cells[c]);
}

async function checkSample(name) {
  const golden = JSON.parse(readFileSync(path.join(goldenDir, `sensitivity-${name}.json`), "utf8"));
  const tape = parseTapeBundle(
    toArrayBuffer(readFileSync(path.join(rootDir, "samples", `${name}.tape.bin`))),
  );

  same(
    tape.header.flop_names,
    golden.flop_names,
    `${name}: flop order does not match the golden sweep`,
  );

  const base = {
    tape,
    resetVector: golden.reset,
    inputPorts: golden.ports,
    cycles: golden.cycles,
    baseVector: golden.baseline,
    keyPort: golden.key,
    baselineLabel: "golden",
    baseline: new Map(),
  };
  const watch = [...golden.flop_names];
  const window = { from: 0, to: golden.cycles, firstPulse: golden.first_pulse, minGap: 1, maxGap: 8, watch };

  // ---- 1. single-pulse sweep == sensitivity.map --------------------------

  const sweep = await run(base, "single-pulse", window);
  for (const [element, cycles] of Object.entries(golden.map.hits)) {
    const column = sweep.columns.indexOf(element);
    const found = sweep.rows.flatMap((row, r) => (row.cells[column] ? [r] : []));
    same(found, cycles, `${name}: cycles moving ${element} differ from sensitivity.map`);
  }
  for (const [cycle, elements] of Object.entries(golden.map.by_cycle)) {
    same(
      marked(sweep, sweep.rows[Number(cycle)]),
      elements,
      `${name}: elements moved by a pulse at cycle ${cycle} differ from sensitivity.map`,
    );
  }

  // The two self-checks are part of the result, not an extra: the same
  // unreactive elements and silent cycles L33 warns about must be found here.
  const unreactive = sweep.columns.filter((_, c) => sweep.rows.every((row) => !row.cells[c]));
  same(unreactive, golden.map.unreactive, `${name}: unreactive elements differ from sensitivity.map`);
  const silent = sweep.rows.flatMap((row, r) => (row.marked === 0 ? [r] : []));
  same(silent, golden.map.silent_cycles, `${name}: silent cycles differ from sensitivity.map`);

  const warnings = selfChecks(sweep.columns, sweep.rows);
  check(
    warnings.length === (unreactive.length ? 1 : 0) + (silent.length ? 1 : 0),
    `${name}: the self-checks did not report ${unreactive.length} unreactive and ${silent.length} silent`,
  );
  check(
    sweep.warnings.length === warnings.length,
    `${name}: run() dropped a self-check the panel has to show`,
  );

  // ---- 2. gap sweep ------------------------------------------------------

  const gaps = await run(base, "gap", window);
  golden.gaps.forEach((entry, i) => {
    same(marked(gaps, gaps.rows[i]), entry.changed, `${name}: gap ${entry.gap} differs from probe`);
  });

  // ---- 3. bit-flip sensitivity, against a key ----------------------------

  const keyed = {
    ...base,
    baseline: new Map(golden.base_pulses.map((c) => [c, { [golden.key]: 1 }])),
  };
  const flips = await run(keyed, "bit-flip", window);
  golden.flips.forEach((entry, i) => {
    same(
      marked(flips, flips.rows[i]),
      entry.changed,
      `${name}: flipping cycle ${entry.cycle} differs from probe`,
    );
  });

  // ---- 4. reset-value scan ----------------------------------------------

  const scan = await run(keyed, "reset-scan", window);
  check(scan.cellKind === "value", `${name}: a value scan must not report itself as changed-marks`);
  golden.scan.forEach((bits, cycle) => {
    same(
      Array.from(scan.rows[cycle].cells).join(""),
      bits,
      `${name}: state at cycle ${cycle} differs from the recorded trace`,
    );
  });
}

for (const sample of ["sample", "puzzle"]) {
  await checkSample(sample);
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} experiment checks against the golden sweeps`);