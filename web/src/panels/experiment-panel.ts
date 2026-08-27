// The Experiment Runner (game-plan.md §4.7): pick a baseline, a perturbation
// set and a watch set, get a matrix.
//
// This panel absorbed the former Sensitivity panel, which was this same
// engine with the recipe pinned to `single-pulse`, the watch set pinned to
// every flop, and the axes transposed -- so it is now the `elements down the
// side` toggle rather than a tab of its own. That also settles a real
// inconsistency: the old panel hardcoded a baseline of every port at 0, which
// differs from this one's "idle" whenever a port's cycle-0 level is 1, and
// nothing said which you were looking at.
//
// This is the panel where a puzzle is usually cracked, and three things about
// it are deliberate:
//
// 1. **It runs on the gate tape, not in Python.** A single-pulse sweep is
//    `cycles` traces of `cycles` steps each, and doing that in Pyodide would
//    make the most-used measurement in the game a batch job. The engine in
//    web/src/experiments/recipes.ts is a port of `gdsx.sim.sensitivity`, held to
//    it by a golden (web/scripts/test-experiments.mjs), and the `{ }` button
//    shows the real `sensitivity` call rather than a paraphrase of one.
// 2. **The self-checks are shown, not logged.** An element that reacts to no
//    run, or a run that moves nothing, is displayed as a warning above the
//    matrix (§4.10 requires exactly this). Both usually mean the watch set or
//    the window is wrong, and a correct-looking matrix built on either is worse
//    than no matrix.
// 3. **Every result names its baseline.** "Cycle 7 writes flop 50" is a fact
//    about the baseline it was measured against. The header says which, and the
//    evidence exported to the notebook carries it too.
//
// The matrix is drawn on a canvas: a 121 x 92 sweep is 11k cells, which is a
// lot of DOM nodes for something that is redrawn on every hover.

import type { SimStore } from "../sim/store.ts";
import { cursorBus } from "../store/cursor.ts";
import { constraintsInbox } from "../store/constraints-inbox.ts";
import type { DesignClient } from "../design/client.ts";
import type { WinConditionSource } from "../design/win-condition.ts";
import { drawer } from "./mounts.ts";
import { mountConstraints } from "./constraints-panel.ts";
import { highlightBus } from "../store/highlight.ts";
import { labels } from "../store/labels.ts";
import { instanceChip, netChip } from "./chips.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import { evidenceLog } from "../notebook/evidence.ts";
import {
  RECIPES,
  type ExperimentContext,
  type ExperimentResult,
  type RecipeId,
  type RecipeParams,
  perturbationsOf,
  tracksOf,
  unknownWatched,
} from "../experiments/recipes.ts";
import { SweepClient } from "../experiments/client.ts";

const CELL_W = 9;
const CELL_H = 11;
const GUTTER_W = 96;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function option(value: string, label: string): HTMLOptionElement {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  return o;
}

