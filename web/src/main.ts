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
import { dieViewPanel, type DieViewApi, type FrameStats } from "./panels/die-panel";
import { netlistBrowserPanel } from "./panels/netlist-panel";
import { coneWalkerPanel } from "./panels/cone-walker-panel";
import { waveformPanel } from "./panels/waveform-panel";
import { sequenceEditorPanel } from "./panels/sequence-editor-panel";
import { notebookPanel } from "./panels/notebook-panel";
import { createDesignClient, type DesignClient } from "./design/client";
import { parseTapeBundle, type GateTape } from "./sim/tape";
import { SimStore } from "./sim/store";

// Two Stars' own driver protocol (puzzle-solved-no-sat.md §9): `clk` is
// never read by the executor so it is not a track at all; `enable` is held
// high throughout including the one-cycle reset pulse; `rst_n` deasserts
// (asserted low, so 0) for that same one cycle and is high for the rest.
// This is glue for *this* puzzle -- SimStore itself knows nothing about
// which port is a clock or a reset, only "primary inputs" and "an optional
// step before cycle 0".
const PUZZLE_RESET_VECTOR = { clk: 0, rst_n: 0, enable: 1, I: 0 };
const PUZZLE_DEFAULT_LEVELS: Record<string, 0 | 1> = { enable: 1, rst_n: 1 };
const PUZZLE_CYCLES = 140;
//: What the notebook's coverage measures the explained fraction of, per
//: game-plan.md §5, and the net every genre of this puzzle is ultimately about.
const PUZZLE_SUCCESS_NET = "success";
const PUZZLE_ID = "two-stars";

async function main(): Promise<void> {
  const t0 = performance.now();
  let tBundle = t0;

  const bundleReady: Promise<RenderBundle> = fetch("/samples/puzzle.render.bin")
    .then((r) => r.arrayBuffer())
    .then((buf) => {
      tBundle = performance.now();
      Object.assign(globalThis, { __bundleBytes: buf.byteLength });
      return RenderBundle.parse(buf);
    });

  // The gate tape, same loading tier as the render bundle -- neither waits
  // on Pyodide (game-plan.md §9: "300 ms fetch tape.bin + netlist.json ->
  // sim, waveform, netlist browser live").
  const tapeReady: Promise<GateTape> = fetch("/samples/puzzle.tape.bin")
    .then((r) => r.arrayBuffer())
    .then(parseTapeBundle);

  const storeReady: Promise<SimStore> = tapeReady.then(
    (tape) =>
      new SimStore(tape, PUZZLE_CYCLES, {
        resetVector: PUZZLE_RESET_VECTOR,
        initialLevels: PUZZLE_DEFAULT_LEVELS,
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
    createDesignClient(api, "/samples/puzzle.netlist.json"),
  );

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
      netlistBrowserPanel(designReady),
      coneWalkerPanel(designReady),
      waveformPanel(storeReady),
      sequenceEditorPanel(storeReady),
      notebookPanel({
        designReady,
        storeReady,
        puzzleId: PUZZLE_ID,
        successNet: PUZZLE_SUCCESS_NET,
        onCoverage: (text) => workspace.setStatus(text),
      }),
    ],
    [
      ["die-view", "netlist", "cone-walker", "notebook"],
      ["waveform", "sequence-editor"],
    ],
  );

  /** Time api.analyse() on the baked netlist -- what the game does at load. */
  async function analyseBaked(): Promise<Envelope> {
    await pyReady;
    const netlistJson = await fetch("/samples/puzzle.netlist.json").then((r) => r.text());
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

  /** Time the whole path from GDS bytes: extract, then analyse. */
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