// The Waveform panel (game-plan.md §4.6): a watch list over any net or flop,
// backed directly by `SimStore`'s history so scrubbing is an array lookup,
// not a re-simulation -- the whole point of compiling to a tape in the first
// place. Indexed nets (`O[0]`..`O[7]`) can be watched as one grouped bus row
// instead of eight separate 0/1 traces.
//
// Not built here: clicking a cycle driving a die-view heat overlay of every
// high net (§4.6's third bullet). That needs the die view to highlight a
// *set* of nets, not the single selection it supports today -- a real
// extension, scoped out of this pass. Hovering a watched net's name still
// cross-highlights it via the same bus the die view and netlist browser use.

import type { SimStore } from "../sim/store.ts";
import { busValueAt } from "../sim/store.ts";
import { highlightBus } from "../store/highlight.ts";
import { cursorBus } from "../store/cursor.ts";
import type { PanelDef } from "../workspace/workspace.ts";

const CELL_W = 6;
const ROW_H = 28;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

type WatchKind = "net" | "flop" | "bus";
interface WatchItem {
  kind: WatchKind;
  label: string;
  /** net name (kind=net), flop instance name (kind=flop), or bit-net names lsb..msb (kind=bus) */
  ref: string | string[];
}

function busPrefix(name: string): string | null {
  const m = /^(.+)\[0\]$/.exec(name);
  return m ? m[1] : null;
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
      const loadingEl = container.querySelector(".wave-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".wave-body") as HTMLDivElement;
      const rulerEl = container.querySelector(".wave-ruler") as HTMLDivElement;
      const rowsEl = container.querySelector(".wave-rows") as HTMLDivElement;

      let disposed = false;
      let store: SimStore | null = null;
      let watch: WatchItem[] = [];
      let unsubStore: (() => void) | null = null;
      let unsubCursor: (() => void) | null = null;

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
          if (!prefix && name.toLowerCase().includes(q)) {
            out.push({ label: name, add: () => addWatch({ kind: "net", label: name, ref: name }) });
          }
        }
        for (const name of store.tape.header.flop_names) {
          if (out.length >= 30) break;
          if (name.toLowerCase().includes(q)) {
            out.push({ label: `${name} (flop)`, add: () => addWatch({ kind: "flop", label: name, ref: name }) });
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
        if (bits.length) addWatch({ kind: "bus", label: `${prefix}[${bits.length - 1}:0]`, ref: bits });
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
        canvas.width = cycles * CELL_W;
        canvas.height = ROW_H;
        const ctx = canvas.getContext("2d")!;
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Epoch/decade gridlines, for visual alignment with the sequence editor.
        ctx.strokeStyle = "#1c2130";
        ctx.lineWidth = 1;
        for (let c = 0; c < cycles; c += 10) {
          ctx.beginPath();
          ctx.moveTo(c * CELL_W + 0.5, 0);
          ctx.lineTo(c * CELL_W + 0.5, ROW_H);
          ctx.stroke();
        }

        if (item.kind === "bus") {
          drawBus(ctx, item.ref as string[], cycles);
        } else {
          drawBit(ctx, item, cycles);
        }

        const cursor = cursorBus.get();
        ctx.strokeStyle = "#f2cc4d";
        ctx.beginPath();
        ctx.moveTo(cursor * CELL_W + 0.5, 0);
        ctx.lineTo(cursor * CELL_W + 0.5, ROW_H);
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
          x += CELL_W;
          ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      function drawBus(ctx: CanvasRenderingContext2D, bits: string[], cycles: number): void {
        const top = 5;
        const bottom = ROW_H - 5;
        ctx.strokeStyle = "#8ecdf7";
        ctx.fillStyle = "#8ecdf7";
        ctx.font = "10px ui-monospace, monospace";
        let segStart = 0;
        let segValue = busValueAt(store!, 0, bits);
        const closeSegment = (end: number) => {
          const x0 = segStart * CELL_W;
          const x1 = end * CELL_W;
          ctx.strokeRect(x0 + 0.5, top, Math.max(1, x1 - x0 - 1), bottom - top);
          if (x1 - x0 >= 26) {
            ctx.fillText(`0x${segValue.toString(16)}`, x0 + 3, ROW_H / 2 + 3, x1 - x0 - 4);
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
        row.draggable = true;
        const label = el("div", "wave-row-label");
        label.append(el("span", "wave-drag-handle", "⋮⋮"));
        const nameEl = el("span", "wave-row-name", item.label);
        if (item.kind === "net") {
          nameEl.addEventListener("pointerenter", () => highlightBus.set({ name: item.ref as string }));
          nameEl.addEventListener("pointerleave", () => highlightBus.set(null));
        }
        label.append(nameEl);
        const removeBtn = el("button", "wave-row-remove", "×") as HTMLButtonElement;
        removeBtn.type = "button";
        removeBtn.addEventListener("click", () => {
          watch = watch.filter((w) => w !== item);
          renderRows();
        });
        label.append(removeBtn);
        row.append(label);

        const canvas = el("canvas", "wave-row-canvas") as HTMLCanvasElement;
        canvas.addEventListener("pointerdown", (e) => {
          const rect = canvas.getBoundingClientRect();
          cursorBus.set(Math.floor((e.clientX - rect.left) / CELL_W));
        });
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
        rulerEl.style.width = `${store.cycles * CELL_W}px`;
        for (let c = 0; c < store.cycles; c += 10) {
          const tick = el("span", "wave-tick", String(c));
          tick.style.left = `${c * CELL_W}px`;
          rulerEl.append(tick);
        }
      }

      function renderRows(): void {
        rowsEl.replaceChildren();
        watch.forEach((item, i) => rowsEl.append(buildRow(item, i)));
      }

      function redrawAll(): void {
        renderRuler();
        Array.from(rowsEl.children).forEach((row, i) => {
          const canvas = row.querySelector("canvas") as HTMLCanvasElement | null;
          if (canvas && watch[i]) drawTrace(canvas, watch[i]);
        });
      }

      storeReady
        .then((s) => {
          if (disposed) return;
          store = s;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          // A useful default watch list: the input the player is driving,
          // the flag they are trying to raise, and the byte it reveals.
          for (const name of ["I", "success"]) {
            if (name in s.tape.header.names) addWatch({ kind: "net", label: name, ref: name });
          }
          addBus("O");
          renderRuler();
          unsubStore = s.subscribe(redrawAll);
          unsubCursor = cursorBus.subscribe(redrawAll);
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