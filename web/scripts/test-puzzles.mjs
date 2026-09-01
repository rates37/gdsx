// Checks the level picker's two halves: the descriptor derived from a baked
// puzzle (scripts/puzzle-index.mjs) and the choice of which one to open
// (web/src/puzzles/catalog.ts).
//
// The properties worth protecting, in the order they are checked below:
//
//   1. solution.json's answer never reaches the browser. The descriptor is
//      derived from the same file that holds the key bits, so this is the
//      check standing between a schema addition and a spoiler. The `checks`
//      block raises the stakes here -- it is derived by *reading* the answer
//      and the verifier spec -- so it gets its own pass (1b) asserting that
//      no plaintext answer survives into it, for every baked puzzle;
//   2. the derived driver protocol for the original puzzle is exactly the
//      block of constants main.ts used to carry -- the switcher is not
//      allowed to quietly change how that design is clocked;
//   3. a puzzle with no data input derives no key port rather than a wrong
//      one, and one with a bus track derives every port of the bus;
//   4. routing: `?puzzle=<id>` opens that puzzle's workspace and every other
//      URL -- no parameter, an empty one, an id the catalog does not have --
//      opens the menu.
//
// Usage: node --experimental-strip-types web/scripts/test-puzzles.mjs

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, DIFFICULTIES, SPOILERS } from "./puzzle-index.mjs";
import { answerDigestInput, normaliseAnswer } from "../src/puzzles/answer-normalise.mjs";
import { hashAnswer, verifySubmission } from "../src/puzzles/answer-check.ts";
import {
  chooseRoute,
  findPuzzle,
  loadCatalog,
  menuUrl,
  requestedId,
  urlFor,
} from "../src/puzzles/catalog.ts";
import { panelsFor, TOOL_PANELS } from "../src/puzzles/tools.ts";
import { SimStore } from "../src/sim/store.ts";
import { parseTapeBundle } from "../src/sim/tape.ts";

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
  return describe(dir, read(dir, "manifest.json"), read(dir, "solution.json"), read(dir, "hints.json"));
}

const puzzlesDir = path.join(rootDir, "puzzles");

/** Every puzzle directory that has a solution to check against. */
const bakedDirs = readdirSync(puzzlesDir, { withFileTypes: true })
  .filter((e) => e.isDirectory())
  .map((e) => e.name)
  .filter((name) =>
    statSync(path.join(puzzlesDir, name, "solution.json"), { throwIfNoEntry: false })?.isFile(),
  )
  .sort();

check(bakedDirs.length >= 7, "every authored puzzle should have a solution to check");

// ---- 1. no spoilers -----------------------------------------------------

for (const dir of bakedDirs) {
  const solution = read(dir, "solution.json");
  // `hints` is excluded from this scan: it is authored independently in
  // hints.json, not derived from solution.json, so its free-text analysis is
  // allowed to mention any number it likes -- a short numeric answer (e.g.
  // "8") coincidentally appearing inside an instance name or an unrelated
  // count ("reg_dfrtp_2_8", "80 instances") is not a leak of the answer.
  const { hints: _hints, ...withoutHints } = descriptorFor(dir);
  const serialised = JSON.stringify(withoutHints);
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

// ---- 1b. the checks block carries no plaintext answer -------------------
//
// The block above is derived by reading solution.json's answer and verifier
// spec, so "it copies nothing" is no longer the reason it is safe. This is.

/** Every way this puzzle's answer is written down, as whole answers.
 *  Whole, not per-element: a sweep over a track's individual values would
 *  compare a `1` in 4-nine-lives' symbol stream against a check's `value: 1`
 *  and fail for no reason. */
function plaintextAnswers(solution) {
  const out = [];
  if (solution.key?.bits) out.push(solution.key.bits);
  for (const track of solution.key?.tracks ?? []) {
    if (track.bits) out.push(track.bits);
    if (Array.isArray(track.values)) out.push(track.values.join(","));
  }
  if (solution.answer?.value !== undefined) out.push(solution.answer.value);
  for (const field of solution.answer?.fields ?? []) out.push(field.value);
  return out.map(normaliseAnswer).filter((a) => a.length > 0);
}

/** Every string and number in a nested value, with its path. */
function* leaves(value, at = "checks") {
  if (value === null || value === undefined) return;
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) yield* leaves(value[i], `${at}[${i}]`);
  } else if (typeof value === "object") {
    for (const [k, v] of Object.entries(value)) yield* leaves(v, `${at}.${k}`);
  } else if (typeof value === "string" || typeof value === "number") {
    yield [at, value];
  }
}

