// The Waveform panel (game-plan.md §4.6): a watch list over any net or flop,
// backed directly by `SimStore`'s history so scrubbing is an array lookup,
// not a re-simulation -- the whole point of compiling to a tape in the first
// place. Indexed nets (`O[0]`..`O[7]`) can be watched as one grouped bus row
// instead of eight separate 0/1 traces, in a choice of radix.
//
// Two things about the cursor, because both were expensive to work without:
//
// - **The cycle field and the canvas click are two views of one cursor.** The
//   cursor lives in `cursorBus` and nowhere else; the field is rendered from
//   it on every change and never holds its own idea of the value. Clicking a
//   trace, typing a number, arrowing left or right and another panel moving
//   the cursor all take the same path, so there is nothing for the two to
//   disagree about and no echo to break. The field also has to exist because
//   the canvas is not always there: with every trace closed the panel used to
//   have no way to move the cursor at all, while the Cone Walker's flatten
//   drawer went on evaluating its ✓/✗ at whatever the cursor last was.
// - **Every row prints its value at the cursor.** "What is this flop at this
//   cycle" is the most repeated question in a solve, and the panel drew the
//   answer as a waveform without ever stating it.
//
// Not built here: clicking a cycle driving a die-view heat overlay of every
// high net (§4.6's third bullet). That needs the die view to highlight a
// *set* of nets, not the single selection it supports today -- a real
// extension, scoped out of this pass. Hovering a watched net's name still
// cross-highlights it via the same bus the die view and netlist browser use.

import type { SimStore } from "../sim/store.ts";
import { busValueAt } from "../sim/store.ts";
import { cursorBus } from "../store/cursor.ts";
import { labels } from "../store/labels.ts";
import { instanceChip, netChip } from "./chips.ts";
import type { PanelDef } from "../workspace/workspace.ts";

const ROW_H = 28;
const MIN_CELL_W = 2;
const MAX_CELL_W = 36;
const DEFAULT_CELL_W = 6;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

type WatchKind = "net" | "flop" | "bus";
type Radix = "hex" | "dec" | "bin" | "ascii";

interface WatchItem {
  kind: WatchKind;
  label: string;
  /** net name (kind=net), flop instance name (kind=flop), or bit-net names lsb..msb (kind=bus) */
  ref: string | string[];
  /** kind=bus only. */
  radix: Radix;
}

function busPrefix(name: string): string | null {
  const m = /^(.+)\[0\]$/.exec(name);
  return m ? m[1] : null;
}

/** A bus value formatted in the row's chosen radix. `bits` is the field width, for binary padding. */
function formatValue(value: number, bits: number, radix: Radix): string {
  switch (radix) {
    case "hex":
      return `0x${value.toString(16)}`;
    case "dec":
      return String(value);
    case "bin":
      return `0b${value.toString(2).padStart(bits, "0")}`;
    case "ascii":
      return value >= 32 && value <= 126
        ? `'${String.fromCharCode(value)}'`
        : `\\x${value.toString(16).padStart(2, "0")}`;
  }
}

