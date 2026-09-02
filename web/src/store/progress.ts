// What the game remembers about a player's progress, and the one module that
// knows the whole `gdsx.*` localStorage namespace.
//
// Nothing else records that a puzzle was solved: main.ts's submitControl
// calls verifySubmission, the toolbar shows the resulting Verdict, and until
// this file existed the fact was discarded the moment the popover closed.
// Reload the page and the game had forgotten you won.
//
// It is also the namespace's one owner for a second reason. Every panel that
// needed to persist something picked its own `gdsx.<thing>.<id>` key --
// notebook claims, labels, the model builder, evidence, REPL history, the
// sticky-flops classification, the driven stimulus, plus app-level settings like the workspace
// layout and the guide's state -- and nothing enumerated them. clearPuzzle
// and clearAll (the level menu's per-puzzle and full reset, 19.2/19.3) need to
// delete every key a puzzle owns without keeping a hardcoded list that goes
// stale the next time a panel adds one, so they enumerate `localStorage`
// itself and match each real key against KEY_RULES below.
//
// scripts/test-progress.mjs derives its own list of key literals/templates
// straight from the source -- every `const ..._KEY = "gdsx...."` and
// `this.key = \`gdsx....\`` style assignment -- and asserts each is covered by
// a rule here. A panel that starts persisting a new `gdsx.*` key without a
// matching rule fails that test loudly instead of silently surviving a clear.
//
// Persistence follows the pattern in workspace.ts: best effort, and
// unreadable saved state is discarded rather than thrown.

import type { Verdict } from "../puzzles/answer-check.ts";

/** Bumping this only affects new progress records -- see solvedState's
 *  version check. It does not migrate or move any of the *other* keys this
 *  file knows about; those are out of scope for this step. */
export const VERSION = 1;

export interface ProgressRecord {
  puzzleId: string;
  /** ISO 8601. Set once, the first time a submission for this puzzle is
   *  accepted, and never overwritten after -- a later re-check does not move
   *  the first-solve timestamp. */
  solvedAt?: string;
  /** Every submit attempt against this puzzle, accepted or not -- a puzzle
   *  that took eleven tries should be able to say so. */
  attempts: number;
  /** The most recent verdict's `observed` string. Absent if the puzzle's
   *  check kind never observes anything (a `digest` check, by design) or no
   *  attempt has carried one yet. */
  lastObserved?: string;
  /** How many hint tiers have been revealed, highest tier taken plus one.
   *  Tiers are revealed in order (workspace/toolbar.ts's `HintsControl`), so
   *  this single count is the whole history -- there is no "took tier 2 but
   *  not tier 1" to represent. 0 if none have been taken. Scoring's
   *  "hints taken" penalty reads this; this file only records it. */
  hintsTaken: number;
  /** Milliseconds spent with this puzzle open and the tab actually visible --
   *  see `recordEngagement`. Keeps accumulating after a solve. 0 on a record
   *  written before this field existed. */
  engagedMs: number;
  /** `engagedMs` at the moment of the first accepted verdict, frozen there.
   *  This, not `engagedMs`, is what the wall-clock bonus is measured against:
   *  how long the solve took, not how long the tab has been open since.
   *  Absent while unsolved, and on a record solved before this field existed. */
  solveMs?: number;
  /** The best score this puzzle has been solved with -- see
   *  notebook/scoring.ts's `scoreCard`. Persisted because the level menu is a
   *  separate document that loads none of the stores a score is derived from.
   *  Absent until a solve is scored. */
  bestScore?: number;
}

interface Saved {
  version: number;
  attempts: number;
  solvedAt?: string;
  lastObserved?: string;
  hintsTaken?: number;
  engagedMs?: number;
  solveMs?: number;
  bestScore?: number;
}

function progressKey(puzzleId: string): string {
  return `gdsx.progress.${puzzleId}.v${VERSION}`;
}

