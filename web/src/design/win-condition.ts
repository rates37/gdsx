// The win condition, as flop values: "which flops must hold which value for
// this puzzle's success net to go high", derived once and shared by every
// panel that wants it.
//
// This exists because the derivation was previously done by hand, in the
// player's head, across two panels: the Cone Walker's flatten drawer knows
// the names, the Experiments watch box takes a comma-separated list, and
// nothing carried one to the other. It is deliberately *not* a panel helper --
// a panel importing another panel to borrow its analysis is how two
// almost-identical copies of a derivation get created.
//
// What the derivation is, in the same three steps a player performs:
//
//  1. **Step through the success flop.** `success` is a flop's Q net, so
//     `requirements(success, 1)` says only "`dfrtp_2_83.Q == 1`" -- true, and
//     useless. The question worth asking is what has to hold one cycle
//     earlier, on that flop's D net. Traversal stops at flops, so this is the
//     one step across a clock edge and there is never a second.
//  2. **Flatten that D net.** Forced leaves are the answer directly.
//  3. **Follow the one live option of each choice.** A latch's D net is
//     `set | Q`, so the flatten comes back as an OR: one option is the
//     feedback path ("it stays high because it was already high"), which
//     answers a different question, and the rest describe how it *becomes*
//     high. Dropping the self-hold leaves exactly one option on a design
//     whose lock has a single way in -- and where it does not, this reports
//     nothing rather than picking one, because "any of these" is not a win
//     condition.
//
// Anything that cannot be pinned down that way comes back `null`, and a
// caller offering this as an option must then not offer it at all. A puzzle
// with no lock (`driver.successNet === null`) is the same answer without a
// round trip.

import { DesignError, type DesignClient } from "./client";
import type { LeafValue } from "../gdsx-types";

/** How deep the choice-following recursion may go. The original puzzle needs
 *  one level; the cap is here so a design that fans out forever fails as a
 *  missing option rather than as a hung panel. */
const MAX_DEPTH = 6;

export interface WinCondition {
  /** The net this is the win condition for, e.g. `success`. */
  net: string;
  /** Flop instance name (no `.Q`) -> the value its Q must hold. */
  flops: ReadonlyMap<string, number>;
  /** Flop instance names in the order the derivation found them, which is the
   *  order `requirements` returns leaves in: sorted by label. */
  names: readonly string[];
  /** The `gdsx.api` calls that produced it, in order, for the `{ }` button. */
  call: string;
}

/**
 * A memoised handle on the current puzzle's win condition.
 *
 * `get()` is safe to call on panel mount: the work is a handful of
 * `requirements` round trips (~250 ms on the original puzzle, the largest
 * design in the pack), it is shared between every caller, and it never
 * re-runs. It does await the design handle, so a caller on the gate-tape
 * loading tier must not block its own first result on it.
 */
export interface WinConditionSource {
  get(): Promise<WinCondition | null>;
}

export function winConditionSource(
  designReady: Promise<DesignClient>,
  /** The puzzle's success net; `null` for a puzzle that declares no lock. */
  successNet: string | null,
): WinConditionSource {
  let pending: Promise<WinCondition | null> | null = null;
  return {
    get(): Promise<WinCondition | null> {
      if (successNet === null) return Promise.resolve(null);
      pending ??= designReady
        .then((design) => derive(design, successNet))
        .catch((err) => {
          // A design that will not answer is a missing option, not a broken
          // panel: every caller here is offering an extra, and none of them
          // has anything to say about a Python failure.
          console.warn("gdsx: could not derive the win condition", err);
          return null;
        });
      return pending;
    },
  };
}

async function derive(design: DesignClient, successNet: string): Promise<WinCondition | null> {
  const calls: string[] = [];
  const flops = new Map<string, number>();
  const names: string[] = [];
  const seen = new Set<string>();
  // The two names the origin can wear in its own feedback path: the net, and
  // the Q of the flop driving it.
  const selfHold = new Set<string>([successNet]);
  let undecided = false;

  let root = successNet;
  try {
    const { data, call } = await design.flopDNet(successNet);
    if (data.net !== null) {
      calls.push(call);
      root = data.net;
      selfHold.add(`${data.instance}.Q`);
    }
  } catch (err) {
    // Not a flop's Q (a puzzle whose lock is combinational): there is no edge
    // to step across, so the net justifies itself directly.
    if (!(err instanceof DesignError) || err.code !== "not_a_flop") throw err;
  }

  function take(leaf: LeafValue): void {
    if (!leaf.net.endsWith(".Q")) return; // a port or a constant: not a flop
    const instance = leaf.net.slice(0, -2);
    if (!flops.has(instance)) names.push(instance);
    flops.set(instance, leaf.value);
  }

  async function expand(net: string, value: number, depth: number): Promise<void> {
    const key = `${net}=${value}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (depth > MAX_DEPTH) {
      undecided = true;
      return;
    }
    const { data, call } = await design.requirements(net, value === 0 ? 0 : 1);
    calls.push(call);
    if (!data.consistent) {
      undecided = true;
      return;
    }
    for (const leaf of data.leaves) take(leaf);
    for (const choice of data.choices) {
      const live = choice.options.filter(
        (option) => !option.literals.some((l) => selfHold.has(l.net) && l.value === 1),
      );
      if (live.length !== 1) {
        // Either every way in is a self-hold, or there are several genuinely
        // different ways in. Neither is a set of flop values that must hold.
        undecided = true;
        continue;
      }
      for (const literal of live[0].literals) {
        if (literal.net.endsWith(".Q")) take(literal);
        else await expand(literal.net, literal.value, depth + 1);
      }
    }
  }

  await expand(root, 1, 0);

  // Undecided *and* empty is "this design does not have one"; undecided with
  // leaves found is a partial answer, which would be a watch set missing
  // columns nobody could see were missing. Both are no answer.
  if (undecided || flops.size === 0) return null;
  return { net: successNet, flops, names, call: calls.join("\n") };
}