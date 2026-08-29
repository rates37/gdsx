// Checks web/src/store/progress.ts: the one module that knows the whole
// `gdsx.*` localStorage namespace.
//
// The properties worth protecting, in the order they are checked below:
//
//   1. a solve is recorded and survives a reload -- recordSolved's write and
//      solvedState's read agree, including the first-solve timestamp and the
//      attempt count;
//   2. clearPuzzle deletes exactly one puzzle's keys, and none of another
//      puzzle's, and none of the app's global settings;
//   3. clearAll empties localStorage of every gdsx.* key and nothing else;
//   4. every `gdsx.*` key literal/template actually assigned to a key-named
//      variable anywhere in web/src is matched by a rule in KEY_RULES. This
//      is the guard against the failure mode the work order calls out: a
//      panel that starts persisting a new key outside the convention would
//      otherwise silently survive clearPuzzle/clearAll instead of failing a
//      test.
//
// No browser is available under plain Node, so a small in-memory Storage
// stands in for localStorage -- installed before anything in this file reads
// or writes it. progress.ts never touches localStorage at module load time
// (only inside its exported functions), so import order relative to the shim
// does not matter, but the shim is installed first regardless.
//
// Usage: node --experimental-strip-types web/scripts/test-progress.mjs

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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

// `onCleared` listens on `window`; under Node there is none, so a two-method
// stand-in collects the listeners and the test dispatches to them directly.
// Nothing else in progress.ts touches `window`.
const listeners = new Set();
globalThis.window = {
  addEventListener: (type, fn) => {
    if (type === "storage") listeners.add(fn);
  },
  removeEventListener: (type, fn) => {
    if (type === "storage") listeners.delete(fn);
  },
};
function dispatchStorage(event) {
  for (const fn of [...listeners]) fn(event);
}

const {
  recordSolved,
  solvedState,
  allProgress,
  clearPuzzle,
  clearAll,
  storageUsage,
  onCleared,
  KEY_RULES,
  VERSION,
} = await import("../src/store/progress.ts");

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const srcDir = path.join(webDir, "src");

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function reset() {
  globalThis.localStorage = new FakeStorage();
}

// ---- 1. a solve is recorded and survives a reload ----------------------

reset();
{
  check(solvedState("puzzle-a") === null, "an untouched puzzle has no progress record");

  recordSolved("puzzle-a", { accepted: false, observed: "success reads 0 at cycle 40", reason: "too early" });
  let state = solvedState("puzzle-a");
  check(state !== null, "an attempt is recorded even when rejected");
  check(state?.attempts === 1, `first attempt should count as 1, got ${state?.attempts}`);
  check(state?.solvedAt === undefined, "a rejected attempt does not set solvedAt");
  check(
    state?.lastObserved === "success reads 0 at cycle 40",
    "a rejection's observed string is still recorded",
  );

  recordSolved("puzzle-a", { accepted: true, observed: "success latches 1 at cycle 121", reason: "accepted" });
  // "Survives a reload": read the record back through the same public API a
  // freshly loaded page would use, rather than inspecting FakeStorage
  // directly -- there is no process boundary to cross under plain Node, so
  // this is the strongest statement this test can make.
  state = solvedState("puzzle-a");
  check(state?.attempts === 2, `second attempt should bring the count to 2, got ${state?.attempts}`);
  check(typeof state?.solvedAt === "string" && state.solvedAt.length > 0, "an accepted verdict sets solvedAt");
  const firstSolvedAt = state?.solvedAt;

  // A later re-check, accepted again, must not move the first-solve time.
  recordSolved("puzzle-a", { accepted: true, observed: "success latches 1 at cycle 121", reason: "accepted" });
  state = solvedState("puzzle-a");
  check(state?.attempts === 3, `third attempt should bring the count to 3, got ${state?.attempts}`);
  check(state?.solvedAt === firstSolvedAt, "solvedAt does not move on a later accepted re-check");

  const all = allProgress();
  check(all.length === 1, `allProgress should list the one puzzle attempted, got ${all.length}`);
  check(all[0]?.puzzleId === "puzzle-a", "allProgress reports the right puzzle id");
}

// ---- 2. clearPuzzle removes one puzzle's keys and no other's -----------

