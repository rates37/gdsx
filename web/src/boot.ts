// The workspace screen: the panel shell paints immediately; the die view
// comes up as soon as `render.bin` arrives, without waiting for Python.
// Pyodide boots afterwards in a worker, so the M0 timing spike (extract +
// analyse against the same page) still runs -- `globalThis.spike` exposes
// the measurements it reads, unchanged from the M0 shape so
// `scripts/measure-m0.mjs` keeps working.
//
// This module is reached only through main.ts's dynamic import, and only for
// a URL that names a puzzle. That is what keeps the level menu cheap: every
// expensive thing the app does -- Pyodide in a worker, the render bundle, the
// gate tape, dockview, three.js -- is imported from here, so none of it is in
// the menu's module graph at all.

import { wrap, type Remote } from "comlink";
import type { GdsxWorker, Envelope } from "./worker";
import { RenderBundle } from "./render/bundle";
import { Workspace, type MenuGroup } from "./workspace/workspace";
import type { DieViewApi, FrameStats } from "./panels/die-panel";
import { dieViewPanel } from "./panels/die-view-panel";
import { netlistPanel } from "./panels/netlist-host";
import { coneWalkerPanel } from "./panels/cone-walker-panel";
import { waveformPanel } from "./panels/waveform-panel";
import { sequenceEditorPanel } from "./panels/sequence-editor-panel";
import { notebookPanel } from "./panels/notebook-panel";
import { experimentPanel } from "./panels/experiment-panel";
import { modelPanel } from "./panels/model-panel";
import { registerPanel } from "./panels/register-host";
import { replPanel } from "./panels/repl-panel";
import { labels } from "./store/labels";
import { notebookFor } from "./notebook/store";
import { score, scoreCard, coverage, type Coverage, type ScoreCard } from "./notebook/scoring";
import { coverageBasis, type CoverageBasis } from "./notebook/basis";
import type { WriteupSession } from "./notebook/writeup";
import { ModelStore, bestCleanRun } from "./model/store";
import {
  onCleared,
  recordEngagement,
  recordHintTaken,
  recordScore,
  recordSolved,
  solvedDay,
  solvedState,
} from "./store/progress";
import { Guide, armAutostart } from "./guide/guide";
import type { HintsControl, SolvedState, SubmitControl } from "./workspace/toolbar";
import { verifySubmission, type Submission } from "./puzzles/answer-check";
import { GUIDE_PUZZLE_ID } from "./guide/steps";
import { createDesignClient, type DesignClient } from "./design/client";
import { winConditionSource } from "./design/win-condition";
import { parseTapeBundle, type GateTape } from "./sim/tape";
import { SimStore } from "./sim/store";
import { persistStimulus, restoreStimulus } from "./sim/stimulus";
import { menuUrl, openPuzzle, rememberPuzzle, type PuzzleDescriptor } from "./puzzles/catalog";
import { panelsFor } from "./puzzles/tools";
import { assetUrl } from "./asset-url.ts";

// The toolbar's menu bar, macOS/Windows style. The Notebook is deliberately
// absent -- it is the only scored surface in the game, so it stays a
// first-class toolbar button next to `guide` rather than hiding in a menu.
// Every panel named here is always
// reachable; which of them the *default* layout opens is a separate list
// (`defaultPanelIds` below), gated by the loaded puzzle's `tools_enabled`
// through `panelsFor`.
const MENUS: MenuGroup[] = [
  { label: "View", items: ["die-view", "netlist", "waveform"] },
  { label: "Analyse", items: ["cone-walker", "register-inspector", "repl"] },
  { label: "Experiment", items: ["sequence-editor", "experiments", "model-builder"] },
];

/** How often engaged time is banked. Small enough that closing the tab loses
 *  a negligible amount, large enough to be a rounding error against the
 *  ~5 MB storage quota. */
const ENGAGEMENT_TICK_MS = 15_000;

/**
 * Accumulate time-on-puzzle into the progress store, for the write-up's
 * wall-clock bonus.
 *
 * Two properties this has and a start-timestamp would not. A hidden tab does
 * not count, so a puzzle left open in a background tab overnight does not
 * arrive at its solve looking like a twelve-hour struggle. And each tick banks
 * at most one interval regardless of how long the timer was actually starved
 * -- a throttled background tab or a sleeping laptop fires late, and the wall
 * clock between two ticks is not time the player spent playing.
 *
 * Never stopped: the page owns the puzzle until it is unloaded.
 */
