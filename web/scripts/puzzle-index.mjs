// Turns a baked puzzle directory into the descriptor the web app loads a
// level from. Used by sync-assets.mjs to write public/puzzles/index.json,
// and tested directly by scripts/test-puzzles.mjs.
//
// The one rule here, and the reason this is its own module rather than a few
// lines inside the copier: **solution.json never reaches the browser.** It
// holds the key bits, the answer value and the author's hint, and a player
// who opens devtools has then been handed the puzzle. What the app actually
// needs from it is the *driver protocol* -- which port is the clock, what
// the reset pulse looks like, how long the window is, which net is the lock
// -- all of which the player can read off the design anyway. `describe()`
// derives exactly that and copies nothing else, so adding a field to
// solution.json can never leak it by default.

/** Cycles of slack after the puzzle's own window, so a player can see what
 *  the design does once the deadline has passed rather than the waveform
 *  stopping exactly on it. */
const TAIL_CYCLES = 20;

/** Every field of solution.json that would spoil the puzzle. Asserted
 *  against the emitted descriptor in the tests, so this list failing to
 *  keep up with the schema is a test failure and not a silent leak. */
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
 */
export function describe(dir, manifest, solution) {
  return {
    id: manifest.id ?? dir,
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
  };
}

/** The files a puzzle directory must have before it can be offered as a
 *  level. A directory that is authored but not yet baked is skipped rather
 *  than shipped half-loadable. */
export const REQUIRED = ["manifest.json", "solution.json", "netlist.json", "render.bin", "tape.bin"];