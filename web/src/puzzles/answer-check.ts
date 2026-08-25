// Whether a submission is right, decided in the browser from the
// descriptor's `checks` block (see web/scripts/puzzle-index.mjs for how that
// block is derived and why it carries no answer).
//
// This is the verification half only. Choosing a widget per `answerKind`,
// showing the verdict and scoring it are the submission surface's job; this
// file is what that surface calls, so the normalisation used to hash a
// player's answer is the same code that hashed the author's at bake time
// rather than a second implementation free to drift from it.
//
// Rejections carry `observed`: what the design actually did. This audience
// wants the measurement, not a buzzer -- "O read 0x2a at cycle 519" is a
// lead, "wrong" is not.

import { answerDigestInput, normaliseAnswer } from "./answer-normalise.mjs";
import type { PuzzleChecks, PuzzleDescriptor } from "./catalog.ts";
import { busValueAt, type SimStore } from "../sim/store.ts";

export interface Verdict {
  accepted: boolean;
  /** What the design did, for a rejection to be informative. Absent when
   *  nothing was measured -- a digest check observes nothing by design. */
  observed?: string;
  reason: string;
}

/**
 * What the player handed in. One loose shape rather than a union because a
 * single submission panel fills whichever field its widget owns:
 *
 *   sequence  -- `tracks`, port -> bit string, driven into the store before
 *                the lock is checked;
 *   constant  -- `value`, in any radix;
 *   parameter -- `fields`, field name -> value.
 *
 * A `digest` check accepts either `fields` or, for the single-field
 * `constant` case, `value`.
 */
export interface Submission {
  tracks?: Record<string, string>;
  value?: string | number;
  fields?: Record<string, string | number>;
}

/** Lowercase hex SHA-256 of `<puzzle>:<field>:<canonical value>` -- the same
 *  bytes node:crypto hashed at bake time. */
export async function hashAnswer(
  puzzleId: string,
  fieldName: string,
  value: unknown,
): Promise<string> {
  const bytes = new TextEncoder().encode(answerDigestInput(puzzleId, fieldName, value));
  const out = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...out].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function hex(value: number, width: number): string {
  return `0x${(value >>> 0).toString(16).padStart(Math.ceil(width / 4), "0")}`;
}

/**
 * The first cycle `net` reads `value`, or null.
 *
 * The sticky case delegates to the store's own `firstLatchedHigh` rather
 * than re-deciding what "latched" means: the waveform reports the latch
 * cycle from that method, and a verdict that disagrees with the trace the
 * player is looking at is worse than no verdict.
 */
function firstCycle(store: SimStore, net: string, value: number, sticky: boolean): number | null {
  if (sticky && value === 1) return store.firstLatchedHigh(net);
  for (let c = 0; c < store.cycles; c++) {
    if (store.netValueAt(c, net) !== value) continue;
    if (!sticky) return c;
    let holds = true;
    for (let k = c; k < store.cycles; k++) {
      if (store.netValueAt(k, net) !== value) {
        holds = false;
        break;
      }
    }
    if (holds) return c;
  }
  return null;
}

function checkLatch(checks: Extract<PuzzleChecks, { kind: "latch" }>, store: SimStore): Verdict {
  const at = firstCycle(store, checks.net, checks.value, checks.sticky);
  const held = checks.sticky ? "latches" : "reads";
  if (at === null) {
    return {
      accepted: false,
      observed: `${checks.net} never ${held} ${checks.value} in ${store.cycles} cycles`,
      reason: "the lock never opened",
    };
  }
  if (checks.byCycle !== null && at > checks.byCycle) {
    return {
      accepted: false,
      observed: `${checks.net} ${held} ${checks.value} at cycle ${at}`,
      reason: `too late — the deadline is cycle ${checks.byCycle}`,
    };
  }
  return {
    accepted: true,
    observed: `${checks.net} ${held} ${checks.value} at cycle ${at}`,
    reason: "accepted",
  };
}

function checkBusAt(
  checks: Extract<PuzzleChecks, { kind: "bus-at" }>,
  submission: Submission,
  store: SimStore,
): Verdict {
  const { net, value } = checks.when;
  if (!net) return { accepted: false, reason: "this puzzle names no conditioning port" };

  // Not `firstCycle(..., sticky)`: the authored predicate conditions on the
  // port reaching a value, and says nothing about it holding.
  const at = firstCycle(store, net, value, false);
  if (at === null) {
    return {
      accepted: false,
      observed: `${net} never reads ${value} in ${store.cycles} cycles`,
      reason: "the conditioning port never fired, so there is nothing to read",
    };
  }

  // `bus` is authored most-significant first; busValueAt wants bit0 first.
  const observed = busValueAt(store, at, [...checks.bus].reverse());
  const shown = `${hex(observed, checks.bus.length)} (${observed}) at cycle ${at}`;
  if (normaliseAnswer(observed) !== normaliseAnswer(submission.value)) {
    return { accepted: false, observed: shown, reason: "the design reads a different value" };
  }
  return { accepted: true, observed: shown, reason: "accepted" };
}

async function checkDigest(
  checks: Extract<PuzzleChecks, { kind: "digest" }>,
  submission: Submission,
  puzzleId: string,
): Promise<Verdict> {
  const wrong: string[] = [];
  for (const field of checks.fields) {
    const submitted = submission.fields?.[field.name] ?? (checks.fields.length === 1 ? submission.value : undefined);
    if (submitted === undefined || String(submitted).trim() === "") {
      wrong.push(`${field.name} (nothing submitted)`);
      continue;
    }
    if ((await hashAnswer(puzzleId, field.name, submitted)) !== field.hash) wrong.push(field.name);
  }
  if (wrong.length > 0) {
    return {
      accepted: false,
      // Nothing was measured: this answer is not carried on any bus and is
      // not reachable by simulating, which is why it is a digest at all.
      reason: `not the recorded value for ${wrong.join(", ")}`,
    };
  }
  return { accepted: true, reason: "accepted" };
}

/**
 * Verify `submission` against `descriptor.checks`.
 *
 * For a `sequence` puzzle the submission's tracks are driven into `store`
 * first, so the design is actually run with what the player handed in rather
 * than with whatever the sequence editor happened to be showing. Every other
 * kind reads the store as the shipped driver already ran it.
 */
export async function verifySubmission(
  descriptor: PuzzleDescriptor,
  submission: Submission,
  store?: SimStore,
): Promise<Verdict> {
  const checks = descriptor.checks;
  if (!checks) {
    return { accepted: false, reason: "this puzzle declares no answer that can be checked" };
  }

  // A digest observes nothing, so it needs no running design -- which is the
  // whole reason it is a digest.
  if (checks.kind === "digest") return checkDigest(checks, submission, descriptor.id);
  if (!store) return { accepted: false, reason: "this answer needs a running simulation to check" };

  if (submission.tracks) {
    for (const [port, bits] of Object.entries(submission.tracks)) store.setPattern(port, bits);
    if (store.isDirty()) store.recompute();
  }

  return checks.kind === "latch" ? checkLatch(checks, store) : checkBusAt(checks, submission, store);
}