export function waveformPanel(storeReady: Promise<SimStore>): PanelDef {
  return {
    id: "waveform",
    title: "Waveform",
    render(container: HTMLElement) {
      container.classList.add("wave-panel");
      container.innerHTML = `
        <div class="wave-toolbar">
          <input class="wave-search" type="text" placeholder="add net or flop…" />
          <div class="wave-suggest" hidden></div>
          <div class="wave-toolbar-right">
            <label class="wave-cursor" title="the shared cursor — ← and → step it, shift for ten">
              cycle
              <input class="wave-cycle" type="number" min="0" step="1" value="0" />
            </label>
            <div class="wave-zoom">
              <button class="wave-zoom-out" type="button" title="zoom out">−</button>
              <button class="wave-zoom-reset" type="button" title="reset zoom">⟲</button>
              <button class="wave-zoom-in" type="button" title="zoom in">+</button>
            </div>
          </div>
        </div>
        <div class="wave-loading">waiting on the gate tape…</div>
        <div class="wave-body" hidden>
          <div class="wave-ruler-row">
            <div class="wave-label-spacer"></div>
            <div class="wave-ruler"></div>
          </div>
          <div class="wave-rows"></div>
        </div>`;

      const searchEl = container.querySelector(".wave-search") as HTMLInputElement;
      const suggestEl = container.querySelector(".wave-suggest") as HTMLDivElement;
      const zoomOutBtn = container.querySelector(".wave-zoom-out") as HTMLButtonElement;
      const zoomInBtn = container.querySelector(".wave-zoom-in") as HTMLButtonElement;
      const zoomResetBtn = container.querySelector(".wave-zoom-reset") as HTMLButtonElement;
      const cycleInput = container.querySelector(".wave-cycle") as HTMLInputElement;
      const loadingEl = container.querySelector(".wave-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".wave-body") as HTMLDivElement;
      const rulerEl = container.querySelector(".wave-ruler") as HTMLDivElement;
      const rowsEl = container.querySelector(".wave-rows") as HTMLDivElement;

      let disposed = false;
      let store: SimStore | null = null;
      let watch: WatchItem[] = [];
      let cellW = DEFAULT_CELL_W;
      let unsubStore: (() => void) | null = null;
      let unsubCursor: (() => void) | null = null;
      /**
       * Where the cursor LINE is painted, measured in cycles and fractional:
       * dragging puts it wherever the pointer is, including mid-cycle.
       *
       * This is a paint position, not a second cursor. The cursor is the whole
       * cycle the line sits inside, it lives in `cursorBus`, and it is what the
       * field, the value column and every other panel read -- a run has one
       * value per cycle, so there is nothing between 11 and 12 to report. Any
       * move that does not come from this panel's own pointer snaps the line
       * back onto the cycle it was given.
       */
      let cursorX = 0;
      let movingSelf = false;

      function setZoom(next: number): void {
        cellW = Math.max(MIN_CELL_W, Math.min(MAX_CELL_W, next));
        redrawAll();
      }

      /** The cycle a paint position falls in, clamped to the run: the history
       *  arrays past its end read as 0 rather than as "no such cycle", and a
       *  value column quietly showing zeroes off the end of the run is exactly
       *  the kind of reading that costs an hour. */
      function cycleAt(x: number): number {
        const last = store ? store.cycles - 1 : 0;
        return Math.max(0, Math.min(last, Math.floor(x)));
      }

      /** The one way this panel moves the cursor: put the line at `x` (in
       *  cycles) and hand the cycle it lands in to the bus. */
      function placeCursor(x: number): void {
        const end = store ? store.cycles : 1;
        cursorX = Math.max(0, Math.min(end, x));
        const cycle = cycleAt(cursorX);
        const moved = cursorBus.get() !== cycle;
        movingSelf = true;
        cursorBus.set(cycle);
        movingSelf = false;
        // `cursorBus.set` is a no-op when the cycle has not changed, so a drag
        // within one cycle -- or a clamped edit -- still has to repaint the
        // line and pull the field back into line with the cursor.
        syncCursorField();
        if (!moved) redrawAll();
      }

      /** Move to a whole cycle: the field, the arrow keys, a clamp. */
      function setCursor(cycle: number): void {
        const last = store ? store.cycles - 1 : 0;
        placeCursor(Math.max(0, Math.min(last, Math.round(cycle))));
      }

      /** The field is a rendering of the cursor, never a second copy of it. */
      function syncCursorField(): void {
        const text = String(cursorBus.get());
        if (cycleInput.value !== text) cycleInput.value = text;
      }

      cycleInput.addEventListener("input", () => {
        // Mid-edit emptiness is not cycle 0: leave the cursor alone until
        // there is a number to read.
        if (cycleInput.value.trim() === "") return;
        setCursor(Number(cycleInput.value));
      });
      cycleInput.addEventListener("blur", syncCursorField);

      // Arrow stepping for the panel, not for the field: inside an input the
      // arrows already mean "move the caret", and inside the number field the
      // browser's own up/down stepping is the better behaviour.
      container.tabIndex = 0;
      container.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        const target = e.target as HTMLElement | null;
        if (
          target instanceof HTMLInputElement ||
          target instanceof HTMLSelectElement ||
          target instanceof HTMLTextAreaElement
        ) {
          return;
        }
        e.preventDefault();
        const step = (e.shiftKey ? 10 : 1) * (e.key === "ArrowRight" ? 1 : -1);
        setCursor(cursorBus.get() + step);
      });

      zoomOutBtn.addEventListener("click", () => setZoom(cellW - 2));
      zoomInBtn.addEventListener("click", () => setZoom(cellW + 2));
      zoomResetBtn.addEventListener("click", () => setZoom(DEFAULT_CELL_W));
      // Horizontal zoom on ctrl/cmd+wheel, the usual waveform-viewer gesture --
      // plain wheel still scrolls `wave-body` (ruler and rows share that one
      // scroll container, which is what keeps them in lockstep horizontally).
      bodyEl.addEventListener(
        "wheel",
        (e) => {
          if (!e.ctrlKey && !e.metaKey) return;
          e.preventDefault();
          setZoom(cellW - Math.sign(e.deltaY) * 2);
        },
        { passive: false },
      );

      function candidates(query: string): { label: string; add: () => void }[] {
        if (!store || query.length === 0) return [];
        const q = query.toLowerCase();
        const out: { label: string; add: () => void }[] = [];
        const seenBus = new Set<string>();
        for (const name of Object.keys(store.tape.header.names)) {
          if (out.length >= 25) break;
          const prefix = busPrefix(name);
          if (prefix && prefix.toLowerCase().includes(q) && !seenBus.has(prefix)) {
            seenBus.add(prefix);
            out.push({
              label: `${prefix}[…] (bus)`,
              add: () => addBus(prefix),
            });
            continue;
          }
          if (!prefix && (name.toLowerCase().includes(q) || labels.matches("net", name, q))) {
            out.push({
              label: labels.has("net", name) ? `${labels.display("net", name)}  ${name}` : name,
              add: () => addWatch({ kind: "net", label: name, ref: name, radix: "hex" }),
            });
          }
        }
        for (const name of store.tape.header.flop_names) {
          if (out.length >= 30) break;
          if (name.toLowerCase().includes(q) || labels.matches("instance", name, q)) {
            out.push({
              label: `${labels.display("instance", name)} (flop)`,
              add: () => addWatch({ kind: "flop", label: name, ref: name, radix: "hex" }),
            });
          }
        }
        return out;
      }

      function addBus(prefix: string): void {
        if (!store) return;
        const bits: string[] = [];
        for (let i = 0; ; i++) {
          const name = `${prefix}[${i}]`;
          if (!(name in store.tape.header.names)) break;
          bits.push(name);
        }
        if (bits.length) {
          addWatch({ kind: "bus", label: `${prefix}[${bits.length - 1}:0]`, ref: bits, radix: "hex" });
        }
      }

      function addWatch(item: WatchItem): void {
        if (watch.some((w) => w.label === item.label)) return;
        watch.push(item);
        searchEl.value = "";
        suggestEl.hidden = true;
        renderRows();
      }

      searchEl.addEventListener("input", () => {
        const found = candidates(searchEl.value.trim());
        suggestEl.replaceChildren();
        suggestEl.hidden = found.length === 0;
        for (const c of found) {
          const item = el("div", "wave-suggest-item", c.label);
          item.addEventListener("click", c.add);
          suggestEl.append(item);
        }
      });
      searchEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const found = candidates(searchEl.value.trim());
          found[0]?.add();
        } else if (e.key === "Escape") {
          suggestEl.hidden = true;
        }
      });

      function drawTrace(canvas: HTMLCanvasElement, item: WatchItem): void {
        if (!store) return;
        const cycles = store.cycles;
        const cssW = cycles * cellW;
        const cssH = ROW_H;
        // The backing store must be `dpr` pixels per CSS pixel or a Retina
        // display upscales a 1x bitmap and every trace looks soft -- the
        // same fix the die view and minimap already need. Drawing code below
        // stays in CSS-pixel units; the transform does the scaling.
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        canvas.style.width = `${cssW}px`;
        canvas.style.height = `${cssH}px`;
        const ctx = canvas.getContext("2d")!;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, cssW, cssH);

        // Decade gridlines, for visual alignment with the sequence editor.
        ctx.strokeStyle = "#1c2130";
        ctx.lineWidth = 1;
        for (let c = 0; c < cycles; c += 10) {
          ctx.beginPath();
          ctx.moveTo(c * cellW + 0.5, 0);
          ctx.lineTo(c * cellW + 0.5, ROW_H);
          ctx.stroke();
        }

        if (item.kind === "bus") {
          drawBus(ctx, item.ref as string[], cycles, item.radix);
        } else {
          drawBit(ctx, item, cycles);
        }

        // Painted where the pointer left it, not snapped to the cell edge.
        const x = Math.round(cursorX * cellW) + 0.5;
        ctx.strokeStyle = "#f2cc4d";
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, ROW_H);
        ctx.stroke();
      }

      function drawBit(ctx: CanvasRenderingContext2D, item: WatchItem, cycles: number): void {
        const top = 5;
        const bottom = ROW_H - 5;
        const yFor = (v: number) => (v ? top : bottom);
        const at = (c: number) =>
          item.kind === "flop"
            ? store!.flopValueAt(c, item.ref as string)
            : store!.netValueAt(c, item.ref as string);
        ctx.strokeStyle = "#9fe39f";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        let x = 0;
        let y = yFor(at(0));
        ctx.moveTo(x, y);
        for (let c = 0; c < cycles; c++) {
          const ny = yFor(at(c));
          if (ny !== y) {
            ctx.lineTo(x, ny);
            y = ny;
          }
          x += cellW;
          ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      function drawBus(ctx: CanvasRenderingContext2D, bits: string[], cycles: number, radix: Radix): void {
        const top = 5;
        const bottom = ROW_H - 5;
        ctx.strokeStyle = "#8ecdf7";
        ctx.fillStyle = "#8ecdf7";
        ctx.font = "10px ui-monospace, monospace";
        let segStart = 0;
        let segValue = busValueAt(store!, 0, bits);
        const closeSegment = (end: number) => {
          const x0 = segStart * cellW;
          const x1 = end * cellW;
          ctx.strokeRect(x0 + 0.5, top, Math.max(1, x1 - x0 - 1), bottom - top);
          const text = formatValue(segValue, bits.length, radix);
          const textW = ctx.measureText(text).width;
          if (x1 - x0 >= textW + 6) {
            ctx.fillText(text, x0 + 3, ROW_H / 2 + 3, x1 - x0 - 4);
          }
        };
        for (let c = 1; c < cycles; c++) {
          const v = busValueAt(store!, c, bits);
          if (v !== segValue) {
            closeSegment(c);
            segStart = c;
            segValue = v;
          }
        }
        closeSegment(cycles);
      }

      let dragFrom: number | null = null;

      function buildRow(item: WatchItem, index: number): HTMLElement {
        const row = el("div", "wave-row");
        const label = el("div", "wave-row-label");
        // Only the label gutter is the reorder handle -- it is the part that
        // shows `cursor: grab`, and it has to be the whole draggable, because
        // a native HTML5 drag beginning anywhere in the row takes the pointer
        // away from the canvas: scrubbing died after one pointermove when the
        // row itself was draggable.
        label.draggable = true;
        label.append(el("span", "wave-drag-handle", "⋮⋮"));
        // A watched net or flop is drawn as a chip, so the name in the
        // waveform is the same name (and the same rename affordance) as
        // everywhere else. A bus has no single underlying name to label, so it
        // keeps the prefix it was added under.
        label.append(
          item.kind === "net"
            ? netChip(item.ref as string, { className: "wave-row-name", onClick: false })
            : item.kind === "flop"
              ? instanceChip(item.ref as string, { className: "wave-row-name", onClick: false })
              : el("span", "wave-row-name", item.label),
        );
        if (item.kind === "bus") {
          const radixSel = el("select", "wave-row-radix") as HTMLSelectElement;
          for (const r of ["hex", "dec", "bin", "ascii"] as const) {
            const opt = document.createElement("option");
            opt.value = r;
            opt.textContent = r;
            opt.selected = r === item.radix;
            radixSel.append(opt);
          }
          radixSel.addEventListener("click", (e) => e.stopPropagation());
          radixSel.addEventListener("change", () => {
            item.radix = radixSel.value as Radix;
            const canvas = row.querySelector("canvas") as HTMLCanvasElement | null;
            if (canvas) drawTrace(canvas, item);
            renderValues();
          });
          label.append(radixSel);
        }
        label.append(el("span", "wave-row-value"));
        const removeBtn = el("button", "wave-row-remove", "×") as HTMLButtonElement;
        removeBtn.type = "button";
        removeBtn.addEventListener("click", () => {
          watch = watch.filter((w) => w !== item);
          renderRows();
        });
        label.append(removeBtn);
        row.append(label);

        const canvas = el("canvas", "wave-row-canvas") as HTMLCanvasElement;
        // Press and drag scrubs: the line follows the pointer, and the cursor
        // is whichever cycle it is over at the time. Pointer capture is what
        // keeps a drag alive when the pointer leaves this row's canvas.
        const atPointer = (e: PointerEvent): number =>
          (e.clientX - canvas.getBoundingClientRect().left) / cellW;
        canvas.addEventListener("pointerdown", (e) => {
          canvas.setPointerCapture(e.pointerId);
          placeCursor(atPointer(e));
          // So the arrows step from where the player just clicked, without
          // scrolling the panel out from under them.
          container.focus({ preventScroll: true });
        });
        canvas.addEventListener("pointermove", (e) => {
          if (!canvas.hasPointerCapture(e.pointerId)) return;
          placeCursor(atPointer(e));
        });
        const endDrag = (e: PointerEvent): void => {
          if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
        };
        canvas.addEventListener("pointerup", endDrag);
        canvas.addEventListener("pointercancel", endDrag);
        row.append(canvas);
        drawTrace(canvas, item);

        row.addEventListener("dragstart", () => {
          dragFrom = index;
        });
        row.addEventListener("dragover", (e) => e.preventDefault());
        row.addEventListener("drop", (e) => {
          e.preventDefault();
          if (dragFrom === null || dragFrom === index) return;
          const [moved] = watch.splice(dragFrom, 1);
          watch.splice(index, 0, moved);
          dragFrom = null;
          renderRows();
        });
        return row;
      }

      function renderRuler(): void {
        if (!store) return;
        rulerEl.replaceChildren();
        rulerEl.style.width = `${store.cycles * cellW}px`;
        for (let c = 0; c < store.cycles; c += 10) {
          const tick = el("span", "wave-tick", String(c));
          tick.style.left = `${c * cellW}px`;
          rulerEl.append(tick);
        }
      }

      /** One row's value at the cursor, as text. A bus reads in its own radix;
       *  everything else is one bit. */
      function valueAt(item: WatchItem, cycle: number): string {
        if (!store) return "";
        if (item.kind === "bus") {
          const bits = item.ref as string[];
          return formatValue(busValueAt(store, cycle, bits), bits.length, item.radix);
        }
        const value =
          item.kind === "flop"
            ? store.flopValueAt(cycle, item.ref as string)
            : store.netValueAt(cycle, item.ref as string);
        return String(value);
      }

      function renderValues(): void {
        const cycle = cursorBus.get();
        Array.from(rowsEl.children).forEach((row, i) => {
          const item = watch[i];
          const cell = row.querySelector(".wave-row-value") as HTMLElement | null;
          if (!item || !cell) return;
          cell.textContent = valueAt(item, cycle);
          cell.title = `${item.label} = ${cell.textContent} at cycle ${cycle}`;
        });
      }

      function renderRows(): void {
        rowsEl.replaceChildren();
        watch.forEach((item, i) => rowsEl.append(buildRow(item, i)));
        renderValues();
      }

      function redrawAll(): void {
        if (store) {
          // A shorter run (the Sequence Editor can set one) can leave the
          // cursor past the end, where every history read is a silent 0.
          cycleInput.max = String(store.cycles - 1);
          if (cursorX > store.cycles) cursorX = store.cycles;
          if (cursorBus.get() > store.cycles - 1) setCursor(store.cycles - 1);
        }
        renderRuler();
        Array.from(rowsEl.children).forEach((row, i) => {
          const canvas = row.querySelector("canvas") as HTMLCanvasElement | null;
          if (canvas && watch[i]) drawTrace(canvas, watch[i]);
        });
        renderValues();
        syncCursorField();
      }

      storeReady
        .then((s) => {
          if (disposed) return;
          store = s;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          cycleInput.max = String(s.cycles - 1);
          syncCursorField();
          // A useful default watch list: the input the player is driving,
          // the flag they are trying to raise, and the byte it reveals.
          for (const name of ["I", "success"]) {
            if (name in s.tape.header.names) {
              addWatch({ kind: "net", label: name, ref: name, radix: "hex" });
            }
          }
          addBus("O");
          renderRuler();
          unsubStore = s.subscribe(redrawAll);
          unsubCursor = cursorBus.subscribe(() => {
            // A cycle handed to us by anyone else -- another panel, the field,
            // the arrows -- is a whole cycle, so the line goes back on it. Our
            // own drag has already put the line where the pointer is, and must
            // not be snapped out from under it.
            if (!movingSelf) cursorX = cursorBus.get();
            redrawAll();
          });
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