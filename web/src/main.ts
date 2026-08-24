// Entry point: the panel workspace shell paints immediately; the die view
// comes up as soon as `render.bin` arrives, without waiting for Python.
// Pyodide boots afterwards in a worker, so the M0 timing spike (extract +
// analyse against the same page) still runs -- `globalThis.spike` exposes
// the measurements it reads, unchanged from the M0 shape so
// `scripts/measure-m0.mjs` keeps working.

import { wrap, type Remote } from "comlink";
import type { GdsxWorker, Envelope } from "./worker";
import { RenderBundle } from "./render/bundle";
import { Workspace } from "./workspace/workspace";
import type { DieViewApi, FrameStats } from "./panels/die-panel";
import { dieViewPanel } from "./panels/die-view-panel";
import { netlistPanel } from "./panels/netlist-host";
import { coneWalkerPanel } from "./panels/cone-walker-panel";
import { waveformPanel } from "./panels/waveform-panel";
import { sequenceEditorPanel } from "./panels/sequence-editor-panel";
import { notebookPanel } from "./panels/notebook-panel";
import { experimentPanel } from "./panels/experiment-panel";
import { modelPanel } from "./panels/model-panel";
import { registerInspectorPanel } from "./panels/register-inspector-panel";
import { requirementsPanel } from "./panels/requirements-panel";
import { registerDecoderPanel } from "./panels/register-decoder-panel";
import { stickyFlopsPanel } from "./panels/sticky-flops-panel";
import { constraintsPanel } from "./panels/constraints-panel";
import { replPanel } from "./panels/repl-panel";
import { labels } from "./store/labels";
import { Guide, armAutostart } from "./guide/guide";
import { GUIDE_PUZZLE_ID } from "./guide/steps";
import { createDesignClient, type DesignClient } from "./design/client";
import { parseTapeBundle, type GateTape } from "./sim/tape";
import { SimStore } from "./sim/store";
import {
  choosePuzzle,
  lastPlayedId,
  loadCatalog,
  openPuzzle,
  rememberPuzzle,
  requestedId,
} from "./puzzles/catalog";

