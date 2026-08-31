// The 2D half of the Die View: canvas, layer toggles, LOD select, minimap,
// and a stats readout. Builds its DOM lazily -- it is mounted (and the
// workspace shell paints) before `render.bin` has even arrived; see
// `bundleReady` in main.ts for the loading sequence this depends on.
//
// Not a panel in its own right. `die-view-panel.ts` owns the panel and
// mounts either this or the 3D view into it, per game-plan.md §4.1's
// `[2D|3D]` toggle; this module knows nothing about that choice beyond
// being told to tear itself down when the player switches away.

import type { RenderBundle } from "../render/bundle";
import { DieView } from "../render/dieview";
import { Minimap } from "../render/minimap";
import { highlightBus } from "../store/highlight";
import { coneRootBus } from "../store/selection";
import { openNetMenu } from "./net-menu";
import { netTooltip } from "./net-tooltip";
import { palette } from "../theme";


export interface FrameStats {
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

/** Handed back once the bundle has loaded and the view actually exists. */
export interface DieViewApi {
  view: DieView;
  fit(): void;
  setLod(l: number | "auto"): void;
  resetMeter(): void;
  setRepeat(n: number): void;
  frame(): FrameStats;
  rendererInfo: string;
}

export interface DiePanelOptions {
  bundleReady: Promise<RenderBundle>;
  /** Extra line shown under the frame stats, e.g. Pyodide boot status. */
  statusLine: () => string;
  /** Brings another workspace panel to the front -- what the right-click
   *  menu's "open this net over there" items need. Injected rather than
   *  imported: the panels are constructed before the `Workspace` that owns
   *  them exists (see `boot.ts`), which is also why the Cone Walker takes
   *  its `onFocusWaveform` the same way. */
  onFocusPanel?: (id: string) => void;
  /** Called with the live view once the bundle has loaded, and with `null`
   *  when this mount is torn down -- so a holder (main.ts's `spike`) never
   *  keeps calling into a renderer the player has switched away from. */
  onReady?: (api: DieViewApi | null) => void;
}

/** Mounts the 2D view into `container`, which must be a positioned element
 *  it can fill. Returns a handle whose `dispose` stops the render loop and
 *  drops the view. */
export function mountDie2D(
  container: HTMLElement,
  opts: DiePanelOptions,
): { dispose: () => void } {
  container.classList.add("die-panel");
  container.innerHTML = `
    <div class="die-canvas-wrap">
      <canvas class="die-canvas"></canvas>
      <div class="die-loading">loading render.bin&hellip;</div>
      <div class="panel-overlay die-layers">
        <h2>Layers</h2>
        <div class="layer-list"></div>
        <hr />
        <h2>Detail</h2>
        <select class="lod-select">
          <option value="auto" selected>auto (by zoom)</option>
          <option value="0">LOD 0 &mdash; full</option>
          <option value="1">LOD 1 &mdash; collapsed</option>
          <option value="2">LOD 2 &mdash; cells only</option>
        </select>
        <hr />
        <div class="hint">drag: pan<br />wheel: zoom<br />F: fit die<br />hover: highlight net<br />click: select net<br />right-click: actions</div>
      </div>
      <pre class="panel-overlay die-stats"></pre>
      <canvas class="die-minimap" title="click or drag to jump"></canvas>
    </div>`;

  const canvas = container.querySelector(".die-canvas") as HTMLCanvasElement;
  const loading = container.querySelector(".die-loading") as HTMLDivElement;
  const layerList = container.querySelector(".layer-list") as HTMLDivElement;
  const lodSelect = container.querySelector(".lod-select") as HTMLSelectElement;
  const stats = container.querySelector(".die-stats") as HTMLPreElement;
  const minimapCanvas = container.querySelector(".die-minimap") as HTMLCanvasElement;

  let disposed = false;
  let rafId = 0;
  let unsubHighlight: (() => void) | null = null;
  let onKeydown: ((e: KeyboardEvent) => void) | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let tooltip: ReturnType<typeof netTooltip> | null = null;

  opts.bundleReady
    .then((bundle) => {
      if (disposed) return;
      loading.remove();

      const view = new DieView(canvas, bundle);
      const minimap = new Minimap(minimapCanvas, bundle, view);
      const meter = new FpsMeter();
      const rendererInfo = view.rendererInfo();
      let repeat = 1;

      // The substrate and anything else synthetic is 3D-only -- listing a
      // toggle here for a layer the 2D view never draws is a dead control.
      const synthetic2D = new Set(bundle.header.synthetic_layers ?? []);
      const toggles = [
        "instances",
        ...bundle.header.layers.filter((n) => !synthetic2D.has(n)),
      ];
      for (const name of toggles) {
        const label = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = view.isLayerOn(name);
        box.addEventListener("change", () => view.setLayer(name, box.checked));
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = palette().layer[name] ?? "#fff";
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

      // Ignored while a text field has focus: this listener is on `window`,
      // so without the guard typing an "f" into the netlist filter or the
      // cone walker's net box reframed the die behind it.
      onKeydown = (e: KeyboardEvent) => {
        if (e.key !== "f" && e.key !== "F") return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const active = document.activeElement;
        if (
          active instanceof HTMLInputElement ||
          active instanceof HTMLTextAreaElement ||
          active instanceof HTMLSelectElement ||
          (active instanceof HTMLElement && active.isContentEditable)
        ) {
          return;
        }
        view.fit();
        meter.reset();
      };
      window.addEventListener("keydown", onKeydown);

      // `fit` reads `canvas.clientWidth`, and the view is built before
      // dockview has finished sizing the group -- so the camera can be framed
      // against a canvas a few tens of pixels wide and then keep that framing
      // for the rest of the session, drawing the die as a speck in the middle
      // of a large panel. Refit whenever the canvas resizes, up until the
      // player first moves the camera themselves; after that the framing is
      // theirs and nothing should take it back.
      let cameraIsOurs = true;
      const releaseCamera = (): void => {
        cameraIsOurs = false;
      };
      canvas.addEventListener("pointerdown", releaseCamera);
      canvas.addEventListener("wheel", releaseCamera, { passive: true });
      resizeObserver = new ResizeObserver(() => {
        if (cameraIsOurs) view.fit();
      });
      resizeObserver.observe(canvas);

      // Hover names the net and glows it; click fixes on it. The two are
      // different layers of the same bus (`set` vs `pin`), so moving the
      // pointer away undims back to whatever was clicked rather than
      // clearing the die.
      tooltip = netTooltip(
        container.querySelector(".die-canvas-wrap") as HTMLElement,
      );
      const tip = tooltip;

      canvas.addEventListener("pointermove", (e) => {
        const hit = view.pickNet(e.clientX, e.clientY);
        highlightBus.set(hit ? { name: hit.name } : null);
        if (hit) tip.show(e.clientX, e.clientY, hit.name);
        else tip.hide();
      });
      canvas.addEventListener("pointerleave", () => {
        highlightBus.set(null);
        tip.hide();
      });

      // A click is a press that did not turn into a pan. The die view's
      // primary gesture is dragging the camera, so anything past a few
      // pixels of travel is a pan and must not also select.
      const DRAG_SLOP_PX = 3;
      let pressX = 0;
      let pressY = 0;
      let pressButton = -1;
      canvas.addEventListener("pointerdown", (e) => {
        pressX = e.clientX;
        pressY = e.clientY;
        pressButton = e.button;
      });
      canvas.addEventListener("pointerup", (e) => {
        if (e.button !== 0 || pressButton !== 0) return;
        if (
          Math.abs(e.clientX - pressX) > DRAG_SLOP_PX ||
          Math.abs(e.clientY - pressY) > DRAG_SLOP_PX
        ) {
          return;
        }
        const hit = view.pickNet(e.clientX, e.clientY);
        highlightBus.pin(hit ? { name: hit.name } : null);
        // Same pair a netlist row click performs: pin what is selected, and
        // re-root the Cone Walker on it. Focus stays here -- the player is
        // looking at the die, and yanking them to another tab on every click
        // would make the die view unusable for browsing.
        if (hit) coneRootBus.open(hit.name);
      });

      canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const hit = view.pickNet(e.clientX, e.clientY);
        if (hit) highlightBus.pin({ name: hit.name });
        openNetMenu(e.clientX, e.clientY, hit?.name ?? null, {
          onFocusPanel: opts.onFocusPanel,
        });
      });

      unsubHighlight = highlightBus.subscribe((sel) =>
        view.setHighlightNet(sel ? view.netIdOf(sel.name) : null),
      );

      function frame(now: number): void {
        // dockview detaches an inactive tab's content from the document, so
        // this canvas can be off-screen while the loop keeps running. Drawing
        // into a detached canvas is pure waste, and on a machine with no GPU
        // (a headless browser, a VM) it is software-rasterised waste that
        // pins a core. Keep the loop alive so switching back is instant, and
        // skip the work.
        if (!canvas.isConnected) {
          meter.reset();
          rafId = requestAnimationFrame(frame);
          return;
        }
        for (let i = 0; i < repeat; i++) view.render();
        minimap.render();
        meter.tick(now);
        stats.textContent = [
          `${bundle.header.top}`,
          `lod ${view.lastFrame.lod}   ${view.lastFrame.drawCalls} draws   ${view.lastFrame.instances} rects`,
          `${meter.fps.toFixed(1)} fps   ${meter.frameMs.toFixed(2)} ms/frame`,
          "",
          opts.statusLine(),
        ].join("\n");
        rafId = requestAnimationFrame(frame);
      }
      rafId = requestAnimationFrame(frame);

      opts.onReady?.({
        view,
        rendererInfo,
        fit: () => {
          view.fit();
          meter.reset();
        },
        setLod: (l) => {
          view.lodMode = l;
          lodSelect.value = String(l);
          meter.reset();
        },
        resetMeter: () => meter.reset(),
        setRepeat: (n) => {
          repeat = Math.max(1, n);
          meter.reset();
        },
        frame: () => ({
          fps: meter.fps,
          frameMs: meter.frameMs,
          drawCalls: view.lastFrame.drawCalls,
          instances: view.lastFrame.instances,
          lod: view.lastFrame.lod,
        }),
      });
    })
    .catch((err) => {
      loading.textContent = `ERROR: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
      console.error(err);
    });

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(rafId);
      unsubHighlight?.();
      tooltip?.dispose();
      tooltip = null;
      resizeObserver?.disconnect();
      if (onKeydown) window.removeEventListener("keydown", onKeydown);
      opts.onReady?.(null);
      container.classList.remove("die-panel");
      container.replaceChildren();
    },
  };
}