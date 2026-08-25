// Which puzzle the page is playing, and where its assets live.
//
// The catalog is `public/puzzles/index.json`, written at asset-sync time by
// scripts/puzzle-index.mjs from each baked puzzle's manifest and driver
// protocol. Nothing here knows what a level *is* beyond "a render bundle, a
// gate tape, a netlist and how to clock it" -- adding a puzzle is baking it
// and re-running the sync, not editing this file.
//
// Selection is in the URL (`?puzzle=<id>`), so a level is linkable and the
// back button moves between levels. The last choice is also remembered, so
// reopening the app lands where the player left off.

export interface PuzzleDriver {
  clockPort: string;
  resetPort: string | null;
  /** The one-time step before cycle 0. */
  resetVector: Record<string, number>;
  /** How many cycles the authored reset protocol asserts for. More than one
   *  means `resetVector` is only its first step -- SimStore models a single
   *  pre-cycle, and a puzzle needing more would need that lifted first. */
  resetCycles: number;
  initialLevels: Record<string, 0 | 1>;
  cycles: number;
  /** The track the sequence editor opens on. Null for a puzzle with no data
   *  input, where the caller substitutes the design's first primary input. */
  keyPort: string | null;
  trackPorts: string[];
  /** Null for a puzzle with no lock (`parameter`), which is exactly the kind
   *  that does not enable the panels needing one. */
  successNet: string | null;
}

/** `sequence`: drive the key, then check the lock latches. */
export interface LatchCheck {
  kind: "latch";
  net: string;
  value: number;
  /** The authored deadline, or null if the puzzle sets none. */
  byCycle: number | null;
  /** True when the net must stay high once high, rather than merely pulse. */
  sticky: boolean;
}

/**
 * `constant`, when the value the puzzle's predicate compares IS the answer.
 * The shape without the value: simulate the shipped driver, read `bus` at
 * the first cycle `when.net` reads `when.value`, compare that against the
 * submission. The answer is not in the bundle to be found.
 */
export interface BusAtCheck {
  kind: "bus-at";
  /** Bit nets, most-significant first. `busValueAt` wants the reverse. */
  bus: string[];
  when: { net: string | null; value: number };
  /** Never present. A `bus-at` check is the predicate's shape without its
   *  value, and the type says so. */
  value?: never;
  /** Never present either, and for a sharper reason: one puzzle's answer is
   *  8 and its bus is 8 bits wide, so a `width` copied from the authored
   *  answer would leak it by coincidence. A widget wanting a bit count uses
   *  `bus.length`. */
  width?: never;
}

/** Everything that cannot be established by simulating: `parameter` always,
 *  and `constant` whose predicate checks something other than the answer.
 *  See puzzle-index.mjs for why this is a hash and what it is not. */
export interface DigestCheck {
  kind: "digest";
  fields: { name: string; hash: string }[];
}

export type PuzzleChecks = LatchCheck | BusAtCheck | DigestCheck;

export interface PuzzleDescriptor {
  id: string;
  dir: string;
  title: string;
  blurb: string;
  difficulty: number | string | null;
  parMinutes: number | null;
  answerKind: string | null;
  toolsEnabled: string[];
  assets: { netlist: string; render: string; tape: string };
  driver: PuzzleDriver;
  /** How to tell whether a submission is right. Null for a puzzle that
   *  declares no verifiable answer -- the app says so rather than pretending
   *  a submission was rejected. */
  checks: PuzzleChecks | null;
}

const INDEX_URL = "/puzzles/index.json";
const LAST_PLAYED_KEY = "gdsx.puzzle.last.v1";
const PARAM = "puzzle";

/**
 * Two Stars on the /samples/ assets: what the app loaded before it could
 * load anything else, and what it falls back to if the catalog is missing.
 *
 * That happens for real -- `npm run dev` after a `git pull` that added this
 * file but before `sync-assets` has run -- and a shell that boots into the
 * puzzle it has always booted into is a much better failure than a blank
 * page. The values are the ones main.ts used to hold as constants.
 */
