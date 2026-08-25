// Checks the level picker's two halves: the descriptor derived from a baked
// puzzle (scripts/puzzle-index.mjs) and the choice of which one to open
// (web/src/puzzles/catalog.ts).
//
// The properties worth protecting, in the order they are checked below:
//
//   1. solution.json's answer never reaches the browser. The descriptor is
//      derived from the same file that holds the key bits, so this is the
//      check standing between a schema addition and a spoiler;
//   2. the derived driver protocol for the original puzzle is exactly the
//      block of constants main.ts used to carry -- the switcher is not
//      allowed to quietly change how that design is clocked;
//   3. a puzzle with no data input derives no key port rather than a wrong
//      one, and one with a bus track derives every port of the bus;
//   4. selection prefers the URL, then the last played, then the first, and
//      an unknown id opens something rather than nothing.
//
// Usage: node --experimental-strip-types web/scripts/test-puzzles.mjs

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, SPOILERS } from "./puzzle-index.mjs";
import { choosePuzzle, FALLBACK, loadCatalog, urlFor } from "../src/puzzles/catalog.ts";
import { TOOL_PANELS } from "../src/puzzles/tools.ts";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootDir = path.dirname(webDir);

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function eq(got, want, message) {
  check(
    JSON.stringify(got) === JSON.stringify(want),
    `${message}\n      got:  ${JSON.stringify(got)}\n      want: ${JSON.stringify(want)}`,
  );
}

function read(dir, name) {
  return JSON.parse(readFileSync(path.join(rootDir, "puzzles", dir, name), "utf8"));
}

function descriptorFor(dir) {
  return describe(dir, read(dir, "manifest.json"), read(dir, "solution.json"));
}

// ---- 1. no spoilers -----------------------------------------------------

for (const dir of ["original-puzzle", "1-warm-start"]) {
  const solution = read(dir, "solution.json");
  const serialised = JSON.stringify(descriptorFor(dir));
  for (const field of SPOILERS) {
    check(
      !serialised.includes(`"${field}"`),
      `${dir}: the descriptor must not carry solution.json's ${field}`,
    );
  }
  const answer = solution.key?.bits ?? String(solution.answer?.value ?? "");
  check(
    answer.length === 0 || !serialised.includes(answer),
    `${dir}: the descriptor leaks the answer itself`,
  );
}

// ---- 2. the original puzzle is clocked exactly as it always was ---------

const original = descriptorFor("original-puzzle");
eq(original.driver.resetVector, { clk: 0, enable: 1, I: 0, rst_n: 0 }, "reset vector changed");
eq(original.driver.initialLevels, { enable: 1, rst_n: 1 }, "default input levels changed");
eq(original.driver.keyPort, "I", "key port changed");
eq(original.driver.successNet, "success", "success net changed");
check(original.driver.cycles >= 121, "the window must cover the puzzle's 121 input cycles");
eq(original.assets.tape, "/puzzles/original-puzzle/tape.bin", "tape URL");

// ---- 3. shapes the single-track puzzle does not have --------------------

const warmStart = descriptorFor("1-warm-start");
eq(warmStart.driver.keyPort, null, "a puzzle with no data input has no key port");
eq(warmStart.driver.trackPorts, [], "a puzzle with no data input has no tracks");
eq(warmStart.driver.successNet, "success", "the constant puzzle's observation port");
check(warmStart.driver.cycles > 520, "the window must cover the puzzle's 520 run cycles");

const busPuzzle = describe(
  "bus",
  { id: "bus", title: "Bus" },
  {
    answer_kind: "sequence",
    key: {
      tracks: [
        { bus: ["I[1]", "I[0]"], values: [0, 3, 2] },
        { port: "go", bits: "001" },
      ],
    },
    driver: {
      clock: { port: "clk" },
      reset: { port: "rst_n", active_low: true, protocol: [{ cycles: 1, values: { rst_n: 0 } }] },
      static_inputs: { enable: 1 },
      input_cycles: 3,
    },
    verify: { predicate: { type: "port_reaches_value_by_cycle", port: "done", by_cycle: 3 } },
  },
);
eq(busPuzzle.driver.trackPorts, ["I[1]", "I[0]", "go"], "every port of a bus track is driven");
eq(
  busPuzzle.driver.resetVector,
  { clk: 0, enable: 1, "I[1]": 0, "I[0]": 0, go: 0, rst_n: 0 },
  "a multi-track reset vector holds every track at 0 under the reset values",
);
eq(busPuzzle.driver.keyPort, "I[1]", "the key port is the first track's first bit");
eq(busPuzzle.driver.successNet, "done", "the success net comes from the predicate");

// ---- 4. which puzzle opens ----------------------------------------------

const catalog = [warmStart, original];
eq(
  choosePuzzle(catalog, { requested: "original-puzzle" }).id,
  "original-puzzle",
  "the URL wins",
);
eq(
  choosePuzzle(catalog, { requested: null, lastPlayed: "original-puzzle" }).id,
  "original-puzzle",
  "the last played wins when the URL says nothing",
);
eq(choosePuzzle(catalog, {}).id, "1-warm-start", "the first puzzle is the default");
eq(
  choosePuzzle(catalog, { requested: "no-such-puzzle", lastPlayed: "original-puzzle" }).id,
  "original-puzzle",
  "an unknown id falls through rather than failing",
);
eq(choosePuzzle(catalog, { requested: "no-such-puzzle" }).id, "1-warm-start", "…all the way down");

eq(
  urlFor("1-warm-start", "https://example.test/?debug=1"),
  "https://example.test/?debug=1&puzzle=1-warm-start",
  "switching levels keeps the rest of the query",
);
eq(
  urlFor("a", "https://example.test/?puzzle=b"),
  "https://example.test/?puzzle=a",
  "switching levels replaces an existing selection",
);

// ---- 5. a missing catalog degrades to the built-in puzzle ---------------

const offline = await loadCatalog(async () => {
  throw new Error("no catalog here");
});
eq(offline.map((p) => p.id), [FALLBACK.id], "a missing catalog falls back to one built-in puzzle");
check(
  FALLBACK.assets.render.startsWith("/samples/"),
  "the fallback must point at the sample assets, which ship without a sync",
);

// ---- 6. every tools_enabled key a manifest actually declares maps to a
//         panel -- a new key that TOOL_PANELS does not know about would
//         otherwise silently do nothing (web/src/puzzles/tools.ts). --------

const puzzlesDir = path.join(rootDir, "puzzles");
for (const entry of readdirSync(puzzlesDir, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const manifestPath = path.join(puzzlesDir, entry.name, "manifest.json");
  if (!statSync(manifestPath, { throwIfNoEntry: false })?.isFile()) continue;
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const dir = entry.name;
  for (const key of manifest.tools_enabled ?? []) {
    check(
      key in TOOL_PANELS,
      `puzzles/${dir}/manifest.json: tools_enabled key "${key}" is not in TOOL_PANELS`,
    );
  }
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} puzzle-catalog checks`);