for (const dir of bakedDirs) {
  const solution = read(dir, "solution.json");
  const descriptor = descriptorFor(dir);
  const banned = new Set(plaintextAnswers(solution));

  for (const [at, leaf] of leaves(descriptor.checks)) {
    check(
      !banned.has(normaliseAnswer(leaf)),
      `${dir}: ${at} is the puzzle's answer in plain sight (${JSON.stringify(leaf)})`,
    );
  }

  const kind = descriptor.checks?.kind ?? null;
  if (solution.answer_kind === "parameter") {
    eq(kind, "digest", `${dir}: a parameter answer cannot be simulated, so it must be a digest`);
  }
  if (kind === "bus-at") {
    // The predicate SHAPE and not its value: nothing here says what the bus
    // should read, only where to read it.
    check(
      !("value" in descriptor.checks),
      `${dir}: a bus-at check must ship the shape without the value`,
    );
    check(descriptor.checks.bus.length > 0, `${dir}: a bus-at check needs bus nets`);
    check(descriptor.checks.when.net !== null, `${dir}: a bus-at check needs a conditioning port`);
  }
  if (kind === "latch") {
    eq(
      descriptor.checks.net,
      descriptor.driver.successNet,
      `${dir}: the lock the check names and the one the driver names must agree`,
    );
  }
  if (kind === "digest") {
    // Round-trip: recompute each hash from the authored plaintext. Without
    // this a refactor that quietly hashed the field name, or the empty
    // string, would still pass every assertion above.
    const authored = new Map(
      solution.answer?.fields?.map((f) => [f.name, f.value]) ??
        (solution.answer?.value !== undefined ? [["value", solution.answer.value]] : []),
    );
    eq(
      descriptor.checks.fields.map((f) => f.name),
      [...authored.keys()],
      `${dir}: the digest must cover exactly the authored fields`,
    );
    for (const field of descriptor.checks.fields) {
      check(/^[0-9a-f]{64}$/.test(field.hash), `${dir}: ${field.name}'s hash is not a sha256`);
      eq(
        field.hash,
        createHash("sha256")
          .update(answerDigestInput(descriptor.id, field.name, authored.get(field.name)), "utf8")
          .digest("hex"),
        `${dir}: ${field.name}'s hash is not the hash of the authored answer`,
      );
    }
  }
}

// puzzles/5-magic-number is why `constant` is not unconditionally a bus-at
// check: its answer is the 32-bit constant inside the comparator, while its
// predicate reads O[7:0] on the confirmation run, one shift past the match.
// Shipping that shape would compare 0x0c against the submitted constant and
// reject the right answer, so the derivation falls back to a digest.
eq(descriptorFor("1-warm-start").checks.kind, "bus-at", "a predicate that checks the answer");
eq(descriptorFor("5-magic-number").checks.kind, "digest", "a predicate that checks something else");

// ---- 2. the original puzzle is clocked exactly as it always was ---------

const original = descriptorFor("original-puzzle");
eq(original.driver.resetVector, { clk: 0, enable: 1, I: 0, rst_n: 0 }, "reset vector changed");
eq(original.driver.initialLevels, { enable: 1, rst_n: 1 }, "default input levels changed");
eq(original.driver.keyPort, "I", "key port changed");
eq(original.driver.successNet, "success", "success net changed");
check(original.driver.cycles >= 121, "the window must cover the puzzle's 121 input cycles");
eq(original.assets.tape, "puzzles/original-puzzle/tape.bin", "tape URL");

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

// ---- 4. which screen a URL opens ----------------------------------------
//
// The whole routing rule: a puzzle the catalog has opens the workspace, and
// everything else opens the menu. The deep links matter -- web/scripts/solve/
// and docs/game/original-puzzle-walkthrough.md both drive
// `?puzzle=original-puzzle` and expect the workspace, not a menu.

const catalog = [warmStart, original];

const deepLink = chooseRoute(catalog, { requested: "original-puzzle" });
eq(deepLink.kind, "puzzle", "?puzzle=<id> opens the workspace");
eq(deepLink.puzzle?.id, "original-puzzle", "…on the puzzle the URL names");
eq(
  chooseRoute(catalog, { requested: "original-puzzle" }).puzzle?.dir,
  original.dir,
  "a puzzle's directory name is a legal ?puzzle= value too",
);
eq(chooseRoute(catalog, {}).kind, "menu", "a bare URL opens the menu");
eq(chooseRoute(catalog, { requested: null }).kind, "menu", "…as does an absent parameter");
eq(chooseRoute(catalog, { requested: "" }).kind, "menu", "…and an empty one");
eq(
  chooseRoute(catalog, { requested: "no-such-puzzle" }).kind,
  "menu",
  "an id the catalog does not have opens the menu rather than a different puzzle",
);