/**
 * The observables this puzzle asks a model to reproduce, for the score's model
 * scope component (notebook/scoring.ts).
 *
 * Read off what the puzzle already declares rather than from a new manifest
 * field: the lock the driver names, plus whatever its win check actually looks
 * at. Both are already the objective the player is shown, so nothing here is a
 * secret and nothing has to be authored twice. A `digest` check observes
 * nothing at all -- its answer cannot be established by simulating -- so such a
 * puzzle's required set is just the lock, and a puzzle with neither gets an
 * empty set and loses the component rather than being scored zero on it.
 */
function requiredObservables(puzzle: PuzzleDescriptor): string[] {
  const names = new Set<string>();
  if (puzzle.driver.successNet) names.add(puzzle.driver.successNet);
  const checks = puzzle.checks;
  if (checks?.kind === "latch") names.add(checks.net);
  if (checks?.kind === "bus-at") for (const bit of checks.bus) names.add(bit);
  return [...names];
}

function startEngagementClock(puzzleId: string): void {
  let last = Date.now();
  setInterval(() => {
    const now = Date.now();
    const elapsed = Math.min(now - last, ENGAGEMENT_TICK_MS);
    last = now;
    if (document.visibilityState === "visible") recordEngagement(puzzleId, elapsed);
  }, ENGAGEMENT_TICK_MS);
}

