// The Sensitivity panel (game-plan.md §4.10, needs L33): state elements down
// the side, cycles across the top, a mark where a perturbation at that cycle
// changes that element.
//
// This is deliberately NOT a second sensitivity engine. `web/src/experiments/
// recipes.ts` already ports `gdsx.sim.sensitivity` to TypeScript for
// interactive speed (see that file's own header for why: a 121x92 sweep in
// Pyodide would make the most-used measurement in the game a batch job), and
// `experiment-panel.ts` already draws exactly this matrix -- rows are pulse
// cycles, columns are watched elements. This panel is that same machine, run
// with the recipe fixed to `"single-pulse"` and the axes transposed, because
// that is the orientation game-plan.md asks for *here*. The two self-checks
// (an element sensitive to no cycle; a cycle affecting no element) are
// `result.warnings`, already computed there -- just surfaced at the top
// rather than left under the matrix.
//
// Rows are selectable: clicking an element sends its hit-cycle list to the
// Constraints panel (§4.13) via `constraintsInbox`, which is the whole point
// of drawing this matrix in the first place -- the slot structure becomes a
// row someone can turn into a constraint.

import { highlightBus } from "../store/highlight.ts";
import { constraintsInbox } from "../store/constraints-inbox.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { SimStore } from "../sim/store.ts";
import {
  type ExperimentContext,
  type ExperimentResult,
} from "../experiments/recipes.ts";
import { SweepClient } from "../experiments/client.ts";

const CELL_W = 9;
const CELL_H = 12;
const GUTTER_W = 130;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

export interface SensitivityPanelOptions {
  storeReady: Promise<SimStore>;
  tapeUrl: string;
}