// Which puzzle is loaded, and its driver protocol, come from the catalog
// (`/puzzles/index.json`, written by scripts/sync-assets.mjs from each baked
// puzzle). What used to be a block of Two Stars constants here is now that
// puzzle's entry in the catalog, derived from its solution.json -- so a new
// level is a bake plus a sync, with nothing to edit in this file.
//
// SimStore still knows nothing about which port is a clock or a reset, only
// "primary inputs" and "an optional step before cycle 0"; the descriptor is
// what supplies the puzzle-specific half.
async function main(): Promise<void> {
  const t0 = performance.now();
  let tBundle = t0;

  const catalog = await loadCatalog();
  const puzzle = choosePuzzle(catalog, {
    requested: requestedId(),
    lastPlayed: lastPlayedId(),
  });
  rememberPuzzle(puzzle.id);
  //: Before any panel is built, so the first chip drawn already knows what the
  //: player called it. Per puzzle: `n96` means nothing in another design.
  labels.open(puzzle.id);
  document.title = `DIESHARK — ${puzzle.title}`;

  const driver = puzzle.driver;
  //: One URL, because the sweep worker fetches its own copy of the tape (a
  //: cache hit) rather than having one posted to it per sweep.
  const tapeUrl = puzzle.assets.tape;
  //: What the notebook's coverage measures the explained fraction of, per
  //: game-plan.md §5. A puzzle with no lock at all (`parameter`) would need
  //: the panels that read this gated by `tools_enabled` before it could be
  //: offered; no baked puzzle has that shape yet.
  const successNet = driver.successNet ?? "success";

  const bundleReady: Promise<RenderBundle> = fetch(puzzle.assets.render)
    .then((r) => r.arrayBuffer())
    .then((buf) => {
      tBundle = performance.now();
      Object.assign(globalThis, { __bundleBytes: buf.byteLength });
      return RenderBundle.parse(buf);
    });

  // The gate tape, same loading tier as the render bundle -- neither waits
  // on Pyodide (game-plan.md §9: "300 ms fetch tape.bin + netlist.json ->
  // sim, waveform, netlist browser live").
  const tapeReady: Promise<GateTape> = fetch(tapeUrl)
    .then((r) => r.arrayBuffer())
    .then(parseTapeBundle);

  const storeReady: Promise<SimStore> = tapeReady.then(
    (tape) =>
      new SimStore(tape, driver.cycles, {
        resetVector: driver.resetVector,
        initialLevels: driver.initialLevels,
      }),
  );

  let pyLine = "python: booting…";
  let dieApi: DieViewApi | null = null;

  // ---- Python side. Analysis panels (netlist browser, cone walker) show
  // their own "analysis engine starting" state until this resolves, per the
  // fallback game-plan.md §9 explicitly allows -- they need a live design
  // handle and there is no TS-side netlist model to fall back to. -------

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
    createDesignClient(api, puzzle.assets.netlist),
  );

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
        onReady: (api) => {
          dieApi = api;
        },
      }),
      netlistPanel(designReady),
      coneWalkerPanel(designReady),
      waveformPanel(storeReady),
      sequenceEditorPanel(storeReady),
      notebookPanel({
        designReady,
        storeReady,
        puzzleId: puzzle.id,
        successNet,
        keyPort: driver.keyPort,
        onCoverage: (text) => workspace.setStatus(text),
      }),
      // Both of M3's measurement panels run on the gate tape, so they are live
      // as soon as tape.bin lands and do not wait for Pyodide -- the sweep that
      // cracks a puzzle open is available before the analysis engine boots.
      experimentPanel({ storeReady, puzzleId: puzzle.id, tapeUrl }),
      modelPanel({ storeReady, puzzleId: puzzle.id }),
      registerInspectorPanel({ designReady }),
      requirementsPanel({ designReady, storeReady, puzzleId: puzzle.id }),
      registerDecoderPanel({ designReady, puzzleId: puzzle.id }),
      stickyFlopsPanel({ designReady, puzzleId: puzzle.id, successNet }),
      constraintsPanel({ designReady }),
      replPanel({ api, designReady, puzzleId: puzzle.id }),
    ],
    // One tabbed group, roughly in the order of game-plan.md §2's core loop:
    // look at the die, read the netlist, walk a cone, write it down, then the
    // measurement and solving panels.
    [
      "die-view",
      "netlist",
      "cone-walker",
      "notebook",
      "waveform",
      "sequence-editor",
      "experiments",
      "model-builder",
      "register-inspector",
      "requirements",
      "register-decoder",
      "sticky-flops",
      "constraints",
      "repl",
    ],
    {
      puzzles: catalog.map((p) => ({
        id: p.id,
        title: p.title,
        blurb: p.blurb,
        parMinutes: p.parMinutes,
      })),
      currentId: puzzle.id,
      onSelect: openPuzzle,
    },
    guideControl,
    {
      blurb: puzzle.blurb,
      answerKind: puzzle.answerKind,
      parMinutes: puzzle.parMinutes,
    },
  );

  if (isGuidePuzzle) {
    guide = new Guide(workspace);
    guide.subscribe(notifyGuideChange);
    notifyGuideChange();
  }

  /** Time api.analyse() on the loaded puzzle's baked netlist -- what the
   *  game does at load. */
  async function analyseBaked(): Promise<Envelope> {
    await pyReady;
    const netlistJson = await fetch(puzzle.assets.netlist).then((r) => r.text());
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
    const gds = await fetch("/samples/puzzle.gds").then((r) => r.arrayBuffer());
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

main().catch((err) => {
  const workspaceEl = document.getElementById("workspace");
  if (workspaceEl) {
    workspaceEl.textContent = `ERROR: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
  }
  console.error(err);
});

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