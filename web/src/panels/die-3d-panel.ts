// The 3D half of the Die View. Same `render.bin` bundle
// as the 2D view (`die-panel.ts`), extruded through the sky130 z-stack with
// three.js. Mirrors `die-panel.ts`'s structure: the DOM is built lazily once
// the bundle resolves, and disposal tears down GL resources so switching back
// to 2D does not leak them.
//
// Falls back to a plain message -- never a crash, and never breaks the 2D
// view, which has no dependency on this one -- if WebGL2 is unavailable or
// three's `WebGLRenderer` fails to initialise.

import type { RenderBundle } from "../render/bundle";
import type { Die3D } from "../render/die3d";
import { highlightBus } from "../store/highlight";
import { coneRootBus } from "../store/selection";
import { openNetMenu } from "./net-menu";
import { netTooltip } from "./net-tooltip";
import { palette } from "../theme";


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
}

export interface Die3DPanelOptions {
  bundleReady: Promise<RenderBundle>;
  /** Brings another workspace panel to the front, for the right-click menu.
   *  Same injection as the 2D view's -- see `DiePanelOptions`. */
  onFocusPanel?: (id: string) => void;
}

/** Mounts the 3D view into `container`, which must be a positioned element
 *  it can fill. Returns a handle whose `dispose` stops the render loop and
 *  releases the three.js resources. */
