// The Die View panel: one panel, two renderers, a `[2D | 3D]` toggle between
// them -- the control game-plan.md §4.1 draws inside the die view itself.
// They read the same `render.bin` and cross-highlight through the same net
// bus, so which one is on screen is a view preference, not a different
// workspace tab.
//
// Only the selected renderer is mounted. Switching disposes the other,
// which is what keeps a hidden view from holding a WebGL context and a
// requestAnimationFrame loop for a canvas nobody is looking at.
//
// The stats read-out (fps, draw calls, rect counts) is diagnostics, not
// gameplay, so it is off unless the player asks for it. Both the mode and
// the debug toggle persist, because re-choosing them every session is the
// kind of friction nobody notices themselves doing.

import { mountDie2D, type DiePanelOptions } from "./die-panel";
import { mountDie3D } from "./die-3d-panel";
import type { PanelDef } from "../workspace/workspace";

const MODE_KEY = "gdsx.dieview.mode";
const DEBUG_KEY = "gdsx.dieview.debug";

type Mode = "2d" | "3d";

function storedMode(): Mode {
  return localStorage.getItem(MODE_KEY) === "3d" ? "3d" : "2d";
}

function storedDebug(): boolean {
  return localStorage.getItem(DEBUG_KEY) === "1";
}

export function dieViewPanel(opts: DiePanelOptions): PanelDef {
  return {
    id: "die-view",
    title: "Die View",
    render(container: HTMLElement) {
      container.classList.add("die-view-panel");
      container.innerHTML = `
        <div class="die-modebar">
          <div class="die-mode-seg">
            <button type="button" data-mode="2d">2D</button>
            <button type="button" data-mode="3d">3D</button>
          </div>
          <div class="die-modebar-spacer"></div>
          <label class="die-debug-toggle" title="frame rate, draw calls, rect counts">
            <input type="checkbox" /> debug info
          </label>
        </div>
        <div class="die-mode-host"></div>`;

      const bar = container.querySelector(".die-mode-seg") as HTMLDivElement;
      const buttons = Array.from(bar.querySelectorAll("button")) as HTMLButtonElement[];
      const debugBox = container.querySelector(
        ".die-debug-toggle input",
      ) as HTMLInputElement;
      const host = container.querySelector(".die-mode-host") as HTMLDivElement;

      let mode = storedMode();
      let mounted: { dispose: () => void } | null = null;

      function applyDebug(on: boolean): void {
        // A class on the panel root rather than per-view wiring: both
        // renderers give their stats block the same `.panel-overlay` marker,
        // so one CSS rule covers whichever is mounted, including one mounted
        // later.
        container.classList.toggle("die-debug-on", on);
        debugBox.checked = on;
      }

      function show(next: Mode): void {
        mounted?.dispose();
        mode = next;
        for (const b of buttons) b.classList.toggle("on", b.dataset.mode === next);
        // `DiePanelOptions` is a superset of `Die3DPanelOptions`, so both
        // renderers take the same options object -- including the panel-focus
        // callback their shared right-click menu navigates with.
        mounted = next === "2d" ? mountDie2D(host, opts) : mountDie3D(host, opts);
      }

      for (const b of buttons) {
        b.addEventListener("click", () => {
          const next = b.dataset.mode as Mode;
          if (next === mode) return;
          try {
            localStorage.setItem(MODE_KEY, next);
          } catch {
            // Private mode or a full quota: the toggle still works, it just
            // won't be remembered next session.
          }
          show(next);
        });
      }

      debugBox.addEventListener("change", () => {
        applyDebug(debugBox.checked);
        try {
          localStorage.setItem(DEBUG_KEY, debugBox.checked ? "1" : "0");
        } catch {
          // As above.
        }
      });

      applyDebug(storedDebug());
      show(mode);

      return {
        dispose() {
          mounted?.dispose();
          mounted = null;
        },
      };
    },
  };
}