reset();
{
  // Two puzzles, one key per category this file knows is per-puzzle, plus
  // this file's own progress record for both.
  const categories = [
    "notebook",
    "labels",
    "model",
    "evidence",
    "repl-history",
    "sticky-classification",
  ];
  for (const puzzleId of ["puzzle-a", "puzzle-b"]) {
    for (const category of categories) {
      localStorage.setItem(`gdsx.${category}.${puzzleId}.v1`, "x");
    }
    recordSolved(puzzleId, { accepted: true, reason: "accepted" });
  }
  // App-level settings, which name no puzzle and must survive.
  localStorage.setItem("gdsx.workspace.layout.v2", "x");
  localStorage.setItem("gdsx.puzzle.last.v1", "puzzle-a");
  localStorage.setItem("gdsx.guide.state.v1", "x");
  localStorage.setItem("gdsx.guide.autostart", "1");
  localStorage.setItem("gdsx.dieview.mode", "3d");
  localStorage.setItem("gdsx.dieview.debug", "1");
  localStorage.setItem("gdsx.subtab.netlist", "browser");
  localStorage.setItem("gdsx.drawer.constraints", "1");

  const before = keysOf(localStorage);
  const expectedRemoved = [...categories.map((c) => `gdsx.${c}.puzzle-a.v1`), "gdsx.progress.puzzle-a.v1"];

  clearPuzzle("puzzle-a");

  const after = new Set(keysOf(localStorage));
  for (const key of expectedRemoved) {
    check(!after.has(key), `clearPuzzle("puzzle-a") should have removed ${key}`);
  }
  for (const key of before) {
    if (expectedRemoved.includes(key)) continue;
    check(after.has(key), `clearPuzzle("puzzle-a") should not have touched ${key} (not puzzle-a's)`);
  }
  check(solvedState("puzzle-b")?.attempts === 1, "puzzle-b's own progress survives clearing puzzle-a");
}

// ---- 3. clearAll leaves localStorage empty of gdsx.* keys ---------------

reset();
{
  localStorage.setItem("gdsx.workspace.layout.v2", "x");
  localStorage.setItem("gdsx.notebook.puzzle-a.v1", "x");
  recordSolved("puzzle-a", { accepted: true, reason: "accepted" });
  // Something outside the namespace, to prove clearAll is scoped to gdsx.*
  // and not "everything in localStorage".
  localStorage.setItem("unrelated.other-app.setting", "keep me");

  clearAll();

  const remaining = keysOf(localStorage);
  const gdsxRemaining = remaining.filter((k) => k.startsWith("gdsx."));
  check(gdsxRemaining.length === 0, `clearAll should leave no gdsx.* keys, found ${gdsxRemaining.join(", ")}`);
  check(
    remaining.includes("unrelated.other-app.setting"),
    "clearAll must not touch a key outside the gdsx.* namespace",
  );
}

function keysOf(storage) {
  const out = [];
  for (let i = 0; i < storage.length; i++) out.push(storage.key(i));
  return out;
}

// ---- 3b. storageUsage counts what a clear would delete ------------------
//
// The menu's settings affordance shows the total, and both confirmations
// quote a scoped figure while asking permission to delete it -- so the scope
// must be exactly clearPuzzle's, or the dialog describes the wrong thing.

reset();
{
  localStorage.setItem("gdsx.notebook.puzzle-a.v1", "aaaa");
  localStorage.setItem("gdsx.labels.puzzle-a.v1", "bb");
  localStorage.setItem("gdsx.notebook.puzzle-b.v1", "cccc");
  localStorage.setItem("gdsx.workspace.layout.v2", "dddd");
  localStorage.setItem("unrelated.other-app.setting", "not ours");

  const all = storageUsage();
  check(all.keys === 4, `storageUsage counts every gdsx.* key, got ${all.keys}`);
  check(
    all.bytes === keysOf(localStorage)
      .filter((k) => k.startsWith("gdsx."))
      .reduce((sum, k) => sum + (k.length + localStorage.getItem(k).length) * 2, 0),
    "storageUsage counts key and value, two bytes per UTF-16 unit, as the quota does",
  );

  const scoped = storageUsage("puzzle-a");
  check(scoped.keys === 2, `a scoped usage counts only that puzzle's keys, got ${scoped.keys}`);

  // The property that matters: the figure quoted in the confirmation is the
  // number of keys the clear actually removes.
  const beforeCount = keysOf(localStorage).filter((k) => k.startsWith("gdsx.")).length;
  clearPuzzle("puzzle-a");
  const afterCount = keysOf(localStorage).filter((k) => k.startsWith("gdsx.")).length;
  check(
    beforeCount - afterCount === scoped.keys,
    `storageUsage("puzzle-a") promised ${scoped.keys} items, clearPuzzle removed ${beforeCount - afterCount}`,
  );
  check(storageUsage("puzzle-a").keys === 0, "…and nothing of that puzzle's is left to count");
}