function names(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

export interface ExperimentPanelOptions {
  /** For the Constraints drawer only. The sweep itself never touches Python,
   *  and the drawer does not mount until the player opens it, so this promise
   *  is not awaited anywhere on the path to a first result. */
  designReady: Promise<DesignClient>;
  /** Which flops the puzzle's win condition names, for the third watch
   *  option. Python-backed like the drawer, and on the same terms: it is
   *  resolved in the background and the option appears when it lands, so
   *  nothing on the path to a first sweep waits for it. */
  winCondition: WinConditionSource;
  storeReady: Promise<SimStore>;
  /** Which notebook the evidence lands in. */
  puzzleId: string;
  /** Where the sweep worker fetches its own copy of the gate tape from. */
  tapeUrl: string;
}

export function experimentPanel(options: ExperimentPanelOptions): PanelDef {
  return {
    id: "experiments",
    title: "Experiments",
    render(container: HTMLElement) {
      container.classList.add("xp-panel");
      container.innerHTML = `
        <div class="xp-toolbar">
          <select class="xp-recipe"></select>
          <label class="xp-inline">baseline <select class="xp-baseline">
            <option value="idle">idle (every port at its cycle-0 level)</option>
            <option value="sequence">the current sequence</option>
          </select></label>
          <label class="xp-inline">pulse <select class="xp-port"></select></label>
          <button class="xp-run" type="button" disabled>▶ Run</button>
          <label class="xp-inline xp-orient" title="swap the axes: watched elements down the side, runs across the top">
            <input type="checkbox" class="xp-transpose" /> elements down the side
          </label>
          <span class="xp-status"></span>
          <span class="py-call-slot"></span>
        </div>
        <div class="xp-params"></div>
        <div class="xp-blurb"></div>
        <div class="xp-loading">waiting on the gate tape…</div>
        <div class="xp-body" hidden>
          <div class="xp-warnings"></div>
          <div class="xp-readout">hover a cell</div>
          <div class="xp-canvas-wrap"><canvas class="xp-canvas"></canvas></div>
          <div class="xp-summary"></div>
          <div class="xp-actions">
            <button class="xp-evidence" type="button" disabled>record as evidence</button>
            <button class="xp-copy" type="button" disabled>copy as TSV</button>
            <span class="xp-evidence-note"></span>
          </div>
        </div>
        <div class="xp-drawer-slot"></div>`;

      const recipeBox = container.querySelector(".xp-recipe") as HTMLSelectElement;
      const baselineBox = container.querySelector(".xp-baseline") as HTMLSelectElement;
      const portBox = container.querySelector(".xp-port") as HTMLSelectElement;
      const runBtn = container.querySelector(".xp-run") as HTMLButtonElement;
      const transposeBox = container.querySelector(".xp-transpose") as HTMLInputElement;
      const statusEl = container.querySelector(".xp-status") as HTMLSpanElement;
      const paramsEl = container.querySelector(".xp-params") as HTMLDivElement;
      const blurbEl = container.querySelector(".xp-blurb") as HTMLDivElement;
      const loadingEl = container.querySelector(".xp-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".xp-body") as HTMLDivElement;
      const warnEl = container.querySelector(".xp-warnings") as HTMLDivElement;
      const readoutEl = container.querySelector(".xp-readout") as HTMLDivElement;
      const canvas = container.querySelector(".xp-canvas") as HTMLCanvasElement;
      const summaryEl = container.querySelector(".xp-summary") as HTMLDivElement;
      const evidenceBtn = container.querySelector(".xp-evidence") as HTMLButtonElement;
      const copyBtn = container.querySelector(".xp-copy") as HTMLButtonElement;
      const evidenceNote = container.querySelector(".xp-evidence-note") as HTMLSpanElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      for (const recipe of RECIPES) recipeBox.append(option(recipe.id, recipe.label));

      const log = evidenceLog(options.puzzleId);
      // The sweep itself runs in a worker: see experiments/sweep-worker.ts for
      // why a few hundred milliseconds of arithmetic must not sit on the thread
      // the die view is drawing on.
      const sweeps = new SweepClient(options.tapeUrl);
      let store: SimStore | null = null;

      /**
       * A watch-set column, as a chip of the right kind. The result carries
       * only names, so the gate tape decides: anything it knows as a flop is
       * an instance, everything else is a net. Getting this wrong would put a
       * flop's label in the nets' namespace and silently split one glossary
       * into two.
       */
      function columnKind(name: string): "net" | "instance" {
        return store?.tape.header.flop_names.includes(name) ? "instance" : "net";
      }
      function columnChip(name: string): HTMLElement {
        return columnKind(name) === "instance"
          ? instanceChip(name, { onClick: false })
          : netChip(name, { onClick: false });
      }
      let result: ExperimentResult | null = null;
      let running = false;
      let disposed = false;
      let hovered: { row: number; column: number } | null = null;
      // Which way round the matrix is drawn. Runs-down-the-side is the natural
      // reading of "here are my runs"; elements-down-the-side is the natural
      // reading of "here is my slot map", and on a shift register that view
      // makes the diagonal obvious. It is the *same* result either way -- this
      // never re-runs the sweep, it only swaps the indexing in draw().
      let transposed = false;

      /**
       * The matrix as drawn: what is down the side, what is across the top,
       * and how to get from a drawn (row, column) back to the run and watched
       * element it stands for.
       */
      function axes() {
        const runs = result ? result.rows.length : 0;
        const cols = result ? result.columns.length : 0;
        return {
          rowCount: transposed ? cols : runs,
          colCount: transposed ? runs : cols,
          /** Label down the left gutter. */
          rowLabel: (r: number): string =>
            transposed ? labelFor(result!.columns[r]) : result!.rows[r].label,
          /** Is this drawn row "interesting" -- used to dim empty gutter labels. */
          rowMarked: (r: number): boolean =>
            transposed
              ? result!.rows.some((row) => row.cells[r])
              : Boolean(result!.rows[r].marked),
          cell: (r: number, c: number): number =>
            transposed ? (result!.rows[c]?.cells[r] ?? 0) : (result!.rows[r]?.cells[c] ?? 0),
          /** Index into result.rows for a drawn cell. */
          runIndex: (at: { row: number; column: number }): number =>
            transposed ? at.column : at.row,
          /** Index into result.columns for a drawn cell. */
          columnIndex: (at: { row: number; column: number }): number =>
            transposed ? at.row : at.column,
        };
      }

      function labelFor(column: string): string {
        return labels.display(columnKind(column), column);
      }

      attachPythonCallButton(callSlot, () => result?.call ?? null);

      // ---- parameter fields, per recipe --------------------------------

      const fromInput = numberField("from cycle", 0);
      const toInput = numberField("to cycle", 0);
      const firstInput = numberField("first pulse at", 0);
      const minGapInput = numberField("min gap", 1);
      const maxGapInput = numberField("max gap", 12);
      const watchBox = document.createElement("select");
      watchBox.className = "nb-select xp-watch";
      watchBox.append(option("flops", "every flop"), option("custom", "these signals…"));
      const watchInput = document.createElement("input");
      watchInput.type = "text";
      watchInput.className = "nb-input xp-watch-list";
      watchInput.placeholder = "flop or net names, comma separated";
      watchInput.setAttribute("list", "xp-signals");

      // The third watch option: the flops the win condition names. It is added
      // only once the derivation has produced one -- a puzzle whose lock
      // cannot be reduced to a set of flop values does not get an option that
      // would come back empty. `every flop` on this design returns 43 reacting
      // flops of which 20 are a free-running counter and an output decoder;
      // intersecting that with the win condition by hand is the step this
      // replaces.
      let winList: string | null = null;
      void options.winCondition.get().then((win) => {
        if (disposed || win === null) return;
        winList = win.names.join(", ");
        const opt = option("win", `the win condition (${win.net})`);
        opt.title = `the ${win.flops.size} flops that must hold a value for ${win.net} to go high`;
        watchBox.append(opt);
      });

      function numberField(label: string, value: number): HTMLInputElement {
        const input = document.createElement("input");
        input.type = "number";
        input.className = "xp-num";
        input.value = String(value);
        input.dataset.label = label;
        return input;
      }

      function wrap(input: HTMLInputElement | HTMLSelectElement, label: string): HTMLElement {
        const box = el("label", "xp-inline", `${label} `);
        box.append(input);
        return box;
      }

      function renderParams(): void {
        const recipe = recipeBox.value as RecipeId;
        paramsEl.replaceChildren();
        if (recipe === "gap") {
          paramsEl.append(
            wrap(firstInput, "first pulse at"),
            wrap(minGapInput, "gaps from"),
            wrap(maxGapInput, "to"),
          );
        } else {
          paramsEl.append(wrap(fromInput, "cycles"), wrap(toInput, "to"));
        }
        paramsEl.append(wrap(watchBox, "watch"));
        if (watchBox.value !== "flops") paramsEl.append(watchInput);
        blurbEl.textContent = RECIPES.find((r) => r.id === recipe)?.blurb ?? "";
        // The pulsed port is meaningless for a scan that perturbs nothing.
        portBox.parentElement!.hidden = recipe === "reset-scan";
      }

      // ---- building the context ----------------------------------------

      function contextOf(): ExperimentContext {
        const s = store!;
        const keyPort = portBox.value || s.inputPorts[0];
        const tracks: Record<string, string> = {};
        for (const port of s.inputPorts) {
          tracks[port] = Array.from(s.bitsOf(port), (b) => (b ? "1" : "0")).join("");
        }
        // "Idle" is every port held at the level it starts the run at, with the
        // pulsed port low: the design ticking over with the key never applied.
        const baseVector: Record<string, number> = {};
        for (const port of s.inputPorts) baseVector[port] = tracks[port]?.[0] === "1" ? 1 : 0;
        baseVector[keyPort] = 0;

        const asSequence = baselineBox.value === "sequence";
        return {
          tape: s.tape,
          resetVector: { ...s.resetVector },
          inputPorts: s.inputPorts,
          cycles: s.cycles,
          baseVector,
          keyPort,
          baselineLabel: asSequence
            ? `the current sequence (${s.cycles} cycles)`
            : `idle: ${Object.entries(baseVector)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}`,
          baseline: asSequence
            ? perturbationsOf(tracks, baseVector, s.cycles, s.inputPorts)
            : new Map(),
        };
      }

      function paramsOf(ctx: ExperimentContext): RecipeParams {
        const watch =
          watchBox.value === "flops"
            ? [...ctx.tape.header.flop_names]
            : names(watchInput.value);
        return {
          from: Math.max(0, Math.round(Number(fromInput.value) || 0)),
          to: Math.min(ctx.cycles, Math.round(Number(toInput.value) || ctx.cycles)),
          firstPulse: Math.max(0, Math.round(Number(firstInput.value) || 0)),
          minGap: Math.max(0, Math.round(Number(minGapInput.value) || 1)),
          maxGap: Math.max(1, Math.round(Number(maxGapInput.value) || 1)),
          watch,
        };
      }

      // ---- running ------------------------------------------------------

      async function go(): Promise<void> {
        if (!store || running) return;
        const ctx = contextOf();
        const params = paramsOf(ctx);
        if (params.to <= params.from && recipeBox.value !== "gap") {
          statusEl.textContent = "the window is empty — check the cycle range";
          statusEl.className = "xp-status bad";
          return;
        }
        const missing = unknownWatched(ctx, params.watch);
        if (missing.length) {
          // Not measured and not silently zero: a name the design does not have
          // would otherwise show as an element that reacts to nothing.
          statusEl.textContent = `not a flop or net in this design: ${missing.join(", ")}`;
          statusEl.className = "xp-status bad";
          return;
        }
        if (params.watch.length === 0) {
          statusEl.textContent = "nothing to watch";
          statusEl.className = "xp-status bad";
          return;
        }

        running = true;
        runBtn.disabled = true;
        evidenceNote.textContent = "";
        statusEl.className = "xp-status";
        statusEl.textContent = "running…";
        try {
          result = await sweeps.run(ctx, recipeBox.value as RecipeId, params, (fraction) => {
            statusEl.textContent = `running… ${Math.round(fraction * 100)}%`;
          });
          hovered = null;
          renderResult();
        } catch (err) {
          statusEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
          statusEl.className = "xp-status bad";
        } finally {
          running = false;
          runBtn.disabled = false;
        }
      }

      // ---- drawing ------------------------------------------------------

      function renderResult(): void {
        if (!result) return;
        statusEl.textContent =
          `${result.rows.length} runs · ${result.columns.length} watched · ` +
          `${result.traces} traces in ${result.ms.toFixed(0)} ms`;
        statusEl.className = "xp-status";

        warnEl.replaceChildren();
        for (const warning of result.warnings) {
          warnEl.append(el("div", "xp-warning", `⚠ ${warning}`));
        }
        warnEl.append(
          el("div", "xp-baseline-note", `measured against ${result.baselineLabel}`),
        );

        draw();
        renderSummary();
        evidenceBtn.disabled = false;
        copyBtn.disabled = false;
      }

      function draw(): void {
        if (!result) return;
        const dpr = window.devicePixelRatio || 1;
        const ax = axes();
        const width = GUTTER_W + ax.colCount * CELL_W;
        const height = ax.rowCount * CELL_H;
        canvas.width = Math.ceil(width * dpr);
        canvas.height = Math.ceil(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;

        const ctx2d = canvas.getContext("2d")!;
        ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx2d.clearRect(0, 0, width, height);
        ctx2d.font = "10px ui-monospace, Menlo, monospace";
        ctx2d.textBaseline = "middle";

        for (let r = 0; r < ax.rowCount; r++) {
          const y = r * CELL_H;
          ctx2d.fillStyle = r % 2 ? "#10131a" : "#0d0f14";
          ctx2d.fillRect(0, y, width, CELL_H);
          ctx2d.fillStyle = ax.rowMarked(r) ? "#9aa2b5" : "#4b5162";
          ctx2d.fillText(ax.rowLabel(r), 4, y + CELL_H / 2);

          for (let c = 0; c < ax.colCount; c++) {
            const value = ax.cell(r, c);
            if (!value) continue;
            // "changed" is one colour because it is one bit. A value scan is
            // showing the value itself, and 1 and 0 are not "hit" and "miss".
            ctx2d.fillStyle = result.cellKind === "changed" ? "#4d9be0" : "#6fbf73";
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

      /** The cycle numbers a watched element responded at, read back out of
       *  the run labels. `single-pulse` names its runs "cycle N", which is the
       *  only form the constraints system can consume. */
      function cycleNumbersFor(columnIndex: number): number[] {
        if (!result) return [];
        const out: number[] = [];
        for (const row of result.rows) {
          if (!row.cells[columnIndex]) continue;
          const m = /^cycle (\d+)/.exec(row.label);
          if (m) out.push(Number(m[1]));
        }
        return out;
      }

      function renderSummary(): void {
        if (!result) return;
        summaryEl.replaceChildren();
        if (result.cellKind !== "changed") {
          summaryEl.append(
            el(
              "div",
              "xp-summary-note",
              "green = the element reads 1 at that cycle. This scan perturbs " +
                "nothing: it is the baseline's own history.",
            ),
          );
          return;
        }
        // The actually useful view of a sensitivity sweep: per element, which
        // runs moved it. This is the slot map, and it is what gets exported.
        const table = el("div", "xp-table");
        let shown = 0;
        for (let c = 0; c < result.columns.length; c++) {
          const hits: string[] = [];
          for (const row of result.rows) if (row.cells[c]) hits.push(row.label);
          if (!hits.length) continue;
          if (shown++ >= 40) {
            table.append(
              el("div", "xp-summary-note", `… ${result.columns.length - c} more columns`),
            );
            break;
          }
          const line = el("div", "xp-table-row");
          line.append(columnChip(result.columns[c]), el("span", "xp-hits", hits.join(", ")));
          // The canvas click is already spoken for ("load this run into the
          // waveform"), so the constraints hand-off lives here instead: a
          // labelled button, discoverable, and it survives the transpose.
          const element = result.columns[c];
          const cycles = cycleNumbersFor(c);
          const send = el("button", "xp-to-constraints", "→ constraints") as HTMLButtonElement;
          send.type = "button";
          send.title = `send "${element} moves at ${cycles.join(", ")}" to the Constraints panel`;
          send.addEventListener("click", () => {
            constraintsInbox.push({ name: element, elements: cycles, source: "experiments" });
            send.textContent = "sent";
            send.disabled = true;
          });
          line.append(send);
          table.append(line);
        }
        if (!shown) table.append(el("div", "xp-summary-note", "nothing moved in any run"));
        summaryEl.append(table);
      }

      // ---- interaction ---------------------------------------------------

      function cellAt(event: PointerEvent | MouseEvent): { row: number; column: number } | null {
        if (!result) return null;
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left - GUTTER_W;
        const y = event.clientY - rect.top;
        const column = Math.floor(x / CELL_W);
        const row = Math.floor(y / CELL_H);
        const ax = axes();
        if (column < 0 || row < 0) return null;
        if (column >= ax.colCount || row >= ax.rowCount) return null;
        return { row, column };
      }

      canvas.addEventListener("pointermove", (event) => {
        const at = cellAt(event);
        hovered = at;
        if (!at || !result) {
          readoutEl.textContent = "hover a cell";
          draw();
          return;
        }
        const ax = axes();
        const row = result.rows[ax.runIndex(at)];
        const column = result.columns[ax.columnIndex(at)];
        const columnText = labelFor(column);
        const cell = row.cells[ax.columnIndex(at)];
        readoutEl.textContent =
          result.cellKind === "changed"
            ? `${row.label} × ${columnText} — ${cell ? "moved" : "unchanged"} (final value, vs the baseline)`
            : `${row.label} × ${columnText} = ${cell}`;
        highlightBus.set({ name: column });
        draw();
      });
      canvas.addEventListener("pointerleave", () => {
        hovered = null;
        highlightBus.set(null);
        draw();
      });

      // Clicking a row loads the run that produced it into the sequence editor
      // and the waveform: the "go and look at what the design actually does
      // there" step, one click, same as a notebook counterexample.
      canvas.addEventListener("click", (event) => {
        const at = cellAt(event);
        if (!at || !result || !store) return;
        const row = result.rows[axes().runIndex(at)];
        const ctx = contextOf();
        const tracks = tracksOf(row.perturbations, ctx.baseVector, ctx.cycles, ctx.inputPorts);
        for (const [port, bits] of Object.entries(tracks)) store.setPattern(port, bits);
        const cycles = [...row.perturbations.keys()].sort((a, b) => a - b);
        if (cycles.length) cursorBus.set(cycles[cycles.length - 1]);
        statusEl.textContent = `loaded ${row.label} into the waveform`;
      });

      // The Constraints system, beside the sweep that feeds it. Lazily
      // mounted: closed, it costs nothing and needs no analysis engine.
      const constraintsDrawer = drawer(
        container.querySelector(".xp-drawer-slot") as HTMLElement,
        {
          id: "experiments.constraints",
          title: "Constraints",
          mount: (host) => mountConstraints(host, { designReady: options.designReady }),
        },
      );
      const unsubInbox = constraintsInbox.subscribe((items) => {
        constraintsDrawer.setBadge(items.length ? `${items.length} pending` : null);
      });
      constraintsDrawer.setBadge(
        constraintsInbox.all().length ? `${constraintsInbox.all().length} pending` : null,
      );

      transposeBox.addEventListener("change", () => {
        transposed = transposeBox.checked;
        hovered = null;
        draw();
      });

      evidenceBtn.addEventListener("click", () => {
        if (!result) return;
        const lines: string[] = [];
        if (result.cellKind === "changed") {
          for (let c = 0; c < result.columns.length; c++) {
            const hits: string[] = [];
            for (const row of result.rows) if (row.cells[c]) hits.push(row.label);
            if (hits.length) lines.push(`${result.columns[c]}: ${hits.join(", ")}`);
          }
          if (!lines.length) lines.push("nothing moved in any run");
        } else {
          for (const row of result.rows) {
            const high = result.columns.filter((_, c) => row.cells[c]);
            lines.push(`${row.label}: ${high.length ? high.join(", ") : "all low"}`);
          }
        }
        log.add({
          recipe: result.title,
          summary:
            `${result.rows.length} runs · ${result.columns.length} watched · ` +
            `${result.traces} traces in ${result.ms.toFixed(0)} ms`,
          baseline: result.baselineLabel,
          lines,
          warnings: [...result.warnings],
          call: result.call,
        });
        evidenceNote.textContent = "recorded in the notebook";
      });

      copyBtn.addEventListener("click", () => {
        if (!result) return;
        const header = ["run", ...result.columns].join("\t");
        const body = result.rows
          .map((row) => [row.label, ...Array.from(row.cells)].join("\t"))
          .join("\n");
        navigator.clipboard?.writeText(`${header}\n${body}`).catch(() => {});
        evidenceNote.textContent = "copied";
      });

      recipeBox.addEventListener("change", renderParams);
      watchBox.addEventListener("change", () => {
        // The win-condition option fills the same box the custom one uses --
        // the names stay visible and editable, and everything downstream (the
        // matrix, the self-checks, the TSV) sees one watch list, not two.
        if (watchBox.value === "win" && winList !== null) watchInput.value = winList;
        renderParams();
      });
      runBtn.addEventListener("click", () => void go());

      options.storeReady
        .then((s) => {
          if (disposed) return;
          store = s;
          for (const port of s.inputPorts) portBox.append(option(port, port));
          // The key port by convention is the one the puzzle drives; with no
          // better information that is the first track, which is what the
          // sequence editor shows first too.
          portBox.value = s.inputPorts[0] ?? "";
          toInput.value = String(s.cycles);
          maxGapInput.value = String(Math.min(12, s.cycles - 1));

          const list = document.createElement("datalist");
          list.id = "xp-signals";
          for (const name of [...s.tape.header.flop_names, ...Object.keys(s.tape.header.names)]) {
            const o = document.createElement("option");
            o.value = name;
            list.append(o);
          }
          container.append(list);

          renderParams();
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
          unsubInbox();
          constraintsDrawer.dispose?.();
        },
      };
    },
  };
}