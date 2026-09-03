// What a level menu card says, derived from the catalog and the progress
// store. No DOM: menu.ts renders these, scripts/test-menu.mjs checks them.
//
// Everything here comes from a PuzzleDescriptor -- the catalog is written by
// scripts/puzzle-index.mjs from each baked puzzle's manifest, so a new level
// appears on the menu by being baked and synced, with nothing to edit here.
// Progress arrives as records rather than being read from localStorage
// directly, so the derivation is testable without a browser.

import type { PuzzleDescriptor } from "../puzzles/catalog.ts";
import { goalFor } from "../puzzles/goals.ts";
import { solvedDay, type ProgressRecord } from "../store/progress.ts";

export interface MenuEntry {
  id: string;
  title: string;
  blurb: string;
  /** `?puzzle=<id>`, relative -- the card is a real link, so the routing rule
   *  and the click target are the same thing. */
  href: string;
  /** The difficulty band the manifest declares — `easy`, `medium` or `hard` —
   *  or null when it declares none. Shown verbatim: the vocabulary is fixed
   *  and validated by web/scripts/puzzle-index.mjs at sync time, so there is
   *  nothing for this screen to format or decide. */
  difficulty: string | null;
  /** "par 20m", or null. */
  par: string | null;
  /** The answer kind said in the imperative, shared with the toolbar's
   *  objective. Null for a puzzle declaring an answer kind we have no phrase
   *  for -- the card then says nothing rather than guessing. */
  goal: string | null;
  /** True once a submission for this puzzle has been accepted. */
  solved: boolean;
  /** The first solve, `YYYY-MM-DD`, or null. Deliberately not localised: it
   *  is a fact in a card, not a timestamp a player reads closely. */
  solvedOn: string | null;
  /** Every submit attempt, accepted or not. 0 for an untouched puzzle. */
  attempts: number;
  /** The best score this puzzle has been solved with, or
   *  null when there is none: unsolved, or solved before scoring existed. The
   *  menu shows the number and nothing else -- the breakdown lives in the
   *  write-up, which needs the design loaded. */
  score: number | null;
}


/**
 * One entry per catalog puzzle, in catalog order.
 *
 * Catalog order is the intended order and every puzzle has always been
 * reachable by URL, so nothing here locks, hides or gates a card on another
 * card's progress. A solved puzzle is marked, not removed.
 */
export function menuEntries(
  catalog: PuzzleDescriptor[],
  progress: ProgressRecord[] = [],
): MenuEntry[] {
  const byId = new Map(progress.map((record) => [record.puzzleId, record]));
  return catalog.map((puzzle) => {
    const record = byId.get(puzzle.id);
    return {
      id: puzzle.id,
      title: puzzle.title,
      blurb: puzzle.blurb,
      href: `?puzzle=${encodeURIComponent(puzzle.id)}`,
      difficulty: puzzle.difficulty,
      par: puzzle.parMinutes ? `par ${puzzle.parMinutes}m` : null,
      goal: goalFor(puzzle.answerKind),
      solved: Boolean(record?.solvedAt),
      solvedOn: solvedDay(record?.solvedAt),
      attempts: record?.attempts ?? 0,
      score: record?.bestScore ?? null,
    };
  });
}

/**
 * The entry the Continue banner offers, or null.
 *
 * A bare URL used to reopen the last-played puzzle by itself; now it opens
 * this screen, and this is what keeps that one click away. Null when there is
 * nothing to continue -- no last-played id, storage disabled, or a saved id
 * whose puzzle is no longer in the catalog. A Continue that leads nowhere is
 * worse than no Continue.
 */
export function continueEntry(entries: MenuEntry[], lastPlayed: string | null): MenuEntry | null {
  if (!lastPlayed) return null;
  return entries.find((entry) => entry.id === lastPlayed) ?? null;
}