eq(findPuzzle(catalog, "1-warm-start")?.id, "1-warm-start", "findPuzzle matches on id");
eq(findPuzzle(catalog, "no-such-puzzle"), undefined, "…and reports a miss rather than guessing");

// The toolbar's way back to the menu is the inverse of urlFor: drop the
// selection, keep everything else the player is carrying.
eq(
  menuUrl("https://example.test/?debug=1&puzzle=original-puzzle"),
  "https://example.test/?debug=1",
  "the route back to the menu drops the puzzle and keeps the rest of the query",
);
eq(
  chooseRoute(catalog, { requested: requestedId(new URL(menuUrl("https://example.test/?puzzle=1-warm-start")).search) }).kind,
  "menu",
  "…and the URL it produces routes to the menu, by the same rule",
);

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

// ---- 4b. difficulty is one of three bands, and describe() enforces it ---
//
// Six levels used to declare a number and one a word, so the menu printed
// "difficulty 2" beside "hard". The vocabulary is fixed here, at the point
// the bundle becomes the web app's data, so the browser only ever receives a
// word it can show verbatim.

for (const dir of bakedDirs) {
  const value = descriptorFor(dir).difficulty;
  check(
    value === null || DIFFICULTIES.includes(value),
    `${dir}: difficulty ${JSON.stringify(value)} must be one of ${DIFFICULTIES.join(", ")}`,
  );
}

let rejected = false;
try {
  describe("bogus", { difficulty: 3 }, {});
} catch {
  rejected = true;
}
check(rejected, "a manifest declaring a difficulty outside the vocabulary fails the sync");
eq(describe("nodiff", {}, {}).difficulty, null, "a manifest may still declare no difficulty");

// ---- 5. a missing catalog is empty, and invents nothing -----------------
//
// There is no built-in descriptor to fall back to any more. It was a second
// copy of the original puzzle's authored data -- title, blurb, driver, checks
// and hint tiers -- living in src/, and it had already drifted from the
// manifest it was copied from. Every fact about a puzzle now comes from its
// manifest.json and solution.json through describe(), and nothing in src/
// restates any of it.

const offline = await loadCatalog(async () => {
  throw new Error("no catalog here");
});
eq(offline, [], "a missing catalog is empty rather than an invented puzzle");
eq(
  chooseRoute(offline, { requested: "original-puzzle" }).kind,
  "menu",
  "…and every route into it lands on the menu, which says the build is unsynced",
);

// ---- 6. every tools_enabled key a manifest actually declares maps to a
//         panel -- a new key that TOOL_PANELS does not know about would
//         otherwise silently do nothing (web/src/puzzles/tools.ts). --------

for (const dir of bakedDirs) {
  const manifestPath = path.join(puzzlesDir, dir, "manifest.json");
  if (!statSync(manifestPath, { throwIfNoEntry: false })?.isFile()) continue;
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  for (const key of manifest.tools_enabled ?? []) {
    check(
      key in TOOL_PANELS,
      `puzzles/${dir}/manifest.json: tools_enabled key "${key}" is not in TOOL_PANELS`,
    );
  }
}

// ---- 6b. a sequence answer always opens the Sequence Editor -------------
//
// checks.kind === "latch" is the `sequence` answer kind (catalog.ts's
// LatchCheck) -- the player drives an input track and the app watches a
// lock net latch. There is nothing to submit without that panel, so it must
// be in the default layout regardless of what the manifest's tools_enabled
// says (original-puzzle's manifest predates the panel and never names it).

for (const dir of bakedDirs) {
  const descriptor = descriptorFor(dir);
  const panels = panelsFor(descriptor);
  if (descriptor.checks?.kind === "latch") {
    check(
      panels.includes("sequence-editor"),
      `${dir}: a sequence answer must open with the Sequence Editor in its default layout`,
    );
  } else {
    check(
      !panels.includes("sequence-editor"),
      `${dir}: a non-sequence answer should not be forced open on the Sequence Editor`,
    );
  }
}

// ---- 6c. every baked puzzle ships readable hints (20.1) -----------------
//
// hints.json is authored and baked but was never wired into REQUIRED or the
// descriptor -- a complete feature no player could reach. This asserts the
// fix stays true: every puzzle a player can actually load also ships a
// non-empty, well-formed set of hint tiers.

