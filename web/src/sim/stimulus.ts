// The stimulus the player is holding, kept across reloads.
//
// Everything else a session produces is already persisted per puzzle -- the
// notebook, the glossary, the model, the evidence log, the REPL history, the
// sticky-flop classification. The one thing that was not is the sequence
// itself, which is the *answer*: a player who solved a puzzle and came back
// the next day found an empty Sequence Editor, and the write-up's "Final key"
// section printed a run of zeroes under a Score table that said the puzzle was
// solved.
//
// Saved as bit strings rather than as the `Uint8Array`s `SimStore` holds:
// they are what the Sequence Editor's import/export field already speaks, what
// `setPattern` already accepts, and what a player would paste into either. A
// track is one character per cycle.
//
// Only a stimulus the player actually drove is saved (`SimStore.hasStimulus`).
// Restoring one the puzzle merely started with would mark the store as edited
// and make the Cone Walker's flatten drawer claim a sequence had been driven
// when none had.

import type { SimStore } from "./store.ts";

const VERSION = 1;

/** Milliseconds of quiet before a save. Painting a run of cells is a burst of
 *  `setBit` calls and there is no reason to serialise the whole stimulus for
 *  each one. */
const SAVE_DEBOUNCE_MS = 400;

interface Saved {
  version: number;
  cycles: number;
  /** port -> "0101…", one character per cycle. */
  tracks: Record<string, string>;
}

function storageKey(puzzleId: string): string {
  // Written as an assignment rather than a bare `return` so that
  // scripts/test-progress.mjs's source scan finds it: that scan is what
  // guarantees every `gdsx.*` key this app writes is covered by a rule in
  // store/progress.ts and therefore actually swept by clearPuzzle/clearAll.
  const key = `gdsx.stimulus.${puzzleId}.v${VERSION}`;
  return key;
}

function bitsOf(store: SimStore, port: string): string {
  return Array.from(store.bitsOf(port), (b) => (b ? "1" : "0")).join("");
}

/** The current stimulus, as the Sequence Editor's own export would write it. */
export function snapshotStimulus(store: SimStore): Saved {
  const tracks: Record<string, string> = {};
  for (const port of store.inputPorts) tracks[port] = bitsOf(store, port);
  return { version: VERSION, cycles: store.cycles, tracks };
}

/** Write `store`'s tracks under `puzzleId`, or drop the record when nothing
 *  has been driven -- so clearing a sequence back to the puzzle's own starting
 *  levels is remembered as "nothing driven" rather than as a saved run of
 *  zeroes. */
export function saveStimulus(puzzleId: string, store: SimStore): void {
  try {
    if (!store.hasStimulus()) {
      localStorage.removeItem(storageKey(puzzleId));
      return;
    }
    localStorage.setItem(storageKey(puzzleId), JSON.stringify(snapshotStimulus(store)));
  } catch (err) {
    // Storage full or disabled: the sequence still works this session, it is
    // just not there next time. Same call every other store here makes.
    console.warn("gdsx: could not save the sequence", err);
  }
}

/**
 * Load `puzzleId`'s saved stimulus into `store`. Returns true if one was
 * applied.
 *
 * Applied with `autoRun` suspended and restored afterwards, so a design is
 * simulated once at the end rather than once per track -- and so a player who
 * had auto-run switched off does not get it switched back on by a reload.
 */
export function restoreStimulus(puzzleId: string, store: SimStore): boolean {
  let raw: string | null;
  try {
    raw = localStorage.getItem(storageKey(puzzleId));
  } catch {
    return false;
  }
  if (!raw) return false;

  let saved: Saved;
  try {
    saved = JSON.parse(raw) as Saved;
  } catch (err) {
    console.warn("gdsx: discarding an unreadable saved sequence", err);
    try {
      localStorage.removeItem(storageKey(puzzleId));
    } catch {
      // Storage disabled -- nothing to clean up.
    }
    return false;
  }
  if (saved.version !== VERSION || !saved.tracks || typeof saved.tracks !== "object") return false;

  const wasAuto = store.autoRun;
  store.setAutoRun(false);
  try {
    // Length first: `setPattern` writes into the track it finds, so growing
    // the run afterwards would truncate everything restored above the old
    // length. A saved cycle count the design cannot honour is clamped by
    // `setCycles` itself.
    if (Number.isFinite(saved.cycles) && saved.cycles > 0 && saved.cycles !== store.cycles) {
      store.setCycles(Math.max(1, Math.round(saved.cycles)));
    }
    let applied = false;
    for (const port of store.inputPorts) {
      const bits = saved.tracks[port];
      // A port the saved record does not name is left at the puzzle's own
      // starting level rather than zeroed: the design may have been re-baked
      // with a port this record predates.
      if (typeof bits !== "string") continue;
      store.setPattern(port, bits);
      applied = true;
    }
    return applied;
  } finally {
    store.setAutoRun(wasAuto);
  }
}

/**
 * Keep `puzzleId`'s saved stimulus in step with `store`, and return the
 * unsubscribe.
 *
 * Debounced rather than written on every notification: `SimStore` notifies
 * once per edit, and painting a run of cells is one edit per cell.
 */
export function persistStimulus(puzzleId: string, store: SimStore): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsubscribe = store.subscribe(() => {
    clearTimeout(timer);
    timer = setTimeout(() => saveStimulus(puzzleId, store), SAVE_DEBOUNCE_MS);
  });
  return () => {
    clearTimeout(timer);
    unsubscribe();
  };
}