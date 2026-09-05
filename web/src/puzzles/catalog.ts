// Which puzzle the page is playing, and where its assets live.
//
// The catalog is `public/puzzles/index.json`, written at asset-sync time by
// scripts/puzzle-index.mjs from each baked puzzle's manifest and driver
// protocol. Nothing here knows what a level *is* beyond "a render bundle, a
// gate tape, a netlist and how to clock it" -- adding a puzzle is baking it
// and re-running the sync, not editing this file.
//
// Selection is in the URL (`?puzzle=<id>`), so a level is linkable and the
// back button moves between levels. The last choice is also remembered -- not
// to reopen it by itself (see `chooseRoute`), but so the menu can offer it as
// Continue.

import { assetUrl } from "../asset-url.ts";

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

/** One tier of `gdsx puzzle bake`'s auto-generated hints.json, carried onto
 *  the descriptor verbatim -- see puzzle-index.mjs's `describeHints`. Unlike
 *  `checks`, this is not derived from anything that needs filtering: an
 *  authored hint is meant to reach the player. */
export interface HintTier {
  tier: number;
  text: string;
}

export interface PuzzleDescriptor {
  id: string;
  dir: string;
  title: string;
  blurb: string;
  /** `easy` | `medium` | `hard`, or null when the manifest declares none.
   *  Validated at sync time by web/scripts/puzzle-index.mjs, which refuses
   *  anything else, so this is safe to show verbatim. */
  difficulty: string | null;
  /** Where the design came from, one entry per paragraph: authored prose,
   *  shown on the briefing card and nowhere else. Empty for a manifest that
   *  declares none, which is a card with no background section rather than an
   *  error. */
  backstory: string[];
  parMinutes: number | null;
  answerKind: string | null;
  toolsEnabled: string[];
  assets: { netlist: string; render: string; tape: string };
  driver: PuzzleDriver;
  /** How to tell whether a submission is right. Null for a puzzle that
   *  declares no verifiable answer -- the app says so rather than pretending
   *  a submission was rejected. */
  checks: PuzzleChecks | null;
  /** In order, tier 0 first. Empty for a puzzle baked before hints.json
   *  existed. Revealing one is the toolbar's job (workspace/toolbar.ts's
   *  `HintsControl`); this is just the content. */
  hints: HintTier[];
}

const INDEX_URL = assetUrl("puzzles/index.json");
const LAST_PLAYED_KEY = "gdsx.puzzle.last.v1";
const PARAM = "puzzle";

interface CatalogFile {
  schema_version?: number;
  puzzles?: PuzzleDescriptor[];
}

/**
 * The catalog, or an empty one if it cannot be read. Never rejects: a missing
 * catalog is an empty menu that says so, not a dead page.
 *
 * **There is no built-in puzzle to fall back to, deliberately.** This module
 * used to carry a complete hand-written descriptor for the original puzzle --
 * title, blurb, difficulty, par, driver, checks and the four real hint tiers
 * -- as an offline shell for `npm run dev` before `sync-assets` had run. Two
 * things were wrong with it. `predev` runs `sync-assets`, so the scenario it
 * existed for could not happen; and a second copy of a puzzle's authored data
 * drifts, which it had -- its blurb was a paraphrase of the manifest's and
 * would have gone on diverging every time a level was re-baked.
 *
 * Every fact about a puzzle now has exactly one source: its `manifest.json`
 * and `solution.json`, through `scripts/puzzle-index.mjs` into
 * `public/puzzles/index.json`. Nothing in `src/` restates any of it.
 */
export async function loadCatalog(fetchImpl: typeof fetch = fetch): Promise<PuzzleDescriptor[]> {
  try {
    const response = await fetchImpl(INDEX_URL);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const data = (await response.json()) as CatalogFile;
    if (!data.puzzles?.length) throw new Error("catalog lists no puzzles");
    return data.puzzles;
  } catch (err) {
    console.warn(`gdsx: no puzzle catalog at ${INDEX_URL} — run \`npm run sync-assets\``, err);
    return [];
  }
}

/** The catalog entry with this id, matching a puzzle's directory name as well
 *  as its id -- both spellings appear in saved links. */
export function findPuzzle(
  catalog: PuzzleDescriptor[],
  id: string | null | undefined,
): PuzzleDescriptor | undefined {
  return id ? catalog.find((p) => p.id === id || p.dir === id) : undefined;
}

/** Which of the app's two screens a URL asks for. */
export type Route = { kind: "menu" } | { kind: "puzzle"; puzzle: PuzzleDescriptor };

/**
 * The routing rule, entire: `?puzzle=<id>` opens that puzzle's workspace, and
 * anything else opens the menu.
 *
 * The menu is what a bare URL gets, which is a change -- it used to reopen
 * the last-played puzzle. That convenience moves to the menu's Continue entry
 * rather than disappearing: a player mid-solve is one click from where they
 * were, and a player who has never played is no longer dropped into a
 * 728-instance design with no idea what the other six are.
 *
 * An id the catalog does not have also opens the menu. It used to fall
 * through to the last-played puzzle and then to `catalog[0]`, which meant a
 * stale bookmark silently opened a *different* level and nothing said so; the
 * menu at least shows which levels exist.
 */
export function chooseRoute(
  catalog: PuzzleDescriptor[],
  opts: { requested?: string | null } = {},
): Route {
  const puzzle = findPuzzle(catalog, opts.requested);
  return puzzle ? { kind: "puzzle", puzzle } : { kind: "menu" };
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

/** The URL that opens the level menu: this one with the puzzle selection
 *  dropped, so `?debug=1` and anything else a player is carrying survives the
 *  trip back. The inverse of `urlFor`, and the toolbar's way out. */
export function menuUrl(href: string = location.href): string {
  const url = new URL(href);
  url.searchParams.delete(PARAM);
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
 * bundle and the tape without waiting for Python.
 */
export function openPuzzle(id: string): void {
  rememberPuzzle(id);
  location.assign(urlFor(id));
}