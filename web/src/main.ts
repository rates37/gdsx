// M0 spike entry point.
//
// The die view comes up from the precomputed render bundle without waiting
// for Python. 
// Pyodide boots afterwards in a worker, purely so the spike can time `api.analyse()`
// against the same page.
//
// `globalThis.spike` exposes the measurements the M0 gate asks for so they can
// be read from the console or driven by scripts/measure-m0.mjs.

import { wrap, type Remote } from "comlink";
import type { GdsxWorker, Envelope } from "./worker";
import { RenderBundle } from "./render/bundle";
import { DieView } from "./render/dieview";

const canvas = document.getElementById("die") as HTMLCanvasElement;
const stats = document.getElementById("stats") as HTMLPreElement;
const layerList = document.getElementById("layer-list") as HTMLDivElement;
const lodSelect = document.getElementById("lod") as HTMLSelectElement;

const COLOUR_CSS: Record<string, string> = {
  instances: "#8c94a8",
  li1: "#6bbf6b",
  met1: "#598cf2",
  met2: "#f2735a",
  met3: "#f2cc4d",
  met4: "#bf66e6",
  met5: "#66e6e6",
};

interface FrameStats {
  fps: number;
  frameMs: number;
  drawCalls: number;
  instances: number;
  lod: number;
}

/** Rolling frame timing over the last second of animation frames. */
class FpsMeter {
  private times: number[] = [];
  tick(now: number): void {
    this.times.push(now);
    while (this.times.length > 2 && now - this.times[0] > 1000) this.times.shift();
  }
  get fps(): number {
    if (this.times.length < 2) return 0;
    const span = this.times[this.times.length - 1] - this.times[0];
    return span > 0 ? ((this.times.length - 1) * 1000) / span : 0;
  }
  get frameMs(): number {
    const f = this.fps;
    return f > 0 ? 1000 / f : 0;
  }
  reset(): void {
    this.times = [];
  }
}

async function main(): Promise<void> {
  const t0 = performance.now();

  const buf = await fetch("/samples/puzzle.render.bin").then((r) => r.arrayBuffer());
  const bundle = RenderBundle.parse(buf);
  const tBundle = performance.now();

  const view = new DieView(canvas, bundle);
  const meter = new FpsMeter();

  // Layer toggles, cells first then the routing stack bottom-up.
  for (const name of ["instances", ...bundle.header.layers]) {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = view.isLayerOn(name);
    box.addEventListener("change", () => view.setLayer(name, box.checked));
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = COLOUR_CSS[name] ?? "#fff";
    const text = document.createElement("span");
    const counts = bundle.header.lods["0"]?.[name]?.rects.count;
    text.textContent =
      name === "instances"
        ? `cells (${bundle.header.instances.count})`
        : `${name} (${counts ?? 0})`;
    label.append(box, swatch, text);
    layerList.append(label);
  }

  lodSelect.addEventListener("change", () => {
    view.lodMode = lodSelect.value === "auto" ? "auto" : Number(lodSelect.value);
    meter.reset();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "f" || e.key === "F") view.fit();
  });
  window.addEventListener("resize", () => meter.reset());

  let repeat = 1;
  let pyLine = "python: booting…";
  let last: FrameStats = { fps: 0, frameMs: 0, drawCalls: 0, instances: 0, lod: 0 };

  function frame(now: number): void {
    // `repeat` > 1 draws the scene several times in one animation frame. rAF
    // is vsync-capped, so a scene with 10x headroom and one with none both
    // report 60/120 fps; multiplying the load until fps drops is the honest
    // way to find out which one this is. `gl.finish()` is not -- ANGLE on
    // Metal returns from it long before the GPU is done, and it reports an
    // absurd 46,000 fps.
    for (let i = 0; i < repeat; i++) view.render();
    meter.tick(now);
    last = {
      fps: meter.fps,
      frameMs: meter.frameMs,
      drawCalls: view.lastFrame.drawCalls,
      instances: view.lastFrame.instances,
      lod: view.lastFrame.lod,
    };
    stats.textContent = [
      `${bundle.header.top}  ${(buf.byteLength / 1e6).toFixed(2)} MB bundle`,
      `render.bin fetched+parsed in ${(tBundle - t0).toFixed(0)} ms`,
      `gl: ${rendererInfo}`,
      "",
      `lod ${last.lod}   ${last.drawCalls} draws   ${last.instances} rects`,
      `${last.fps.toFixed(1)} fps   ${last.frameMs.toFixed(2)} ms/frame`,
      "",
      pyLine,
    ].join("\n");
    requestAnimationFrame(frame);
  }
  const rendererInfo = view.rendererInfo();
  requestAnimationFrame(frame);

  // ---- Python side, strictly after the die view is live --------------------

  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const api = wrap<GdsxWorker>(worker);

  const timings: Record<string, number> = {
    fetch_render_bin_ms: Math.round(tBundle - t0),
  };

  const pyReady = (async () => {
    const tPy0 = performance.now();
    await api.ready();
    timings.pyodide_boot_ms = Math.round(performance.now() - tPy0);
    pyLine = `python: ready in ${timings.pyodide_boot_ms} ms`;
  })();

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

  Object.assign(globalThis, {
    api,
    view,
    spike: {
      timings,
      bundleBytes: buf.byteLength,
      rendererInfo,
      frame: () => last,
      fit: () => {
        view.fit();
        meter.reset();
      },
      setLod: (l: number | "auto") => {
        view.lodMode = l;
        lodSelect.value = String(l);
        meter.reset();
      },
      resetMeter: () => meter.reset(),
      setRepeat: (n: number) => {
        repeat = Math.max(1, n);
        meter.reset();
      },
      analyseBaked,
      analyseFromGds,
      ready: pyReady,
    },
  });
}

main().catch((err) => {
  stats.textContent = `ERROR: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
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