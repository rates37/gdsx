// The Sequence Editor: one paintable track per input
// port. Click to toggle a cell, drag to paint a run of them, import/export
// as a bit string so a derived key round-trips with the REPL (or, today,
// with a walkthrough document).
//
// Reruns the whole simulation on every edit rather than patching history
// incrementally -- at a few hundred cycles that is comfortably sub-frame,
// and it means there is no cache to get wrong.

import type { SimStore } from "../sim/store.ts";
import { busValueAt, discoverBuses } from "../sim/store.ts";
import { cursorBus } from "../store/cursor.ts";
import type { PanelDef } from "../workspace/workspace.ts";

const CELL_W = 7;
const CELL_H = 20;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

/**
 * The bus a message would arrive on, or null if the design has none.
 *
 * The widest bus the tape carries that is not one of the driver's own input
 * ports. This used to look for the literal name `"O"` at a fixed width of 8,
 * which is what the baked puzzles happen to call their output word -- a fact
 * about the current pack rather than about the game.
 */
function messageBus(store: SimStore, inputPorts: readonly string[]): string[] | null {
  const buses = discoverBuses(store, inputPorts);
  if (buses.length === 0) return null;
  let widest = buses[0];
  for (const bus of buses) if (bus.bits.length > widest.bits.length) widest = bus;
  return widest.bits;
}

export interface SequenceEditorOptions {
  storeReady: Promise<SimStore>;
  /** The net whose latch this panel reports. From the descriptor -- it was
   *  the literal `"success"`, which is what this design happens to call it. */
  successNet: string | null;
  /** The driver's own input ports, so bus discovery does not offer the
   *  stimulus back as if it were a result. */
  trackPorts: string[];
  /**
   * The port the puzzle's driver uses as its reset, or null if it declares
   * none.
   *
   * A track per primary input is the right rule -- the reset IS an input, and
   * a puzzle may well want it pulsed -- but it is not part of anyone's answer,
   * the driver holds it at a level of its own, and painting a cell of it holds
   * the design in reset for that cycle. Unmarked, it is a row that looks
   * exactly like the one carrying the key, immediately below it.
   */
  resetPort: string | null;
}