/**
 * Read-modify-write one puzzle's record.
 *
 * Every writer below goes through this rather than assembling a `Saved` of its
 * own. Each of them only cares about one or two fields, but a saved record is
 * a whole object: a writer that builds its own drops every field it has not
 * heard of, so the next field anyone adds is silently erased by whichever
 * writer was not updated. `fn` receives the current record (an empty one if
 * there is none) and returns the next.
 */
function update(puzzleId: string, fn: (current: Saved) => Saved): void {
  const existing = solvedState(puzzleId);
  const current: Saved = {
    version: VERSION,
    attempts: existing?.attempts ?? 0,
    solvedAt: existing?.solvedAt,
    lastObserved: existing?.lastObserved,
    hintsTaken: existing?.hintsTaken,
    engagedMs: existing?.engagedMs,
    solveMs: existing?.solveMs,
    bestScore: existing?.bestScore,
  };
  try {
    localStorage.setItem(progressKey(puzzleId), JSON.stringify(fn(current)));
  } catch (err) {
    console.warn("gdsx: could not save progress", err);
  }
}

/**
 * Record one submission against `puzzleId`. Call this for every attempt,
 * accepted or not -- see boot.ts's submitControl, which calls it right after
 * verifySubmission resolves, before the toolbar shows the verdict.
 */
export function recordSolved(puzzleId: string, verdict: Verdict): void {
  update(puzzleId, (current) => {
    const firstSolve = verdict.accepted && current.solvedAt === undefined;
    return {
      ...current,
      attempts: current.attempts + 1,
      solvedAt: current.solvedAt ?? (verdict.accepted ? new Date().toISOString() : undefined),
      lastObserved: verdict.observed ?? current.lastObserved,
      // Frozen at the first solve, alongside solvedAt and for the same reason:
      // a later re-check is not a second solve and must not restate how long
      // the first one took.
      solveMs: firstSolve ? (current.engagedMs ?? 0) : current.solveMs,
    };
  });
}

/**
 * Add `ms` of engaged time to `puzzleId` -- time the puzzle was open and the
 * tab was actually visible. Called by boot.ts's heartbeat.
 *
 * Engaged rather than calendar time on purpose: the wall-clock component is a
 * *bonus*, and a player who leaves the tab open overnight has not spent that
 * night solving. Accumulated in small increments rather than derived from a
 * start timestamp so that closing the tab loses at most one interval, with no
 * "session end" to record.
 */
export function recordEngagement(puzzleId: string, ms: number): void {
  if (!(ms > 0)) return;
  update(puzzleId, (current) => ({
    ...current,
    engagedMs: (current.engagedMs ?? 0) + ms,
  }));
}

/**
 * Record `score` as this puzzle's result, keeping the best ever seen.
 *
 * `Math.max` for the same reason `solvedAt` does not move: a player who
 * re-checks an accepted answer after clearing their notebook has not undone
 * the work they did, and a score that could fall would make re-checking an
 * answer a risk. Separate from `recordSolved` because the score is derived
 * from the record that call writes -- "solved" is one of its inputs.
 */
export function recordScore(puzzleId: string, score: number): void {
  update(puzzleId, (current) => ({
    ...current,
    bestScore: Math.max(current.bestScore ?? 0, score),
  }));
}

/**
 * Record that `tier` (0-based) has been revealed for `puzzleId`. Tiers are
 * always revealed in order from the toolbar's hints popover, so the saved
 * count only ever grows -- `Math.max` guards against an out-of-order call
 * ever moving it backwards, which would silently un-charge a hint already
 * taken.
 */
export function recordHintTaken(puzzleId: string, tier: number): void {
  update(puzzleId, (current) => ({
    ...current,
    hintsTaken: Math.max(current.hintsTaken ?? 0, tier + 1),
  }));
}

/** `puzzleId`'s progress, or null if it has never been attempted (or its
 *  saved record could not be read). */
