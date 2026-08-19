// Checks the write-up generator (web/src/notebook/writeup.ts, game-plan.md
// §8) against the one rule that must never break: PROVEN and LIKELY must
// never render as each other, and a DISPROVEN claim must be visibly struck.
//
// Builds fake stand-ins for Notebook, EvidenceLog, ModelStore and SimStore --
// generateWriteup only calls a handful of methods on each (`.all()`,
// `.latest()`, `.bitsOf()`, `.firstLatchedHigh()`, …), so plain objects that
// implement those are enough; there is no need to go through localStorage.
//
// Usage: node --experimental-strip-types web/scripts/test-writeup.mjs

import { generateWriteup } from "../src/notebook/writeup.ts";
import { VALIDATION_VECTORS } from "../src/model/store.ts";

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

// ---- fake claims, one of each verdict kind -------------------------------

let claimCounter = 0;
function claimRecord(claim, verdict, at) {
  claimCounter++;
  return {
    id: `c${claimCounter}`,
    claim,
    created: at,
    history: verdict === null ? [] : [{ verdict, at, notes: ["assumed enable held high"], call: `gdsx.${claim.kind}(...)` }],
  };
}

const provenStructural = claimRecord(
  { kind: "structural", net: "n149", cell: "nand2" },
  { kind: "PROVEN", method: "structural", cases: 0 },
  1000,
);
const provenExhaustive = claimRecord(
  { kind: "function", flop: "dfrtp_2_50", expression: "n96 & ~n136" },
  { kind: "PROVEN", method: "exhaustive", cases: 1 << 18 },
  2000,
);
const disproven = claimRecord(
  { kind: "structural", net: "n147", cell: "aoi21" },
  {
    kind: "DISPROVEN",
    counterexample: null,
    observed: ["nand2"],
    expected: ["aoi21"],
  },
  1500,
);
const likely = claimRecord(
  { kind: "invariant", net: "n201", value: 1, condition: "enable", regime: "sequential" },
  { kind: "LIKELY", method: "sampled", cases: 10000 },
  2500,
);
const unknown = claimRecord(
  { kind: "role", group: ["dfrtp_2_1"], role: "counter", width: 1, stimulus: {} },
  { kind: "UNKNOWN", reason: "too large" },
  1800,
);
const unverified = claimRecord(
  { kind: "support", flop: "dfrtp_2_9", leaves: ["I"], sense: "structural" },
  null,
  3000,
);

const fakeNotebook = {
  all: () => [provenStructural, provenExhaustive, disproven, likely, unknown, unverified],
};

// ---- fake evidence --------------------------------------------------------

const fakeEvidence = {
  all: () => [
    {
      id: "e1",
      recipe: "single-pulse sweep",
      summary: "121 runs · 92 watched · baseline: the current sequence",
      baseline: "the current sequence",
      lines: ["cycle 12: n201 rises", "cycle 40: n201 rises"],
      warnings: [],
      call: "gdsx.sweep(...)",
      at: 1200,
    },
  ],
};

// ---- fake model store -------------------------------------------------

function fakeModelStore(run) {
  return {
    language: "javascript",
    source: "function evaluate(pulses) {\n  return { success: pulses.size === 22 ? 1 : 0 };\n}",
    latest: () => run,
  };
}

// ---- fake sim store ---------------------------------------------------

function fakeSimStore(solved) {
  return {
    cycles: 8,
    bitsOf: () => new Uint8Array([0, 1, 0, 1, 0, 0, 1, 1]),
    firstLatchedHigh: () => (solved ? 6 : null),
  };
}

// ---- generate and check ------------------------------------------------

const validatedRun = {
  at: 4000,
  language: "javascript",
  vectors: VALIDATION_VECTORS,
  agreed: VALIDATION_VECTORS,
  seed: 1,
  validated: true,
  watch: ["success"],
  constant: [],
};

const md = generateWriteup(
  fakeNotebook,
  fakeEvidence,
  fakeModelStore(validatedRun),
  fakeSimStore(true),
  { successNet: "success", keyPort: "I", puzzleId: "two-stars" },
);

