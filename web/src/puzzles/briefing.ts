// What the briefing card says, derived from the catalog descriptor. No DOM:
// workspace/briefing.ts renders these, scripts/test-briefing.mjs checks them.
//
// Same split as menu/entries.ts, and for the same reason -- the sentences
// here are the ones that would otherwise be assembled inline in a render
// function where nothing outside a browser could ever read them back.
//
// Two rules this file follows and future edits must keep:
//
//  1. **It states the objective, never the answer.** `checks` carries enough
//     to check a submission, and for a `digest` puzzle that is a hash, but
//     the temptation is the other two: a `latch` check names the lock net and
//     the deadline (fair -- that is the goal, and the submit widget already
//     shows both), while a `bus-at` check is deliberately the predicate
//     *without* its value (catalog.ts) and must stay that way here. Nothing
//     below reads `checks.value` for a bus-at, because it does not exist.
//  2. **It restates nothing.** Everything comes from the descriptor, which
//     comes from the manifest -- so a re-baked level's briefing is current
//     with no edit here. The one authored string in this file is the
//     fallback for a puzzle with no checkable answer.

import type { PuzzleDescriptor } from "./catalog.ts";
import { goalFor } from "./goals.ts";

export interface Briefing {
  id: string;
  title: string;
  /** `easy` | `medium` | `hard`, or null. Shown verbatim. */
  difficulty: string | null;
  /** "par 35m", or null. Same spelling as the menu card's chip. */
  par: string | null;
  /** The answer kind in the imperative, shared with the menu and (until the
   *  briefing existed) the toolbar. Null when the puzzle declares an answer
   *  kind we have no phrase for. */
  goal: string | null;
  blurb: string;
  /** Where the design came from, one paragraph per entry. Empty for a level
   *  whose manifest authors none. */
  backstory: string[];
  /** One sentence naming what ends the puzzle, or null when there is no
   *  checkable answer. */
  winCondition: string | null;
  /** What the submit widget will ask for, or null for the same reason. */
  answerShape: string | null;
}

/** "success reads 1 by cycle 5, and stays there" and friends: the sentence a
 *  player should be able to read before touching anything. */
function winCondition(descriptor: PuzzleDescriptor): string | null {
  const checks = descriptor.checks;
  if (!checks) return null;
  if (checks.kind === "latch") {
    const deadline = checks.byCycle === null ? "" : ` by cycle ${checks.byCycle}`;
    const held = checks.sticky ? ", and stay there" : "";
    return `drive the inputs until ${checks.net} reads ${checks.value}${deadline}${held}`;
  }
  if (checks.kind === "bus-at") {
    // Never `checks.value`: a bus-at check is the predicate without its
    // value, on purpose. The width comes from the bus itself.
    const when = checks.when.net;
    const at = when === null ? "" : ` at the first cycle ${when} reads ${checks.when.value}`;
    return `read the ${checks.bus.length}-bit value on ${busLabel(checks.bus)}${at}`;
  }
  const names = checks.fields.map((f) => f.name);
  return `recover ${names.join(" and ")} from the design`;
}

/** `["O[7]", …, "O[0]"]` -> `"O"`. Falls back to the first bit's own name for
 *  a bus whose nets are not `name[i]` -- some designs bring a "bus" out as
 *  separately named nets, and half a name is worse than the real one. */
function busLabel(bus: string[]): string {
  const first = bus[0] ?? "the bus";
  const match = /^(.+)\[\d+\]$/.exec(first);
  return match ? match[1] : first;
}

/** What submit will ask for. Phrased as the widget behaves -- one field per
 *  input port for a sequence, one value for a constant, one per named
 *  parameter otherwise -- so opening it holds no surprise. */
function answerShape(descriptor: PuzzleDescriptor): string | null {
  const checks = descriptor.checks;
  if (!checks) return null;
  if (checks.kind === "latch") {
    const ports = descriptor.driver.trackPorts?.length ?? 0;
    if (ports === 0) return "a bit sequence for each input port";
    if (ports === 1) return "a bit sequence for the design's one input port";
    return `a bit sequence for each of ${ports} input ports`;
  }
  if (checks.kind === "bus-at") return "one value, in the radix of your choice";
  const n = checks.fields.length;
  return n === 1 ? "one named value" : `${n} named values`;
}

/**
 * The briefing for one level.
 *
 * Pure: everything is on the descriptor, so this needs no design handle, no
 * simulator and no network -- which is what lets the card be shown while
 * Python is still booting, and what lets the test check every shipped level
 * under plain Node.
 */
export function briefingFor(descriptor: PuzzleDescriptor): Briefing {
  return {
    id: descriptor.id,
    title: descriptor.title,
    difficulty: descriptor.difficulty,
    par: descriptor.parMinutes === null ? null : `par ${descriptor.parMinutes}m`,
    goal: goalFor(descriptor.answerKind),
    blurb: descriptor.blurb,
    backstory: descriptor.backstory ?? [],
    winCondition: winCondition(descriptor),
    answerShape: answerShape(descriptor),
  };
}

/**
 * True the first time a given level is opened, false ever after.
 *
 * Per puzzle, not once per player: the brief is about *this* level, and
 * someone arriving at level 4 has never read it.
 *
 * Calling this records the visit, so it answers true exactly once -- it is
 * "take", not "peek", and a caller must not use it to test the state.
 * Best-effort storage like every other preference: with storage unavailable
 * the briefing opens on each load, which is a worse habit but not a broken
 * screen.
 */
export function takeFirstVisit(puzzleId: string): boolean {
  // One key per level rather than one list of ids, so clearing a level's
  // progress (the menu's per-puzzle clear) re-arms its briefing along with
  // everything else -- a level reset to untouched should introduce itself
  // again. Spelled inline, in the `const ...key = \`gdsx...\`` shape every
  // other store uses: scripts/test-progress.mjs rediscovers the namespace by
  // scanning for exactly that, and asserts store/progress.ts has a rule
  // covering it.
  const key = `gdsx.briefing.${puzzleId}.v1`;
  try {
    if (localStorage.getItem(key) !== null) return false;
  } catch {
    return true; // unreadable storage: show it, and ask again next time
  }
  try {
    localStorage.setItem(key, new Date().toISOString());
  } catch {
    // Unstorable: the card still opens now, and will open again next load.
  }
  return true;
}