// Which puzzle is loaded, and its driver protocol, come from the catalog
// (`/puzzles/index.json`, written by scripts/sync-assets.mjs from each baked
// puzzle). What used to be a block of Two Stars constants here is now that
// puzzle's entry in the catalog, derived from its solution.json -- so a new
// level is a bake plus a sync, with nothing to edit in this file.
//
// SimStore still knows nothing about which port is a clock or a reset, only
// "primary inputs" and "an optional step before cycle 0"; the descriptor is
// what supplies the puzzle-specific half.
export async function bootWorkspace(
  catalog: PuzzleDescriptor[],
  puzzle: PuzzleDescriptor,
): Promise<void> {
  const t0 = performance.now();
  let tBundle = t0;

  // Set before anything else so the attribute always exists: a driver can
  // then wait on `body[data-ready="true"]` and tell "still starting" from "a
  // build with no readiness signal at all". It flips once the design handle
  // exists, below.
  document.body.dataset.ready = "false";

  //: Set here rather than by whatever navigated here: the menu links straight
  //: to a URL, and a deep link has nothing that could have set it.
  rememberPuzzle(puzzle.id);
  //: Before any panel is built, so the first chip drawn already knows what the
  //: player called it. Per puzzle: `n96` means nothing in another design.
  labels.open(puzzle.id);

  // If this puzzle's saved state is cleared from the menu in another tab,
  // reload rather than carry on: the notebook, labels, model and evidence
  // stores are all held in memory here and would write themselves straight
  // back on the next change, quietly undoing the clear. Clearing a *different*
  // puzzle does not fire this (store/progress.ts's `onCleared` is scoped), so
  // a workspace you are working in is never disturbed by tidying up elsewhere.
  onCleared(puzzle.id, () => {
    location.reload();
  });
  document.title = `DIESHARK — ${puzzle.title}`;

  startEngagementClock(puzzle.id);

  const allowedPanels = new Set<string>(panelsFor(puzzle));

  const driver = puzzle.driver;
  //: One URL, because the sweep worker fetches its own copy of the tape (a
  //: cache hit) rather than having one posted to it per sweep.
  const tapeUrl = assetUrl(puzzle.assets.tape);
  //: What the notebook's coverage measures the explained fraction of.
  //: Null for a puzzle with no lock (`parameter`), and
  //: carried as null rather than defaulted to a name: the descriptor is the
  //: only thing that knows what this design calls its lock, and inventing
  //: `"success"` here would have been right by authoring convention rather
  //: than by anything the bundle said. The panels that read it each say what
  //: they do without one.
  const successNet = driver.successNet;

  const bundleReady: Promise<RenderBundle> = fetch(assetUrl(puzzle.assets.render))
    .then((r) => r.arrayBuffer())
    .then((buf) => {
      tBundle = performance.now();
      Object.assign(globalThis, { __bundleBytes: buf.byteLength });
      return RenderBundle.parse(buf);
    });

  // The gate tape, same loading tier as the render bundle -- neither waits
  // on Pyodide: the target is a 300 ms fetch of tape.bin + netlist.json to
  // get sim, waveform and netlist browser live.
  const tapeReady: Promise<GateTape> = fetch(tapeUrl)
    .then((r) => r.arrayBuffer())
    .then(parseTapeBundle);

  //: The sequence the player was holding, restored before any panel sees the
  //: store and kept in step with it from then on. Everything else a session
  //: produces already survives a reload; the stimulus is the one thing that
  //: did not, and it is the answer -- see sim/stimulus.ts.
  const storeReady: Promise<SimStore> = tapeReady.then((tape) => {
    const store = new SimStore(tape, driver.cycles, {
      resetVector: driver.resetVector,
      initialLevels: driver.initialLevels,
    });
    restoreStimulus(puzzle.id, store);
    persistStimulus(puzzle.id, store);
    return store;
  });

  let pyLine = "python: booting…";
  let dieApi: DieViewApi | null = null;

  // Held unwrapped, same pattern as notebookPanel's own local `store`: the
  // submit widget's "prefill from what's currently driven" needs a
  // synchronous read, and `storeReady` is a promise.
  let liveStore: SimStore | null = null;
  void storeReady.then((s) => {
    liveStore = s;
  });

  /** This puzzle's solved marker as the toolbar wants it, or undefined while
   *  it is unsolved. Re-read rather than cached: a submission accepted during
   *  the session should light the marker up without a reload. */
  const solvedNow = (): SolvedState | undefined => {
    const record = solvedState(puzzle.id);
    if (!record?.solvedAt) return undefined;
    return {
      solvedOn: solvedDay(record.solvedAt),
      attempts: record.attempts,
      score: record.bestScore,
    };
  };

  // ---- Python side. Analysis panels (netlist browser, cone walker) show
  // their own "analysis engine starting" state until this resolves -- they
  // need a live design handle and there is no TS-side netlist model to
  // fall back to. -------

  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const api = wrap<GdsxWorker>(worker);

  const timings: Record<string, number> = {};

  const pyReady = (async () => {
    await bundleReady;
    timings.fetch_render_bin_ms = Math.round(tBundle - t0);
    const tPy0 = performance.now();
    await api.ready();
    timings.pyodide_boot_ms = Math.round(performance.now() - tPy0);
    pyLine = `python: ready in ${timings.pyodide_boot_ms} ms`;
  })();

  const designReady: Promise<DesignClient> = pyReady.then(() =>
    createDesignClient(api, assetUrl(puzzle.assets.netlist)),
  );

  // Coverage's denominator: a design call and a step through the success
  // flop, shared with the Notebook panel via the same memoised promise
  // (notebook/basis.ts) rather than computed twice. Started as soon as the
  // design handle exists, not gated on the Notebook panel ever having been
  // opened -- a player who never opens it must still be scored against a
  // measured cone, not a null one.
  const basisReady: Promise<CoverageBasis> = designReady.then((design) =>
    coverageBasis(design, successNet),
  );

  // One notebook instance for this puzzle, shared with every panel that also
  // calls `notebookFor(puzzle.id)` (it is keyed by puzzle id, see
  // notebook/store.ts) -- held here so the re-scoring below can subscribe to
  // it directly.
  const notebook = notebookFor(puzzle.id);

  // The Model Builder's one instance for this puzzle, hoisted here rather
  // than constructed fresh per read: `currentScore` needs
  // to read its badge, and the re-scoring below needs to subscribe to it.
  // Built with an empty starter -- boot.ts does not know this puzzle's
  // starter source, only the Model Builder panel does (`starterFor`), and an
  // empty starter here changes nothing for a saved model (restored from
  // localStorage over it) or an unsaved one (the panel seeds it via
  // `setLanguage`, which only ever fills an empty source).
  const modelStore = new ModelStore(puzzle.id, "");

  // What a model is asked to reproduce here, for the score's scope component.
  // Derived once: it is a property of the puzzle, not of any run.
  const required = requiredObservables(puzzle);

  /**
   * This puzzle's score. Assembled here because this is the one place holding
   * the progress record, the puzzle descriptor, the notebook and the model
   * store at once.
   *
   * An unsolved puzzle gets a card that says so and carries no points --
   * a score is shown only after solving, and `scoreCard` is where that
   * rule is enforced rather than at each of the surfaces below.
   *
   * Async because coverage's denominator is: `basisReady` is awaited so a
   * solve is never scored against an unmeasured cone just because the
   * analysis engine was still booting.
   */
  const currentScore = async (): Promise<ScoreCard> => {
    const record = solvedState(puzzle.id);
    let found: Coverage | null = null;
    try {
      const basis = await basisReady;
      found = coverage(notebook, basis.flops, basis.coneNets);
    } catch {
      // The design handle never came up -- there is no cone to measure
      // against, and the breakdown says so rather than reporting a 0% no one
      // measured.
      found = null;
    }
    return scoreCard({
      solved: Boolean(record?.solvedAt),
      coverage: found,
      claimPoints: score(notebook),
      // The best clean run, not the badge and not the latest one: agreement
      // is scored as a slope up to the badge's vector count, and a model that
      // agreed all the way through and has since been edited still did
      // (see model/store.ts's `bestCleanRun`).
      model: bestCleanRun(modelStore),
      requiredObservables: required,
      solveMs: record?.solveMs ?? null,
      parMinutes: puzzle.parMinutes,
      hintsTaken: record?.hintsTaken ?? 0,
    });
  };

  /** The score, the timings and the hint text for the write-up's summary,
   *  or null before there is anything to summarise. The
   *  hints are quoted in full rather than counted, so the tiers are looked up
   *  here where the puzzle descriptor is. */
  const currentSession = async (): Promise<WriteupSession | null> => {
    const record = solvedState(puzzle.id);
    // No record at all means nothing has happened worth summarising -- not
    // one attempt, not one hint. An unsolved-but-attempted puzzle still gets
    // a Session section; only the Score half waits for the solve.
    if (!record) return null;
    return {
      score: await currentScore(),
      attempts: record.attempts,
      solveMs: record.solveMs ?? null,
      parMinutes: puzzle.parMinutes,
      hintsTaken: puzzle.hints.slice(0, record.hintsTaken),
    };
  };

  /**
   * Re-score and save whenever something the score depends on changes --
   * a claim settled, a model run recorded -- and the puzzle is already
   * solved. Everything the score rewards most (a validated model, settled
   * claims, coverage) is often finished after the key is cracked, and without
   * this none of that reached the main menu: `bestScore` was written once, at
   * submit, and never again.
   *
   * `recordScore` takes the max with whatever is already saved, so this can
   * only raise the saved score, never lower it.
   */
  const rescoreIfSolved = (): void => {
    if (!solvedState(puzzle.id)?.solvedAt) return;
    void currentScore().then((card) => {
      if (card.solved) recordScore(puzzle.id, card.total);
    });
  };
  notebook.subscribe(rescoreIfSolved);
  modelStore.subscribe(rescoreIfSolved);

  // Always available, never gated behind progress: the
  // control just exposes the puzzle's tiers and reads/writes the same
  // per-puzzle progress record `submitControl` below writes attempts into.
  const hintsControl: HintsControl = {
    tiers: puzzle.hints,
    revealedCount: () => solvedState(puzzle.id)?.hintsTaken ?? 0,
    revealNext: () => {
      const revealed = solvedState(puzzle.id)?.hintsTaken ?? 0;
      if (revealed >= puzzle.hints.length) return;
      recordHintTaken(puzzle.id, revealed);
    },
  };

  const submitControl: SubmitControl = {
    checks: puzzle.checks,
    trackPorts: driver.trackPorts,
    currentBits: (port) =>
      liveStore ? Array.from(liveStore.bitsOf(port), (b) => (b ? "1" : "0")).join("") : "",
    submit: async (submission: Submission) => {
      // A `digest` check observes nothing and needs no running design; every
      // other kind drives `submission` into the live store and reads it back
      // -- see answer-check.ts's `verifySubmission` for why the store is
      // optional at all.
      const store = puzzle.checks?.kind === "digest" ? undefined : await storeReady;
      const verdict = await verifySubmission(puzzle, submission, store);
      recordSolved(puzzle.id, verdict);
      // Scored after recording, because "solved" is one of the score's inputs
      // and this call is what makes it true. Persisted so the level menu --
      // a separate document that loads none of these stores -- can show it.
      const card = await currentScore();
      if (card.solved) recordScore(puzzle.id, card.total);
      const solved = solvedNow();
      if (solved) workspace.setSolved(solved);
      return verdict;
    },
    scoreCard: async () => {
      const card = await currentScore();
      return card.solved ? card : null;
    },
  };

  // Which flops the puzzle's lock needs, derived once and shared by the panels
  // that offer it (Experiments' watch selector, Sticky Flops' suggestions).
  // `driver.successNet` rather than the defaulted `successNet` above: a puzzle
  // that declares no lock must get "there is no win condition" and not a
  // round trip asking about a net called `success`.
  const winCondition = winConditionSource(designReady, driver.successNet);

  // The guide needs the workspace (it moves the player between panels) and
  // the workspace's toolbar needs the guide (it draws the button), so the
  // control is a thin indirection created first and pointed at the Guide the
  // moment it exists. Nothing calls into it before then: every entry point is
  // a click.
  let guide: Guide | null = null;
  const guideListeners: (() => void)[] = [];
  const notifyGuideChange = (): void => {
    for (const fn of guideListeners) fn();
  };
  const isGuidePuzzle = puzzle.id === GUIDE_PUZZLE_ID;
  const hasGuidePuzzle = catalog.some((p) => p.id === GUIDE_PUZZLE_ID);

  const guideControl = hasGuidePuzzle
    ? {
        isOpen: () => guide?.active ?? false,
        toggle: () => {
          // From another level the walkthrough is about a design that is not
          // loaded, so asking for it means "take me there" -- a navigation,
          // with the intent parked in storage across it.
          if (!isGuidePuzzle) {
            armAutostart();
            openPuzzle(GUIDE_PUZZLE_ID);
            return;
          }
          if (guide?.active) guide.close();
          else guide?.open();
        },
        subscribe: (fn: () => void) => {
          guideListeners.push(fn);
        },
      }
    : undefined;

  const workspaceEl = document.getElementById("workspace") as HTMLDivElement;
  const workspace = new Workspace(
    workspaceEl,
    [
      dieViewPanel({
        bundleReady,
        statusLine: () => pyLine,
        onFocusPanel: (id) => workspace.focus(id),
        onReady: (api) => {
          dieApi = api;
        },
      }),
      netlistPanel(designReady, { onFocusPanel: (id) => workspace.focus(id) }),
      coneWalkerPanel({
        designReady,
        storeReady,
        puzzleId: puzzle.id,
        onFocusWaveform: () => workspace.focus("waveform"),
      }),
      waveformPanel({ storeReady, trackPorts: driver.trackPorts, successNet }),
      sequenceEditorPanel({ storeReady, successNet, trackPorts: driver.trackPorts }),
      notebookPanel({
        designReady,
        storeReady,
        puzzleId: puzzle.id,
        successNet,
        keyPort: driver.keyPort,
        session: currentSession,
      }),
      // Both of M3's measurement panels run on the gate tape, so they are live
      // as soon as tape.bin lands and do not wait for Pyodide -- the sweep that
      // cracks a puzzle open is available before the analysis engine boots.
      experimentPanel({ storeReady, designReady, winCondition, puzzleId: puzzle.id, tapeUrl }),
      modelPanel({ storeReady, puzzleId: puzzle.id, successNet, keyPort: driver.keyPort, model: modelStore }),
      registerPanel({
        designReady,
        puzzleId: puzzle.id,
        successNet,
        winCondition,
        clockPort: driver.clockPort,
        resetPort: driver.resetPort,
      }),
      replPanel({ api, designReady, puzzleId: puzzle.id }),
    ],
    {
      menus: MENUS,
      // The menu bar's own order, plus the Notebook (no menu names it) --
      // filtered down to what this puzzle's tools_enabled actually asks for.
      defaultPanelIds: [...MENUS.flatMap((m) => m.items), "notebook"].filter((id) =>
        allowedPanels.has(id),
      ),
      levels: {
        puzzles: catalog.map((p) => ({
          id: p.id,
          title: p.title,
          blurb: p.blurb,
          parMinutes: p.parMinutes,
        })),
        currentId: puzzle.id,
        onSelect: openPuzzle,
      },
      guide: guideControl,
      objective: {
        blurb: puzzle.blurb,
        answerKind: puzzle.answerKind,
        parMinutes: puzzle.parMinutes,
      },
      submit: submitControl,
      hints: hintsControl,
      // The way out. Everything else in the query survives the trip, so a
      // player who arrived with `?debug=1` keeps it.
      home: { href: menuUrl() },
      solved: solvedNow(),
    },
  );

  if (isGuidePuzzle) {
    guide = new Guide(workspace);
    guide.subscribe(notifyGuideChange);
    notifyGuideChange();
  }

  // Ready means the design handle exists, not merely that Pyodide answered:
  // that is the point at which the analysis panels stop saying "loading…",
  // and it is the thing a script waiting to drive the app actually needs.
  // `pyLine` is the detail boot already tracked ("python: ready in 1140 ms"),
  // which until now was only visible inside the Die View's collapsed debug
  // disclosure.
  void designReady.then(
    () => {
      document.body.dataset.ready = "true";
      workspace.setReadiness({ phase: "ready", detail: pyLine });
    },
    (err: unknown) => {
      // Left on screen rather than fading: a boot that failed is the one
      // state the player has to keep seeing.
      workspace.setReadiness({
        phase: "failed",
        detail: err instanceof Error ? err.message : String(err),
      });
    },
  );

  /** Time api.analyse() on the loaded puzzle's baked netlist -- what the
   *  game does at load. */
  async function analyseBaked(): Promise<Envelope> {
    await pyReady;
    const netlistJson = await fetch(assetUrl(puzzle.assets.netlist)).then((r) => r.text());
    const opened = (await api.call("load_netlist", netlistJson)) as Envelope<{
      handle: string;
    }>;
    if (!opened.ok) throw new Error(JSON.stringify(opened.error));
    const t = performance.now();
    const result = await api.call("analyse", opened.data!.handle);
    timings.analyse_baked_ms = Math.round(performance.now() - t);
    pyLine = `python: analyse(baked netlist) ${timings.analyse_baked_ms} ms`;
    return result;
  }

  /** Time the whole path from GDS bytes: extract, then analyse. Pinned to
   *  the sample GDS whichever puzzle is loaded -- it is the M0 reference
   *  measurement, and comparing it across runs only means anything if the
   *  design being extracted is the same one every time. */
  async function analyseFromGds(): Promise<Envelope> {
    await pyReady;
    const gds = await fetch(assetUrl("samples/puzzle.gds")).then((r) => r.arrayBuffer());
    const opened = (await api.call("open_design", new Uint8Array(gds))) as Envelope<{
      handle: string;
    }>;
    if (!opened.ok) throw new Error(JSON.stringify(opened.error));
    const handle = opened.data!.handle;
    const tE = performance.now();
    const extracted = await api.call("extract", handle);
    if (!extracted.ok) throw new Error(JSON.stringify(extracted.error));
    timings.extract_gds_ms = Math.round(performance.now() - tE);
    const tA = performance.now();
    const result = await api.call("analyse", handle);
    timings.analyse_after_extract_ms = Math.round(performance.now() - tA);
    pyLine =
      `python: extract ${timings.extract_gds_ms} ms, ` +
      `analyse ${timings.analyse_after_extract_ms} ms`;
    return result;
  }

  const idleFrame: FrameStats = { fps: 0, frameMs: 0, drawCalls: 0, instances: 0, lod: 0 };

  // The die panel's own `bundleReady.then(...)` was registered before this
  // one (it runs synchronously inside `new Workspace(...)` above), so by the
  // time this continuation runs `dieApi` is already populated -- `spike`
  // therefore appears in `globalThis` with real data from the start, same
  // as the M0 spike this replaces.
  await bundleReady;

  Object.assign(globalThis, {
    api,
    workspace,
    storeReady,
    spike: {
      timings,
      get bundleBytes() {
        return (globalThis as { __bundleBytes?: number }).__bundleBytes ?? 0;
      },
      get rendererInfo() {
        return dieApi?.rendererInfo ?? "";
      },
      frame: () => dieApi?.frame() ?? idleFrame,
      fit: () => dieApi?.fit(),
      setLod: (l: number | "auto") => dieApi?.setLod(l),
      resetMeter: () => dieApi?.resetMeter(),
      setRepeat: (n: number) => dieApi?.setRepeat(n),
      analyseBaked,
      analyseFromGds,
      ready: pyReady,
    },
  });
}

export type Spike = {
  timings: Record<string, number>;
  bundleBytes: number;
  rendererInfo: string;
  frame(): FrameStats;
  fit(): void;
  setLod(l: number | "auto"): void;
  resetMeter(): void;
  setRepeat(n: number): void;
  analyseBaked(): Promise<Envelope>;
  analyseFromGds(): Promise<Envelope>;
  ready: Promise<void>;
};

export type { Remote };