// The one rule that must never break: PROVEN and LIKELY are different words
// in different places, never interchangeable.
check(md.includes("proven · structural"), "structural PROVEN must render with its own label");
check(md.includes("proven ·") && md.includes("cases"), "exhaustive PROVEN must quote its case count");
check(md.includes("no counterexample in 10,000 vectors"), "LIKELY must say how many vectors, not 'proven'");

const likelyLineIdx = md.indexOf("n201 is 1 whenever enable");
const likelySlice = md.slice(likelyLineIdx, likelyLineIdx + 200);
check(!/\bproven\b/i.test(likelySlice), "the LIKELY claim's own line must never contain the word 'proven'");

// DISPROVEN must be struck through.
const disprovenLineIdx = md.indexOf("n147 is driven by aoi21");
check(disprovenLineIdx !== -1, "the disproven claim's text must appear");
check(
  md.slice(disprovenLineIdx - 2, disprovenLineIdx) === "~~",
  "a disproven claim must be struck through (~~...~~), per verdictStyle",
);

// UNKNOWN must say why, not silently disappear.
check(md.includes("unknown · too large"), "an UNKNOWN verdict must carry its reason");

// The unverified claim must be listed, but separately -- not interleaved
// into the settled timeline as if it had a verdict.
const settledHeading = md.indexOf("## Claims, in the order settled");
const unsettledHeading = md.indexOf("## Claims not yet verified");
check(settledHeading !== -1 && unsettledHeading !== -1, "both claim sections must be present");
check(unsettledHeading > settledHeading, "unsettled claims must be a separate, later section");
const settledSection = md.slice(settledHeading, unsettledHeading);
check(
  !settledSection.includes("D(dfrtp_2_9) depends exactly on"),
  "an unverified claim must not appear in the settled timeline",
);

// Settled claims are ordered by first-settled timestamp (structural@1000,
// disproven@1500, unknown@1800, exhaustive@2000, likely@2500) -- NOT creation
// order and not insertion order into fakeNotebook.all().
const order = [
  "n149 is driven by nand2",
  "n147 is driven by aoi21",
  "is a counter of width 1",
  "D(dfrtp_2_50)",
  "n201 is 1",
].map((needle) => settledSection.indexOf(needle));
check(
  order.every((idx, i) => i === 0 || idx > order[i - 1]),
  `settled claims must be ordered by first-settled timestamp, got indices ${order.join(",")}`,
);

// Model section: validated language must be agreement-over-N, never "proven".
const modelHeading = md.indexOf("## Model");
const modelSection = md.slice(modelHeading, md.indexOf("## Final key"));
check(modelSection.includes(String(VALIDATION_VECTORS)), "a validated model must quote its vector count");
check(!/\bproven\b/i.test(modelSection), "the model section must never say 'proven' -- agreement is not a proof");
check(modelSection.includes("function evaluate(pulses)"), "the model source must appear verbatim");

// Evidence section.
check(md.includes("single-pulse sweep"), "evidence must be listed");
check(md.includes("121 runs · 92 watched"), "evidence summary must be included");

// Result banner + final key.
check(md.includes("**Result: solved.**"), "a latched success net must report solved");
check(md.includes("01010011"), "the key bit string must be rendered");
check(md.includes("cycle 6"), "the banner must name the cycle success latched on");

// No hardcoded work/ or L-number reference of the generator's own -- it may
// only ever emit what the fakes above put into the notebook/evidence/model.
check(!/\bwork\//.test(md), "the write-up must never reference a work/ path");
check(!/\bL\d+\b/.test(md), "the write-up must never reference an internal L-number");

// Unsolved case.
const mdUnsolved = generateWriteup(
  fakeNotebook,
  fakeEvidence,
  fakeModelStore(null),
  fakeSimStore(false),
  { successNet: "success", keyPort: "I", puzzleId: "two-stars" },
);
check(mdUnsolved.includes("**Result: not solved.**"), "an unlatched success net must report not solved");
check(
  mdUnsolved.includes(`Validation needs agreement on ${VALIDATION_VECTORS} vectors`),
  "no model run yet must be stated as agreement-needed, not as a missing proof",
);

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} write-up checks`);
