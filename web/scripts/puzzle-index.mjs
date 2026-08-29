// Turns a baked puzzle directory into the descriptor the web app loads a
// level from. Used by sync-assets.mjs to write public/puzzles/index.json,
// and tested directly by scripts/test-puzzles.mjs.
//
// The one rule here, and the reason this is its own module rather than a few
// lines inside the copier: **solution.json never reaches the browser.** It
// holds the key bits, the answer value and the author's hint, and a player
// who opens devtools has then been handed the puzzle.
//
// Two things are derived from it. The *driver protocol* -- which port is the
// clock, what the reset pulse looks like, how long the window is, which net
// is the lock -- all of which the player can read off the design anyway. And
// the *checks* block: the minimum needed to say whether a submission is
// right, and no more (`describeChecks`, which documents the three shapes).
//
// Note the change in what that guarantee rests on. This module used to copy
// nothing but the driver, so a new spoiling field in solution.json could not
// leak by default. `describeChecks` deliberately *reads* `verify.predicate`
// and `answer` -- it has to, to pick a check kind and to hash -- and emits
// neither. That is a stronger claim needing a stronger check, so
// scripts/test-puzzles.mjs asserts directly that the emitted block contains
// no plaintext answer for any baked puzzle, rather than inferring it from
// what was copied.

import { createHash } from "node:crypto";

import { answerDigestInput, normaliseAnswer } from "../src/puzzles/answer-normalise.mjs";

/** Cycles of slack after the puzzle's own window, so a player can see what
 *  the design does once the deadline has passed rather than the waveform
 *  stopping exactly on it. */
const TAIL_CYCLES = 20;

/** Every field of solution.json that would spoil the puzzle. Asserted
 *  against the emitted descriptor in the tests, so this list failing to
 *  keep up with the schema is a test failure and not a silent leak.
 *
 *  These names are also why no field of the `checks` block is called any of
 *  them: the assertion is a substring match on `"<field>"`, and a check
 *  named `answer` would trip it. */
export const SPOILERS = ["key", "answer", "hint", "reveal", "verify"];

function trackPorts(solution) {
  const driver = solution.driver ?? {};
  const key = solution.key ?? {};
  if (Array.isArray(key.tracks)) {
    return key.tracks.flatMap((t) => (t.bus ? [...t.bus] : [t.port]));
  }
  const single = driver.input_port ?? key.port;
  return single ? [single] : [];
}

function windowCycles(solution) {
  const driver = solution.driver ?? {};
  if (typeof driver.input_cycles === "number") return driver.input_cycles;
  const tracks = solution.key?.tracks;
  if (Array.isArray(tracks) && tracks.length > 0) {
    return Math.max(...tracks.map((t) => (t.bits ? t.bits.length : (t.values?.length ?? 0))));
  }
  return driver.run_cycles ?? 0;
}

/** The net a puzzle is ultimately about: the lock for the kinds that have
 *  one, the port the observation is conditioned on for `constant`. A
 *  `parameter` puzzle has neither and returns null -- panels that need a
 *  success net are the ones a `parameter` puzzle does not enable. */
function successNet(solution) {
  const predicate = solution.verify?.predicate;
  if (!predicate) return null;
  return predicate.port ?? predicate.when?.port ?? null;
}

/**
 * The spoiler-free driver protocol, in the shape main.ts hands to SimStore.
 *
 * `resetVector` is the one-time step before cycle 0: every input at its
 * quiescent level, then the reset protocol's own values on top. SimStore
 * models a single such step, so a puzzle whose protocol runs for more than
 * one cycle is reported (`resetCycles`) rather than silently truncated.
 */
function describeDriver(solution) {
  const driver = solution.driver ?? {};
  const clockPort = driver.clock?.port ?? "clk";
  const reset = driver.reset ?? {};
  const statics = driver.static_inputs ?? {};
  const ports = trackPorts(solution);

  const quiescent = { [clockPort]: 0, ...statics };
  for (const port of ports) quiescent[port] = 0;

  const protocol = reset.protocol ?? [];
  const resetVector = { ...quiescent };
  for (const step of protocol) Object.assign(resetVector, step.values ?? {});

  const initialLevels = { ...statics };
  if (reset.port) initialLevels[reset.port] = reset.active_low ? 1 : 0;

  const window = windowCycles(solution);
  const deadline = solution.verify?.predicate?.by_cycle ?? window;

  return {
    clockPort,
    resetPort: reset.port ?? null,
    resetVector,
    resetCycles: protocol.reduce((n, step) => n + (step.cycles ?? 1), 0),
    initialLevels,
    cycles: Math.max(window, deadline) + TAIL_CYCLES,
    // The track the sequence editor opens on, and the one the write-up
    // prints as the key. A puzzle with no data input has none; main.ts
    // falls back to the design's first primary input.
    keyPort: ports[0] ?? null,
    trackPorts: ports,
    successNet: successNet(solution),
  };
}