export function mountDie3D(
  container: HTMLElement,
  opts: Die3DPanelOptions,
): { dispose: () => void } {
  container.classList.add("die3d-panel");
  container.innerHTML = `
    <div class="die3d-wrap">
      <canvas class="die3d-canvas"></canvas>
      <div class="die3d-loading">loading render.bin&hellip;</div>
      <div class="panel-overlay die3d-layers">
        <h2>Layers</h2>
        <div class="die3d-layer-list"></div>
        <hr />
        <h2>Exploded view</h2>
        <input class="die3d-explode" type="range" min="0" max="100" value="0" />
        <hr />
        <h2>Cross-section</h2>
        <label class="die3d-section-toggle"><input type="checkbox" /> enabled</label>
        <input class="die3d-section" type="range" min="0" max="100" value="50" disabled />
        <hr />
        <button class="die3d-trace" disabled>Trace selected net</button>
        <button class="die3d-fit">Reset view</button>
        <div class="hint">drag: orbit (no limits)<br />right/middle/shift-drag: pan<br />wheel: zoom<br />F: fit view<br />hover: highlight net<br />click: select net<br />right-click: actions</div>
      </div>
      <div class="panel-overlay die3d-trace-status" hidden></div>
      <pre class="panel-overlay die3d-stats"></pre>
    </div>`;

  const canvas = container.querySelector(".die3d-canvas") as HTMLCanvasElement;
  const loading = container.querySelector(".die3d-loading") as HTMLDivElement;
  const layerList = container.querySelector(".die3d-layer-list") as HTMLDivElement;
  const explodeSlider = container.querySelector(".die3d-explode") as HTMLInputElement;
  const sectionToggle = container.querySelector(".die3d-section-toggle input") as HTMLInputElement;
  const sectionSlider = container.querySelector(".die3d-section") as HTMLInputElement;
  const traceBtn = container.querySelector(".die3d-trace") as HTMLButtonElement;
  const fitBtn = container.querySelector(".die3d-fit") as HTMLButtonElement;
  const traceStatus = container.querySelector(".die3d-trace-status") as HTMLDivElement;
  const stats = container.querySelector(".die3d-stats") as HTMLPreElement;

  let disposed = false;
  let onKeydown: ((e: KeyboardEvent) => void) | null = null;
  let rafId = 0;
  let unsubHighlight: (() => void) | null = null;
  let view: Die3D | null = null;
  let tooltip: ReturnType<typeof netTooltip> | null = null;

  opts.bundleReady
    .then(async (bundle) => {
      if (disposed) return;

      // three.js is ~800 kB of the app bundle and this is its only importer,
      // so `die3d.ts` is pulled in here rather than at the top of the file:
      // a player who never presses the 3D button never downloads it, and one
      // who does pays for it while the "loading render.bin…" message is
      // already on screen. The import above is `import type`, which Vite
      // erases -- make it a value import again and the split silently
      // disappears back into the entry chunk.
      //
      // Re-check `disposed` after the await as well as before it: the toggle
      // is a button, and 2D/3D/2D inside the download window would otherwise
      // build a view the dispose above has already run past.
      const { Die3D } = await import("../render/die3d");
      if (disposed) return;

      try {
        view = new Die3D(canvas, bundle);
      } catch (err) {
        loading.textContent =
          "3D unavailable — switch back to 2D. " +
          "(" +
          (err instanceof Error ? err.message : String(err)) +
          ")";
        return;
      }
      loading.remove();
      const v = view;
      const meter = new FpsMeter();

      for (const name of v.layerNames()) {
        const label = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = v.isLayerOn(name);
        box.addEventListener("change", () => v.setLayer(name, box.checked));
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = palette().layer[name] ?? "#fff";
        const text = document.createElement("span");
        text.textContent = name;
        label.append(box, swatch, text);
        layerList.append(label);
      }

      explodeSlider.addEventListener("input", () => {
        v.setExplode(Number(explodeSlider.value) / 100);
      });

      sectionToggle.addEventListener("change", () => {
        v.setSectionEnabled(sectionToggle.checked);
        sectionSlider.disabled = !sectionToggle.checked;
      });
      sectionSlider.addEventListener("input", () => {
        v.setSectionPosition(Number(sectionSlider.value) / 100);
      });

      fitBtn.addEventListener("click", () => v.fit());

      // Same key as the 2D view's fit. The two views are one panel with a
      // toggle between them and having F reframe one but not the other was a
      // difference the player has to remember for no reason. Ignored while a
      // text field has focus, so typing an "f" into a filter box somewhere
      // does not move the camera.
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
        v.fit();
      };
      window.addEventListener("keydown", onKeydown);

      traceBtn.addEventListener("click", () => {
        const id = v.highlightedNetId;
        if (id !== null) v.traceNet(id);
      });

      // Mirrors the 2D view: hover names and glows, click fixes on the net
      // and re-roots the Cone Walker, right-click opens the shared menu.
      tooltip = netTooltip(container.querySelector(".die3d-wrap") as HTMLElement);
      const tip = tooltip;

      v.onNetHover = (hover) => {
        if (!hover) {
          highlightBus.set(null);
          tip.hide();
          return;
        }
        highlightBus.set({ name: hover.hit.name });
        tip.show(hover.x, hover.y, hover.hit.name);
      };

      v.onNetPick = (hit) => {
        highlightBus.pin(hit ? { name: hit.name } : null);
        if (hit) coneRootBus.open(hit.name);
      };

      v.onNetContext = (x, y, hit) => {
        if (hit) highlightBus.pin({ name: hit.name });
        openNetMenu(x, y, hit?.name ?? null, {
          onFocusPanel: opts.onFocusPanel,
          // The layer-by-layer sweep is this view's alone, so it is offered
          // where the pick happened as well as on the button in the corner.
          extra: hit
            ? [{ label: "Trace net through the stack", onSelect: () => v.traceNet(hit.id) }]
            : [],
        });
      };

      unsubHighlight = highlightBus.subscribe((sel) => {
        const id = sel ? v.netIdOf(sel.name) : null;
        v.setHighlightNet(id);
        traceBtn.disabled = id === null;
      });
      traceBtn.disabled = v.highlightedNetId === null;

      function frame(now: number): void {
        // Same reasoning as the 2D view: an inactive tab's canvas is detached
        // from the document, and the extruded stack is much the more
        // expensive of the two to draw into nothing.
        if (!canvas.isConnected) {
          rafId = requestAnimationFrame(frame);
          return;
        }
        v.render();
        meter.tick(now);
        // The trace read-out says which net the camera is following, which is
        // gameplay, not diagnostics -- so it lives outside the stats block
        // and stays visible when debug info is off.
        const traceLine = v.traceStatus();
        traceStatus.textContent = traceLine ?? "";
        traceStatus.hidden = traceLine === null;
        stats.textContent = [
          `${bundle.header.top} (3D, LOD1)`,
          `${v.instanceCount()} boxes instanced`,
          `${meter.fps.toFixed(1)} fps`,
        ].join("\n");
        rafId = requestAnimationFrame(frame);
      }
      rafId = requestAnimationFrame(frame);
    })
    .catch((err) => {
      loading.textContent = `ERROR: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
      console.error(err);
    });

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(rafId);
      if (onKeydown) window.removeEventListener("keydown", onKeydown);
      onKeydown = null;
      unsubHighlight?.();
      tooltip?.dispose();
      tooltip = null;
      view?.dispose();
      view = null;
      container.classList.remove("die3d-panel");
      container.replaceChildren();
    },
  };
}