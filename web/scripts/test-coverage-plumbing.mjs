// Checks the plumbing boot.ts's scoring depends on -- two properties that are
// not about the weights in notebook/scoring.ts (test-scoring.mjs already
// covers those) but about *when* coverage is available and *whether* a solve
// keeps up with the notebook after it is recorded:
//
//   1. Coverage's denominator (notebook/basis.ts's `coverageBasis`) is a
//      function of a design handle, not of any panel having rendered. It is
//      called here directly, against a fake DesignClient, with no Notebook
//      panel in sight -- the exact case that used to leave `latestCoverage`
//      null in boot.ts.
//   2. A solved puzzle's saved best score rises when a claim settles after
//      the submit that solved it. This exercises the real pieces
//      `rescoreIfSolved` in boot.ts is built from -- `coverage`, `scoreCard`
//      and `recordScore`'s `Math.max` -- in the same sequence boot.ts calls
//      them, without importing boot.ts itself (it is a DOM entry point, not a
//      pure module).
//
// No browser: a small in-memory Storage stands in for localStorage, the same
// shim test-progress.mjs uses.
//
// Usage: node --experimental-strip-types web/scripts/test-coverage-plumbing.mjs

class FakeStorage {
  #map = new Map();
  getItem(key) {
    return this.#map.has(key) ? this.#map.get(key) : null;
  }
  setItem(key, value) {
    this.#map.set(key, String(value));
  }
  removeItem(key) {
    this.#map.delete(key);
  }
  key(index) {
    return [...this.#map.keys()][index] ?? null;
  }
  get length() {
    return this.#map.size;
  }
}

globalThis.localStorage = new FakeStorage();

const { coverageBasis } = await import("../src/notebook/basis.ts");
const { notebookFor } = await import("../src/notebook/store.ts");
const { coverage, scoreCard } = await import("../src/notebook/scoring.ts");
const { recordSolved, recordScore, solvedState } = await import("../src/store/progress.ts");

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

// ---- 1. coverage is available without a panel ever having rendered -------
//
// A fake DesignClient: just enough of the shape `coverageBasis` calls
// (`claimVocabulary`, `cone`, `flopDNet`) to exercise the same "step through
// the flop" it moved out of notebook-panel.ts unchanged. Nothing here is a
// panel, a DOM node, or anything the Notebook tab owns.
function fakeDesign({ flops, coneCalls }) {
  let calls = 0;
  return {
    async claimVocabulary() {
      calls++;
      return { data: { flops } };
    },
    async cone(net, opts) {
      return coneCalls(net, opts);
    },
    async flopDNet(net) {
      return { data: { instance: "dfrtp_0", net: `${net}_d` } };
    },
    get vocabCalls() {
      return calls;
    },
  };
}

{
  // `success` is a flop's Q net (leaf === "flop_q" at depth 1) -- basis must
  // step through the flop before walking the real cone, exactly as the
  // comment carried over from notebook-panel.ts describes.
  const design = fakeDesign({
    flops: ["f0", "f1"],
    coneCalls: (net, opts) => {
      if (opts.depth === 1) {
        return { data: { net, leaf: "flop_q", children: [] } };
      }
      return {
        data: {
          net,
          leaf: null,
          children: [{ net: "n1", leaf: null, children: [{ net: "n2", leaf: null, children: [] }] }],
        },
      };
    },
  });

  const basis = await coverageBasis(design, "success");
  check(
    basis.flops.length === 2,
    `basis carries the flop vocabulary with no panel involved, got ${basis.flops.length}`,
  );
  // The stepped-through net (`success_d`) plus everything the cone walk over
  // it found, plus the port itself.
  check(
    new Set(basis.coneNets).size === new Set(["success", "success_d", "n1", "n2"]).size &&
      ["success", "success_d", "n1", "n2"].every((n) => basis.coneNets.includes(n)),
    `basis steps through the flop and collects its cone, got ${JSON.stringify(basis.coneNets)}`,
  );

  // A second call against the SAME design instance must not re-walk the
  // cone -- boot.ts starts this as soon as the design is ready and the
  // Notebook panel awaits the same promise, not a second call.
  const again = await coverageBasis(design, "success");
  check(again === basis, "coverageBasis memoises per design instance rather than recomputing");
  check(design.vocabCalls === 1, `claimVocabulary is called once, not per caller, got ${design.vocabCalls}`);
}

{
  // No lock: the existing "no lock, no cone" fallback carries over unchanged
  // -- coverage falls back to the claims alone rather than an invented net.
  const design = fakeDesign({ flops: ["f0"], coneCalls: () => ({ data: { net: "x", leaf: null, children: [] } }) });
  const basis = await coverageBasis(design, null);
  check(basis.coneNets.length === 0, "a puzzle with no lock gets an empty cone, not a thrown error");
  check(basis.flops.length === 1, "the flop vocabulary is still measured with no lock");
}

// ---- 2. a solved puzzle's saved score rises when a later claim settles ---
//
// The exact sequence boot.ts runs: submit solves the puzzle and records a
// score: `coverage()` -> `scoreCard()` -> `recordScore()` (Math.max). Then a
// claim in the (already-shared, puzzle-keyed) notebook is settled -- the
// same event `rescoreIfSolved` reacts to -- and the same sequence runs again.
{
  const puzzleId = "test-puzzle-rescoring";
  const notebook = notebookFor(puzzleId);
  const flops = ["f0", "f1", "f2", "f3"];
  const coneNets = ["success", "n1", "n2", "n3"];

  function scoreNow() {
    const found = coverage(notebook, flops, coneNets);
    return scoreCard({
      solved: true,
      coverage: found,
      claimPoints: 0,
      model: null,
      requiredObservables: [],
      solveMs: null,
      parMinutes: null,
      hintsTaken: 0,
    });
  }

  // Submit: solved with an empty notebook -- a bare solve, per scoring.ts.
  recordSolved(puzzleId, { accepted: true, reason: "accepted" });
  const first = scoreNow();
  recordScore(puzzleId, first.total);
  const afterSubmit = solvedState(puzzleId)?.bestScore;
  check(afterSubmit === first.total, `the first score is saved, got ${afterSubmit} vs ${first.total}`);

  // A claim about a cone net settles PROVEN after the solve -- coverage's
  // cone component moves, so the score should too.
  const record = notebook.add({ kind: "structural", net: "n1", cell: "nand2" });
  notebook.record(record.id, {
    verdict: { kind: "PROVEN", method: "structural", cases: 1 },
    at: Date.now(),
    notes: [],
    call: "",
  });

  const second = scoreNow();
  check(second.total > first.total, `settling a claim after the solve must raise the score (${first.total} -> ${second.total})`);
  recordScore(puzzleId, second.total);
  const afterRescore = solvedState(puzzleId)?.bestScore;
  check(
    afterRescore === second.total,
    `the risen score is saved as the new best, got ${afterRescore} vs ${second.total}`,
  );

  // Re-scoring at the same (or a lower) coverage never lowers the saved
  // best -- `recordScore`'s own Math.max, exercised the way boot.ts uses it.
  recordScore(puzzleId, 0);
  check(
    solvedState(puzzleId)?.bestScore === second.total,
    "recordScore never lowers the saved best score",
  );
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} coverage-plumbing checks`);