for (const dir of bakedDirs) {
  const hintsPath = path.join(puzzlesDir, dir, "hints.json");
  const hasHints = statSync(hintsPath, { throwIfNoEntry: false })?.isFile() ?? false;
  check(hasHints, `puzzles/${dir}/hints.json is missing`);
  if (!hasHints) continue;
  const descriptor = descriptorFor(dir);
  check(descriptor.hints.length > 0, `${dir}: the descriptor must carry at least one hint tier`);
  descriptor.hints.forEach((tier, i) => {
    check(typeof tier.tier === "number", `${dir}: hint ${i} needs a numeric tier index`);
    check(
      typeof tier.text === "string" && tier.text.trim().length > 0,
      `${dir}: hint ${i} needs non-empty text`,
    );
  });
  // Tiers are revealed front-to-back by position, not by the `tier` number
  // itself (workspace/toolbar.ts's HintsControl indexes `hints[revealed]`) --
  // the authored numbering is not always contiguous (three of the seven
  // puzzles number their four tiers 0, 1, 2, 4, skipping 3), so this only
  // requires the numbers to be strictly increasing, which is what "revealed
  // in order" actually depends on.
  const tierNumbers = descriptor.hints.map((t) => t.tier);
  check(
    tierNumbers.every((t, i) => i === 0 || t > tierNumbers[i - 1]),
    `${dir}: hint tiers must be strictly increasing (got ${JSON.stringify(tierNumbers)})`,
  );
}

// ---- 7. the block is actually enough to verify with ---------------------
//
// The point of every assertion above is that the answer is absent. This is
// the other half: that what IS present suffices. Each case runs the real
// baked gate tape, so an accept here is the design itself agreeing.

function storeFor(dir) {
  const descriptor = descriptorFor(dir);
  const bin = readFileSync(path.join(puzzlesDir, dir, "tape.bin"));
  const tape = parseTapeBundle(bin.buffer.slice(bin.byteOffset, bin.byteOffset + bin.byteLength));
  return new SimStore(tape, descriptor.driver.cycles, {
    resetVector: descriptor.driver.resetVector,
    initialLevels: descriptor.driver.initialLevels,
  });
}

async function verdict(dir, submission, store) {
  return verifySubmission(descriptorFor(dir), submission, store);
}

// sequence: drive the key, the lock latches. Nothing extra was needed.
const firstLight = storeFor("0-first-light");
check((await verdict("0-first-light", { tracks: { I: "101101" } }, firstLight)).accepted,
  "0-first-light: the key must be accepted");
const wrongWord = await verdict("0-first-light", { tracks: { I: "111111" } }, firstLight);
check(!wrongWord.accepted, "0-first-light: a wrong word must be rejected");
check(
  (wrongWord.observed ?? "").includes("success"),
  `0-first-light: a rejection must say what was measured, not just "wrong" (got ${wrongWord.observed})`,
);

// constant: the app simulates, reads the bus at the conditioning cycle and
// compares against the submission. The descriptor never saw the value.
const warmStore = storeFor("1-warm-start");
for (const typed of [8, "8", "0x08", "0x8", "0b1000"]) {
  check((await verdict("1-warm-start", { value: typed }, warmStore)).accepted,
    `1-warm-start: ${JSON.stringify(typed)} is the same answer as 8`);
}
const wrongByte = await verdict("1-warm-start", { value: "0x2A" }, warmStore);
check(!wrongByte.accepted, "1-warm-start: 0x2A -- the trap answer -- must be rejected");
check(
  (wrongByte.observed ?? "").includes("0x08"),
  `1-warm-start: a rejection must report the byte the design actually read (got ${wrongByte.observed})`,
);

// parameter: hashed, so the submission is hashed the same way and compared.
for (const typed of ["0xA3000000", "0xa3000000", 2734686208, "2_734_686_208"]) {
  check(
    (await verdict("2-polynomial", { fields: { polynomial: typed, state_at_cycle_1e12: "0xBAB7B0EF" } })).accepted,
    `2-polynomial: ${JSON.stringify(typed)} normalises to the same polynomial`,
  );
}
check(
  !(await verdict("2-polynomial", { fields: { polynomial: "0xA3000001", state_at_cycle_1e12: "0xBAB7B0EF" } })).accepted,
  "2-polynomial: a near-miss mask must be rejected",
);
check(
  !(await verdict("2-polynomial", { fields: { polynomial: "0xA3000000" } })).accepted,
  "2-polynomial: both fields are required",
);

// The two hashers agree byte-for-byte. They are what stops a correct answer
// being rejected because the baker and the browser normalised differently.
eq(
  await hashAnswer("2-polynomial", "polynomial", "0xa3000000"),
  descriptorFor("2-polynomial").checks.fields[0].hash,
  "the browser's SHA-256 must reproduce the baker's",
);

check(
  !(await verifySubmission({ ...descriptorFor("original-puzzle"), checks: null }, { value: 1 }))
    .accepted,
  "a puzzle with no checks reports that, rather than throwing",
);

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} puzzle-catalog checks`);