export function solvedState(puzzleId: string): ProgressRecord | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(progressKey(puzzleId));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const saved = JSON.parse(raw) as Saved;
    if (saved.version !== VERSION || typeof saved.attempts !== "number") return null;
    return {
      puzzleId,
      solvedAt: saved.solvedAt,
      attempts: saved.attempts,
      lastObserved: saved.lastObserved,
      hintsTaken: saved.hintsTaken ?? 0,
      engagedMs: saved.engagedMs ?? 0,
      solveMs: saved.solveMs,
      bestScore: saved.bestScore,
    };
  } catch (err) {
    console.warn("gdsx: discarding unreadable progress", err);
    try {
      localStorage.removeItem(progressKey(puzzleId));
    } catch {
      // Storage disabled -- nothing to clean up.
    }
    return null;
  }
}

/**
 * The day a solve happened, `YYYY-MM-DD`, or null when there is none or the
 * saved timestamp cannot be read -- saved state is player-writable and may be
 * anything.
 *
 * Here rather than in either caller because both the menu's cards and the
 * workspace's solved marker show the same fact, and this file owns the format
 * it is stored in.
 */
export function solvedDay(iso: string | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return at.toISOString().slice(0, 10);
}

/** Every puzzle with a progress record, for the level menu. */
export function allProgress(): ProgressRecord[] {
  const records: ProgressRecord[] = [];
  for (const key of gdsxKeys()) {
    const match = /^gdsx\.progress\.(.+)\.v\d+$/.exec(key);
    if (!match) continue;
    const record = solvedState(match[1]);
    if (record) records.push(record);
  }
  return records;
}

// ---------------------------------------------------------------------------
// The namespace: every key shape known to exist under `gdsx.*`, so
// clearPuzzle/clearAll can sweep it without a hardcoded list of key names.
// ---------------------------------------------------------------------------

interface KeyRule {
  /** Shown in the coverage test's failure message. */
  name: string;
  /** True when the key is namespaced per puzzle -- clearPuzzle sweeps these,
   *  narrowed to one puzzle id. False for a key global to the app (layout,
   *  guide state, dieview settings, per-panel drawer/subtab memory), which
   *  clearPuzzle must leave alone. */
  perPuzzle: boolean;
  matches(key: string, puzzleId?: string): boolean;
}

function exact(name: string, literal: string): KeyRule {
  return { name, perPuzzle: false, matches: (key) => key === literal };
}

/** `gdsx.<prefix><id>` where `<id>` is a panel or drawer id -- never a puzzle
 *  id -- so these are global. `gdsx.subtab.<panelId>`, `gdsx.drawer.<id>`. */
function globalNamespaced(name: string, prefix: string): KeyRule {
  return {
    name,
    perPuzzle: false,
    matches: (key) => key.startsWith(prefix) && !key.slice(prefix.length).includes("."),
  };
}

/** `gdsx.<category>.<puzzleId>.v<N>` -- one store per puzzle, versioned. */
function perPuzzleVersioned(name: string, category: string): KeyRule {
  const re = new RegExp(`^gdsx\\.${category}\\.(.+)\\.v\\d+$`);
  return {
    name,
    perPuzzle: true,
    matches: (key, puzzleId) => {
      const match = re.exec(key);
      return match !== null && (puzzleId === undefined || match[1] === puzzleId);
    },
  };
}

export const KEY_RULES: readonly KeyRule[] = [
  exact("workspace-layout", "gdsx.workspace.layout.v3"),
  exact("puzzle-last-played", "gdsx.puzzle.last.v1"),
  exact("guide-state", "gdsx.guide.state.v1"),
  exact("guide-autostart", "gdsx.guide.autostart"),
  exact("dieview-mode", "gdsx.dieview.mode"),
  exact("dieview-debug", "gdsx.dieview.debug"),
  globalNamespaced("subtab", "gdsx.subtab."),
  globalNamespaced("drawer", "gdsx.drawer."),
  perPuzzleVersioned("notebook", "notebook"),
  perPuzzleVersioned("labels", "labels"),
  perPuzzleVersioned("model", "model"),
  perPuzzleVersioned("evidence", "evidence"),
  perPuzzleVersioned("repl-history", "repl-history"),
  perPuzzleVersioned("sticky-classification", "sticky-classification"),
  perPuzzleVersioned("stimulus", "stimulus"),
  perPuzzleVersioned("progress", "progress"),
];