export function sequenceEditorPanel(options: SequenceEditorOptions): PanelDef {
  const { storeReady, successNet } = options;
  return {
    id: "sequence-editor",
    title: "Sequence Editor",
    render(container: HTMLElement) {
      container.classList.add("seq-panel");
      container.innerHTML = `
        <div class="seq-toolbar">
          <span class="seq-cycles-label">cycles</span>
          <input class="seq-cycles" type="number" min="1" />
          <button class="seq-extend" type="button">+20</button>
          <span class="seq-toolbar-sep"></span>
          <label class="seq-autorun" title="rerun the simulation automatically on every edit">
            <input type="checkbox" class="seq-autorun-cb" checked /> auto-run
          </label>
          <button class="seq-run-btn" type="button" title="recompute the trace now">▶ Run</button>
          <span class="seq-run-status"></span>
          <span class="seq-result"></span>
        </div>
        <div class="seq-loading">waiting on the gate tape…</div>
        <div class="seq-body" hidden>
          <div class="seq-tracks"></div>
        </div>`;

      const cyclesInput = container.querySelector(".seq-cycles") as HTMLInputElement;
      const extendBtn = container.querySelector(".seq-extend") as HTMLButtonElement;
      const autorunCb = container.querySelector(".seq-autorun-cb") as HTMLInputElement;
      const runBtn = container.querySelector(".seq-run-btn") as HTMLButtonElement;
      const runStatusEl = container.querySelector(".seq-run-status") as HTMLSpanElement;
      const resultEl = container.querySelector(".seq-result") as HTMLSpanElement;
      const loadingEl = container.querySelector(".seq-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".seq-body") as HTMLDivElement;
      const tracksEl = container.querySelector(".seq-tracks") as HTMLDivElement;

      let disposed = false;
      let store: SimStore | null = null;
      let unsubStore: (() => void) | null = null;
      let unsubCursor: (() => void) | null = null;

      function ruler(cycles: number): HTMLElement {
        const row = el("div", "seq-ruler");
        row.style.width = `${cycles * CELL_W}px`;
        for (let c = 0; c < cycles; c += 10) {
          const tick = el("span", "seq-tick", String(c));
          tick.style.left = `${c * CELL_W}px`;
          row.append(tick);
        }
        return row;
      }

      function buildTrack(port: string): { root: HTMLElement; cells: HTMLElement } {
        const isReset = port === options.resetPort;
        const root = el("div", "seq-track" + (isReset ? " seq-track-reset" : ""));
        const header = el("div", "seq-track-header");
        header.append(el("span", "seq-track-name", port));
        // Named, not disabled: pulsing a reset is a legitimate thing to want
        // to do, and a control the panel refuses to operate teaches nothing.
        // What it must not be is indistinguishable from the track carrying
        // the key.
        //
        // Drawn on every track and made invisible on the rest, so the tag
        // reserves its own column rather than shunting one track's import and
        // export buttons out of line with its neighbours'.
        const tag = el("span", "seq-track-tag", "reset");
        if (isReset) {
          tag.title =
            `${port} is this puzzle's reset line, held by the driver. It is not part of ` +
            `the answer, and a cell you paint here holds the design in reset for that cycle.`;
        } else {
          tag.classList.add("seq-track-tag-blank");
          tag.setAttribute("aria-hidden", "true");
        }
        header.append(tag);
        const io = el("input", "seq-track-io") as HTMLInputElement;
        io.type = "text";
        io.placeholder = "bit string…";
        const importBtn = el("button", "seq-track-btn", "import") as HTMLButtonElement;
        importBtn.type = "button";
        const exportBtn = el("button", "seq-track-btn", "export") as HTMLButtonElement;
        exportBtn.type = "button";
        importBtn.addEventListener("click", () => store?.setPattern(port, io.value));
        exportBtn.addEventListener("click", () => {
          io.value = Array.from(store?.bitsOf(port) ?? []).join("");
          io.select();
          navigator.clipboard?.writeText(io.value).catch(() => {});
        });
        header.append(io, importBtn, exportBtn);
        root.append(header);

        const cells = el("div", "seq-cells");
        let painting = false;
        let paintValue: 0 | 1 = 1;
        const cellAt = (clientX: number): number => {
          const rect = cells.getBoundingClientRect();
          return Math.max(0, Math.min(store!.cycles - 1, Math.floor((clientX - rect.left) / CELL_W)));
        };
        cells.addEventListener("pointerdown", (e) => {
          if (!store) return;
          const idx = cellAt(e.clientX);
          paintValue = store.bitsOf(port)[idx] ? 0 : 1;
          painting = true;
          cells.setPointerCapture(e.pointerId);
          store.setBit(port, idx, paintValue);
          cursorBus.set(idx);
        });
        cells.addEventListener("pointermove", (e) => {
          if (!painting || !store) return;
          const idx = cellAt(e.clientX);
          store.setBit(port, idx, paintValue);
        });
        cells.addEventListener("pointerup", () => {
          painting = false;
        });
        root.append(cells);
        return { root, cells };
      }

      const trackCells = new Map<string, HTMLElement>();

      function renderTracks(): void {
        if (!store) return;
        tracksEl.replaceChildren();
        trackCells.clear();
        tracksEl.append(ruler(store.cycles));
        for (const port of store.inputPorts) {
          const { root, cells } = buildTrack(port);
          tracksEl.append(root);
          trackCells.set(port, cells);
        }
        renderCells();
      }

      function renderCells(): void {
        if (!store) return;
        const cursor = cursorBus.get();
        for (const [port, cells] of trackCells) {
          const bits = store.bitsOf(port);
          cells.style.width = `${store.cycles * CELL_W}px`;
          cells.replaceChildren();
          for (let c = 0; c < bits.length; c++) {
            const cell = el("span", "seq-cell" + (bits[c] ? " on" : ""));
            cell.style.left = `${c * CELL_W}px`;
            cell.style.width = `${CELL_W}px`;
            cell.style.height = `${CELL_H}px`;
            if (c === cursor) cell.classList.add("cursor");
            if (c % 11 === 0) cell.classList.add("epoch");
            cells.append(cell);
          }
        }
      }

      function renderResult(): void {
        if (!store) return;
        const stale = store.isDirty();
        // A puzzle with no lock has nothing for this line to report.
        const latch = successNet === null ? null : store.firstLatchedHigh(successNet);
        const bus = messageBus(store, options.trackPorts);
        if (latch === null) {
          const said = successNet === null ? "no lock to latch" : `${successNet}: not latched`;
          resultEl.textContent = stale ? `(stale) ${said}` : said;
          resultEl.className = stale ? "seq-result stale" : "seq-result";
          return;
        }
        // Some puzzles stream a message on `O` once the lock opens, one byte
        // per cycle. Most do not -- `O` is just as often a counter or a shift
        // register, and decoding one of those as text produces control-code
        // noise sitting next to the thing the player actually wanted to read.
        // So the bytes have to *look* like a message before they are shown as
        // one: printable, and more than one of them.
        let message = "";
        if (bus) {
          for (let c = latch; c < store.cycles; c++) {
            const byte = busValueAt(store, c, bus);
            if (byte === 0 && c > latch) break;
            if (byte < 32 || byte > 126) {
              message = "";
              break;
            }
            message += String.fromCharCode(byte);
          }
        }
        if (message.length < 2) message = "";
        resultEl.textContent =
          `${stale ? "(stale) " : ""}${successNet} latches at cycle ${latch}` +
          (message ? `  →  ${JSON.stringify(message)}` : "");
        resultEl.className = stale ? "seq-result latched stale" : "seq-result latched";
      }

      function renderRunStatus(): void {
        if (!store) return;
        if (store.isDirty()) {
          runStatusEl.textContent = "● changes pending";
          runStatusEl.className = "seq-run-status dirty";
        } else {
          runStatusEl.textContent = `✓ ran ${store.cycles} cycles in ${store.lastRunMs.toFixed(1)} ms`;
          runStatusEl.className = "seq-run-status";
        }
      }

      cyclesInput.addEventListener("change", () => {
        const n = Math.max(1, Math.round(Number(cyclesInput.value) || 1));
        store?.setCycles(n);
      });
      extendBtn.addEventListener("click", () => {
        if (store) store.setCycles(store.cycles + 20);
      });
      autorunCb.addEventListener("change", () => store?.setAutoRun(autorunCb.checked));
      runBtn.addEventListener("click", () => store?.recompute());

      storeReady
        .then((s) => {
          if (disposed) return;
          store = s;
          cyclesInput.value = String(s.cycles);
          autorunCb.checked = s.autoRun;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          renderTracks();
          renderResult();
          renderRunStatus();
          unsubStore = s.subscribe(() => {
            cyclesInput.value = String(s.cycles);
            renderCells();
            renderResult();
            renderRunStatus();
          });
          unsubCursor = cursorBus.subscribe(() => renderCells());
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          unsubStore?.();
          unsubCursor?.();
        },
      };
    },
  };
}