export const FALLBACK: PuzzleDescriptor = {
  id: "original-puzzle",
  dir: "original-puzzle",
  title: "Original Puzzle",
  blurb: "The 728-instance design. Find the sequence that raises success.",
  difficulty: "hard",
  parMinutes: 120,
  answerKind: "sequence",
  toolsEnabled: [],
  assets: {
    netlist: "/samples/puzzle.netlist.json",
    render: "/samples/puzzle.render.bin",
    tape: "/samples/puzzle.tape.bin",
  },
  driver: {
    clockPort: "clk",
    resetPort: "rst_n",
    resetVector: { clk: 0, rst_n: 0, enable: 1, I: 0 },
    resetCycles: 1,
    initialLevels: { enable: 1, rst_n: 1 },
    cycles: 141,
    keyPort: "I",
    trackPorts: ["I"],
    successNet: "success",
  },
  // Like every other field here, the original puzzle's real value. A `null`
  // would leave the offline shell unable to check an answer it is perfectly
  // capable of checking -- nothing in a latch check is a spoiler.
  checks: { kind: "latch", net: "success", value: 1, byCycle: 121, sticky: true },
};

interface CatalogFile {
  schema_version?: number;
  puzzles?: PuzzleDescriptor[];
}

/** The catalog, or the single fallback entry if it cannot be read. Never
 *  rejects: a missing catalog is a degraded shell, not a dead one. */
export async function loadCatalog(fetchImpl: typeof fetch = fetch): Promise<PuzzleDescriptor[]> {
  try {
    const response = await fetchImpl(INDEX_URL);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const data = (await response.json()) as CatalogFile;
    if (!data.puzzles?.length) throw new Error("catalog lists no puzzles");
    return data.puzzles;
  } catch (err) {
    console.warn(`gdsx: no puzzle catalog at ${INDEX_URL}, falling back to ${FALLBACK.id}`, err);
    return [FALLBACK];
  }
}

/**
 * The puzzle to load: the one named in `?puzzle=`, else the one last played,
 * else the first in the catalog.
 *
 * An id that is not in the catalog falls through to the same chain rather
 * than erroring -- a stale bookmark or a renamed puzzle directory should
 * open *something*, and the picker then shows what it actually opened.
 */
export function choosePuzzle(
  catalog: PuzzleDescriptor[],
  opts: { requested?: string | null; lastPlayed?: string | null } = {},
): PuzzleDescriptor {
  const find = (id: string | null | undefined): PuzzleDescriptor | undefined =>
    id ? catalog.find((p) => p.id === id || p.dir === id) : undefined;
  return find(opts.requested) ?? find(opts.lastPlayed) ?? catalog[0];
}

export function requestedId(search: string = location.search): string | null {
  return new URLSearchParams(search).get(PARAM);
}

export function lastPlayedId(): string | null {
  try {
    return localStorage.getItem(LAST_PLAYED_KEY);
  } catch {
    return null;
  }
}

export function rememberPuzzle(id: string): void {
  try {
    localStorage.setItem(LAST_PLAYED_KEY, id);
  } catch {
    // Storage disabled: the URL still carries the choice, so only the
    // "reopen where I left off" convenience is lost.
  }
}

/** The URL that opens `id`, preserving everything else in the query. */
export function urlFor(id: string, href: string = location.href): string {
  const url = new URL(href);
  url.searchParams.set(PARAM, id);
  return url.toString();
}

/**
 * Switch levels by navigating.
 *
 * A reload rather than an in-place swap, deliberately: every panel holds
 * state derived from one design -- GPU buffers in the two die views, the
 * sim store's cycle history, sweep workers mid-flight, design handles in the
 * Pyodide worker -- and a fresh document is a guaranteed-clean teardown of
 * all of it. The cost is one Pyodide boot, which the loading tiers already
 * cover: the die view, waveform and sequence editor come up off the render
 * bundle and the tape without waiting for Python (game-plan.md §9).
 */
export function openPuzzle(id: string): void {
  rememberPuzzle(id);
  location.assign(urlFor(id));
}