const GDSX_PREFIX = "gdsx.";

function gdsxKeys(): string[] {
  const keys: string[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key !== null && key.startsWith(GDSX_PREFIX)) keys.push(key);
    }
  } catch {
    // Storage disabled -- nothing to enumerate.
  }
  return keys;
}

function removeKey(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Storage disabled -- nothing to remove.
  }
}

/**
 * Delete every key `puzzleId` owns: every store above namespaced per puzzle,
 * this file's own progress record included. Global settings -- layout, guide
 * state, dieview mode, per-panel drawer/subtab memory -- belong to the app,
 * not the puzzle, and are left alone.
 */
export function clearPuzzle(puzzleId: string): void {
  for (const key of gdsxKeys()) {
    if (KEY_RULES.some((rule) => rule.perPuzzle && rule.matches(key, puzzleId))) removeKey(key);
  }
}

/** Delete every `gdsx.*` key there is -- a full reset, including app-level
 *  settings. Sweeps live keys rather than KEY_RULES, so it also removes a key
 *  a future panel invents outside the convention (clearPuzzle cannot, since
 *  it has to know a key is that puzzle's; clearAll only has to know it is
 *  gdsx's). */
export function clearAll(): void {
  for (const key of gdsxKeys()) removeKey(key);
}

/** What the game is holding in `localStorage`: how many keys, and how many
 *  bytes they occupy. Bytes are counted the way the quota is -- both the key
 *  and the value, two bytes per UTF-16 code unit -- so the number can be
 *  compared against the ~5 MB browsers allow. */
export interface StorageUsage {
  keys: number;
  bytes: number;
}

/**
 * How much storage the game is using, in total or for one puzzle.
 *
 * With `puzzleId`, counts exactly the keys `clearPuzzle(puzzleId)` would
 * delete -- so a confirmation can say what is about to go, in the units a
 * player can check afterwards.
 */
export function storageUsage(puzzleId?: string): StorageUsage {
  let keys = 0;
  let bytes = 0;
  for (const key of gdsxKeys()) {
    if (puzzleId !== undefined) {
      const owned = KEY_RULES.some((rule) => rule.perPuzzle && rule.matches(key, puzzleId));
      if (!owned) continue;
    }
    let value: string | null = null;
    try {
      value = localStorage.getItem(key);
    } catch {
      // Storage disabled -- nothing to measure.
    }
    keys++;
    bytes += (key.length + (value?.length ?? 0)) * 2;
  }
  return { keys, bytes };
}

/**
 * Call `fn` when another tab clears state this puzzle depends on.
 *
 * The two screens are separate documents, so clearing from the menu can never
 * reach into a running workspace's memory -- but a player may well have the
 * menu open in one tab and the puzzle in another, and half the panels hold
 * their state in memory and would write it straight back on the next change.
 * The workspace answers by reloading (see boot.ts).
 *
 * Scoped deliberately: a `storage` event for another puzzle's keys is ignored,
 * so clearing a puzzle you are not in disturbs nothing. `key === null` is the
 * whole-storage `clear()` case, and a non-null `newValue` is a write rather
 * than a removal -- neither of those is another tab's clear of this puzzle.
 *
 * Returns an unsubscribe function.
 */
export function onCleared(puzzleId: string, fn: () => void): () => void {
  const handler = (event: StorageEvent): void => {
    if (event.storageArea && event.storageArea !== localStorage) return;
    if (event.key === null) {
      fn();
      return;
    }
    if (event.newValue !== null || !event.key.startsWith(GDSX_PREFIX)) return;
    // Per-puzzle rules narrow to this puzzle; app-level ones (layout, guide
    // state, last played) match whoever removed them, which only clearAll
    // does -- and that concerns every open puzzle.
    if (KEY_RULES.some((rule) => rule.matches(event.key!, puzzleId))) fn();
  };
  window.addEventListener("storage", handler);
  return () => window.removeEventListener("storage", handler);
}