// ---- 3c. onCleared: another tab's clear reaches a running workspace -----
//
// The two screens are separate documents, so a clear from the menu cannot
// touch a workspace's in-memory state directly. It can leave a workspace open
// in another tab holding state that would be written straight back, which is
// what this signal exists to prevent -- and it must stay scoped, so tidying up
// one puzzle never disturbs a workspace open on another.

reset();
{
  let fired = 0;
  const stop = onCleared("puzzle-a", () => {
    fired++;
  });

  dispatchStorage({ key: "gdsx.notebook.puzzle-b.v1", newValue: null });
  check(fired === 0, "another puzzle's keys going away does not disturb this one");

  dispatchStorage({ key: "gdsx.notebook.puzzle-a.v1", newValue: "written" });
  check(fired === 0, "a write is not a clear");

  dispatchStorage({ key: "unrelated.other-app.setting", newValue: null });
  check(fired === 0, "another app's storage is not ours to react to");

  dispatchStorage({ key: "gdsx.notebook.puzzle-a.v1", newValue: null });
  check(fired === 1, "this puzzle's own key being removed fires once");

  dispatchStorage({ key: "gdsx.workspace.layout.v2", newValue: null });
  check(fired === 2, "so does an app-level key, which only clearAll removes");

  dispatchStorage({ key: null, newValue: null });
  check(fired === 3, "so does a whole-storage clear(), which reports a null key");

  stop();
  dispatchStorage({ key: "gdsx.notebook.puzzle-a.v1", newValue: null });
  check(fired === 3, "unsubscribing stops it");
}

// ---- 4. every key prefix found in the source is covered by KEY_RULES ---
//
// Re-derives the inventory from web/src itself rather than trusting a list
// written down once: a `const ..._KEY = "gdsx...."` or `this.key = \`gdsx...\``
// style assignment anywhere in the tree is a real localStorage key (every
// existing one is written exactly this way -- see workspace.ts, guide.ts,
// die-view-panel.ts, catalog.ts, mounts.ts, and the five per-puzzle stores).
// A dotted Python module path handed to `pyimport` or built for the Pyodide
// bridge (`gdsx.api`, `gdsx.api.cone(...)`, `gdsx.core.serial.to_dict`) also
// starts with "gdsx." but is never assigned to a key-named variable, so this
// scan does not see it -- confirmed by hand when this test was written
// (docs/game/web-ui-architecture.md links this file for the current list).

function listTsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listTsFiles(full));
    else if (entry.name.endsWith(".ts")) out.push(full);
  }
  return out;
}

const ASSIGN_RE = /(?:const\s+(\w+)|this\.(\w+))\s*=\s*(`gdsx\.[^`]*`|"gdsx\.[^"]*")/g;

/** `${VERSION}` right after a literal "v" becomes "v1" so it matches a
 *  `v\d+` rule; any other interpolation (a puzzle, panel or drawer id)
 *  becomes a plain token. */
function sample(pattern) {
  return pattern.replace(/v\$\{[^}]*\}/g, "v1").replace(/\$\{[^}]*\}/g, "sample-id");
}

const found = new Map(); // pattern -> [{file, name}]
for (const file of listTsFiles(srcDir)) {
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(ASSIGN_RE)) {
    const name = match[1] ?? match[2];
    if (!/key/i.test(name)) continue;
    const literal = match[3].slice(1, -1); // strip the surrounding quote/backtick
    const rel = path.relative(srcDir, file);
    if (!found.has(literal)) found.set(literal, []);
    found.get(literal).push({ file: rel, name });
  }
}

check(found.size >= 14, `expected to rediscover at least the 14 known key patterns, found ${found.size}`);

for (const [pattern, sites] of found) {
  const example = sample(pattern);
  const covered = KEY_RULES.some((rule) => rule.matches(example));
  check(
    covered,
    `no KEY_RULES entry matches ${pattern} (from ${sites.map((s) => `${s.file}:${s.name}`).join(", ")}); ` +
      `add a rule to web/src/store/progress.ts or clearPuzzle/clearAll will silently skip it`,
  );
}

check(typeof VERSION === "number", "progress.ts documents a VERSION");

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} progress-store checks (${found.size} key patterns rediscovered from source)`);