export function sensitivityPanel(options: SensitivityPanelOptions): PanelDef {
  return {
    id: "sensitivity",
    title: "Sensitivity",
    render(container: HTMLElement) {
      container.classList.add("sv-panel");
      container.innerHTML = `
        <div class="sv-toolbar">
          <label class="sv-inline">pulse <select class="sv-port"></select></label>
          <label class="sv-inline">cycles <input type="number" class="sv-from" value="0" /> to <input type="number" class="sv-to" /></label>
          <button type="button" class="sv-run" disabled>▶ run</button>
          <span class="sv-status"></span>
          <span class="py-call-slot"></span>
        </div>
        <div class="sv-loading">waiting on the gate tape…</div>
        <div class="sv-body" hidden>
          <div class="sv-warnings"></div>
          <div class="sv-readout">hover a cell — click a row to send it to the Constraints panel</div>
          <div class="sv-canvas-wrap"><canvas class="sv-canvas"></canvas></div>
        </div>`;

      const portBox = container.querySelector(".sv-port") as HTMLSelectElement;
      const fromInput = container.querySelector(".sv-from") as HTMLInputElement;
      const toInput = container.querySelector(".sv-to") as HTMLInputElement;
      const runBtn = container.querySelector(".sv-run") as HTMLButtonElement;
      const statusEl = container.querySelector(".sv-status") as HTMLSpanElement;
      const loadingEl = container.querySelector(".sv-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".sv-body") as HTMLDivElement;
      const warnEl = container.querySelector(".sv-warnings") as HTMLDivElement;
      const readoutEl = container.querySelector(".sv-readout") as HTMLDivElement;
      const canvas = container.querySelector(".sv-canvas") as HTMLCanvasElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      const sweeps = new SweepClient(options.tapeUrl);
      let store: SimStore | null = null;
      let result: ExperimentResult | null = null;
      let elements: string[] = []; // rows, was result.columns
      let cycles: number[] = []; // columns, was result.rows (as cycle numbers)
      let selectedRow: number | null = null;
      let hovered: { row: number; column: number } | null = null;
      let running = false;
      let disposed = false;

      attachPythonCallButton(callSlot, () => result?.call ?? null);

      function contextOf(): ExperimentContext {
        const s = store!;
        const keyPort = portBox.value || s.inputPorts[0];
        const baseVector: Record<string, number> = {};
        for (const port of s.inputPorts) baseVector[port] = 0;
        baseVector[keyPort] = 0;
        return {
          tape: s.tape,
          resetVector: { ...s.resetVector },
          inputPorts: s.inputPorts,
          cycles: s.cycles,
          baseVector,
          keyPort,
          baselineLabel: `idle: every port at 0, ${keyPort} pulsed one cycle at a time`,
          baseline: new Map(),
        };
      }

      async function go(): Promise<void> {
        if (!store || running) return;
        const ctx = contextOf();
        const from = Math.max(0, Math.round(Number(fromInput.value) || 0));
        const to = Math.min(ctx.cycles, Math.round(Number(toInput.value) || ctx.cycles));
        if (to <= from) {
          statusEl.textContent = "the window is empty — check the cycle range";
          statusEl.className = "sv-status bad";
          return;
        }
        running = true;
        runBtn.disabled = true;
        statusEl.className = "sv-status";
        statusEl.textContent = "running…";
        selectedRow = null;
        try {
          result = await sweeps.run(
            ctx,
            "single-pulse",
            { from, to, firstPulse: 0, minGap: 1, maxGap: 1, watch: [...ctx.tape.header.flop_names] },
            (fraction) => {
              statusEl.textContent = `running… ${Math.round(fraction * 100)}%`;
            },
          );
          elements = result.columns;
          cycles = Array.from({ length: result.rows.length }, (_, i) => from + i);
          statusEl.textContent =
            `${elements.length} elements × ${cycles.length} cycles · ${result.traces} traces in ${result.ms.toFixed(0)} ms`;
          renderWarnings();
          draw();
        } catch (err) {
          statusEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
          statusEl.className = "sv-status bad";
        } finally {
          running = false;
          runBtn.disabled = false;
        }
      }

      function renderWarnings(): void {
        warnEl.replaceChildren();
        if (!result) return;
        for (const warning of result.warnings) {
          warnEl.append(el("div", "sv-warning", `⚠ ${warning}`));
        }
        warnEl.append(el("div", "sv-baseline-note", `measured against ${result.baselineLabel}`));
      }

      function cellHit(elementIdx: number, cycleIdx: number): boolean {
        if (!result) return false;
        return Boolean(result.rows[cycleIdx]?.cells[elementIdx]);
      }

      function hitsFor(elementIdx: number): number[] {
        const hits: number[] = [];
        for (let c = 0; c < cycles.length; c++) if (cellHit(elementIdx, c)) hits.push(cycles[c]);
        return hits;
      }

      function draw(): void {
        if (!result) return;
        const dpr = window.devicePixelRatio || 1;
        const width = GUTTER_W + cycles.length * CELL_W;
        const height = elements.length * CELL_H;
        canvas.width = Math.ceil(width * dpr);
        canvas.height = Math.ceil(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;

        const ctx2d = canvas.getContext("2d")!;
        ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx2d.clearRect(0, 0, width, height);
        ctx2d.font = "10px ui-monospace, Menlo, monospace";
        ctx2d.textBaseline = "middle";

        for (let r = 0; r < elements.length; r++) {
          const y = r * CELL_H;
          ctx2d.fillStyle = r === selectedRow ? "#1a2130" : r % 2 ? "#10131a" : "#0d0f14";
          ctx2d.fillRect(0, y, width, CELL_H);
          ctx2d.fillStyle = r === selectedRow ? "#cfe0ff" : "#9aa2b5";
          ctx2d.fillText(elements[r], 4, y + CELL_H / 2);
          for (let c = 0; c < cycles.length; c++) {
            if (!cellHit(r, c)) continue;
            ctx2d.fillStyle = "#4d9be0";
            ctx2d.fillRect(GUTTER_W + c * CELL_W + 1, y + 1, CELL_W - 2, CELL_H - 2);
          }
        }
        if (hovered) {
          ctx2d.strokeStyle = "#f2cc4d";
          ctx2d.lineWidth = 1;
          ctx2d.strokeRect(
            GUTTER_W + hovered.column * CELL_W + 0.5,
            hovered.row * CELL_H + 0.5,
            CELL_W - 1,
            CELL_H - 1,
          );
        }
      }

      function cellAt(event: PointerEvent | MouseEvent): { row: number; column: number } | null {
        if (!result) return null;
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left - GUTTER_W;
        const y = event.clientY - rect.top;
        const column = Math.floor(x / CELL_W);
        const row = Math.floor(y / CELL_H);
        if (row < 0 || row >= elements.length) return null;
        return { row, column };
      }

      canvas.addEventListener("pointermove", (event) => {
        const at = cellAt(event);
        hovered = at && at.column >= 0 && at.column < cycles.length ? at : null;
        if (!at) {
          readoutEl.textContent = "hover a cell — click a row to send it to the Constraints panel";
          draw();
          return;
        }
        const element = elements[at.row];
        highlightBus.set({ name: element });
        if (hovered) {
          readoutEl.textContent = `${element} × cycle ${cycles[at.column]} — ${cellHit(at.row, at.column) ? "moved" : "unchanged"}`;
        } else {
          readoutEl.textContent = `${element} — click to send its hit cycles to Constraints`;
        }
        draw();
      });
      canvas.addEventListener("pointerleave", () => {
        hovered = null;
        highlightBus.set(null);
        draw();
      });
      canvas.addEventListener("click", (event) => {
        const at = cellAt(event);
        if (!at) return;
        selectedRow = at.row;
        const element = elements[at.row];
        const hits = hitsFor(at.row);
        constraintsInbox.push({ name: element, elements: hits, source: "sensitivity" });
        readoutEl.textContent = `sent ${element} (${hits.length} cycles) to the Constraints panel`;
        draw();
      });

      runBtn.addEventListener("click", () => void go());

      options.storeReady
        .then((s) => {
          if (disposed) return;
          store = s;
          for (const port of s.inputPorts) {
            const o = document.createElement("option");
            o.value = port;
            o.textContent = port;
            portBox.append(o);
          }
          portBox.value = s.inputPorts[0] ?? "";
          toInput.value = String(s.cycles);
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          runBtn.disabled = false;
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          sweeps.dispose();
        },
      };
    },
  };
}