function sha256(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function digest(id, fields) {
  return {
    kind: "digest",
    fields: fields.map((f) => ({ name: f.name, hash: sha256(answerDigestInput(id, f.name, f.value)) })),
  };
}

/**
 * What the app needs to decide whether a submission is right -- and nothing
 * beyond that. Null when the puzzle declares no verifiable answer; the
 * caller reports "this puzzle cannot be checked" rather than crashing.
 *
 * Three shapes, one per way an answer can be established:
 *
 *   {kind: "latch", net, value, byCycle, sticky}
 *     `sequence`. Needs nothing the descriptor did not already carry: the
 *     player drives the key and the app checks the lock latches. `net` is
 *     the same net as `driver.successNet`.
 *
 *   {kind: "bus-at", bus, when: {net, value}}
 *     `constant`, when the predicate's compared value IS the answer. The
 *     PREDICATE SHAPE only -- the bus nets and the port the observation is
 *     conditioned on, never the value. The app simulates the shipped driver,
 *     reads the bus at the conditioning cycle and compares that against what
 *     the player typed, so the answer never enters the bundle at all. `bus`
 *     is most-significant bit first, as authored.
 *
 *   {kind: "digest", fields: [{name, hash}]}
 *     Everything that cannot be established by simulating. `parameter`
 *     always: puzzles/2-polynomial's two fields are both `check.type:
 *     "declared"` -- a tap mask is a property of the wiring and carried on
 *     no bus, and 10**12 cycles will not be simulated -- so there is no
 *     predicate to derive a shape from. And `constant` when the predicate
 *     turns out to compare something other than the answer (see below).
 *
 * The hash is not an anti-cheat measure. game-plan.md §1 is explicit that
 * anti-cheat is a non-goal with "zero engineering spent on this", and a
 * SHA-256 of a 32-bit number falls to a few seconds of brute force. It buys
 * exactly one thing: the answer is not sitting in plain sight in a file the
 * player can open in a browser tab, for the cost of one digest call.
 */
function describeChecks(id, solution) {
  const kind = solution.answer_kind;
  const predicate = solution.verify?.predicate;
  const answer = solution.answer;

  if (kind === "sequence") {
    if (predicate?.type !== "port_reaches_value_by_cycle") return null;
    return {
      kind: "latch",
      net: predicate.port,
      value: predicate.value ?? 1,
      byCycle: predicate.by_cycle ?? null,
      sticky: predicate.sticky ?? false,
    };
  }

  if (kind === "constant") {
    // Whether the predicate can verify the submission at all is decided
    // here, by asking whether the value it compares IS the answer. It is not
    // always: puzzles/5-magic-number's answer is the 32-bit constant buried
    // in the comparator, while its predicate reads O[7:0] on the
    // confirmation run, one shift past the match. Shipping that shape would
    // have the app compare 0x0c against what the player typed and reject the
    // right answer, so that puzzle falls back to a digest.
    //
    // A puzzle with no authored answer value cannot be told apart from
    // either case, so it gets no check rather than a guess.
    if (answer?.value === undefined) return null;
    const verifiable =
      predicate?.type === "bus_equals_when" &&
      Array.isArray(predicate.bus) &&
      normaliseAnswer(predicate.value) === normaliseAnswer(answer.value);
    if (!verifiable) return digest(id, [{ name: "value", value: answer.value }]);
    // No `width` field, though the submission widget wants a bit count and
    // `answer.width` is right there. puzzles/1-warm-start's answer is 8 and
    // its bus is 8 bits wide, so a width copied from the answer would put
    // that puzzle's answer in the bundle by coincidence -- the test caught
    // exactly this. A widget uses `bus.length`: the same number, not sourced
    // from the answer.
    return {
      kind: "bus-at",
      bus: [...predicate.bus],
      when: { net: predicate.when?.port ?? null, value: predicate.when?.value ?? 1 },
    };
  }

  if (kind === "parameter") {
    const fields = answer?.fields;
    if (!Array.isArray(fields) || fields.length === 0) return null;
    return digest(id, fields);
  }

  return null;
}

/** hints.json's tiers, carried onto the descriptor unchanged -- an authored
 *  hint is not a spoiler (game-plan.md §8: hints are always available, they
 *  just cost), so unlike solution.json this file's content is meant to reach
 *  the player and needs no filtering. */
function describeHints(hints) {
  const tiers = hints.tiers;
  if (!Array.isArray(tiers)) return [];
  return tiers.map((t) => ({ tier: t.tier, text: t.text }));
}

function parMinutes(manifest) {
  if (typeof manifest.par_times?.minutes === "number") return manifest.par_times.minutes;
  if (typeof manifest.par_seconds === "number") return Math.round(manifest.par_seconds / 60);
  return null;
}

/**
 * One entry of public/puzzles/index.json.
 *
 * @param dir      the puzzle directory's name, which is also its URL segment
 * @param manifest parsed manifest.json
 * @param solution parsed solution.json -- read here and never copied
 * @param hints    parsed hints.json. Defaults to no tiers, so call sites that
 *                 predate this parameter (and the inline test fixture in
 *                 test-puzzles.mjs) still work.
 */
export function describe(dir, manifest, solution, hints = {}) {
  const id = manifest.id ?? dir;
  return {
    id,
    dir,
    title: manifest.title ?? dir,
    blurb: manifest.blurb ?? "",
    difficulty: manifest.difficulty ?? null,
    parMinutes: parMinutes(manifest),
    answerKind: solution.answer_kind ?? manifest.answer_kind ?? null,
    toolsEnabled: manifest.tools_enabled ?? [],
    assets: {
      netlist: `/puzzles/${dir}/netlist.json`,
      render: `/puzzles/${dir}/render.bin`,
      tape: `/puzzles/${dir}/tape.bin`,
    },
    driver: describeDriver(solution),
    checks: describeChecks(id, solution),
    hints: describeHints(hints),
  };
}

/** The files a puzzle directory must have before it can be offered as a
 *  level. A directory that is authored but not yet baked is skipped rather
 *  than shipped half-loadable. */
export const REQUIRED = [
  "manifest.json",
  "solution.json",
  "netlist.json",
  "render.bin",
  "tape.bin",
  "hints.json",
];