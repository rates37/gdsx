// The Python REPL drawer (game-plan.md §4.14): Pyodide with `gdsx` preloaded
// and the current design bound to `nl`, `sim`, `design` -- full library
// access, including everything the GUI does not surface. History persists
// per puzzle; snippets from any panel's `{ }` button paste in here (copy from
// the popover, paste into the input -- the popover is read-only display, so
// that hand-off is copy/paste rather than a second code path).
//
// Rich output is real but modest: a returned dataclass the library already
// knows how to flatten (`gdsx.core.serial.to_dict`, the same serialiser
// every `gdsx.api` endpoint uses) pretty-prints as JSON; a `Netlist` or a
// gate-tape trace is not that -- there is no ready-made table/waveform
// renderer for an arbitrary REPL return value, so those fall back to
// `repr()` honestly rather than pretending to be a live rendering they are
// not (worker.ts's `REPL_BOOTSTRAP` says the same in its own comment).

import type { Remote } from "comlink";
import type { GdsxWorker, Envelope } from "../worker.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { DesignClient } from "../design/client.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

interface HistoryEntry {
  source: string;
  stdout: string;
  repr: string;
  rendered: string | null;
  error: string | null;
  at: number;
}

const HISTORY_LIMIT = 200;

class ReplHistory {
  private readonly key: string;
  entries: HistoryEntry[];

  constructor(puzzleId: string) {
    this.key = `gdsx.repl-history.${puzzleId}.v1`;
    this.entries = this.restore();
  }

  push(entry: HistoryEntry): void {
    this.entries = [...this.entries, entry].slice(-HISTORY_LIMIT);
    try {
      localStorage.setItem(this.key, JSON.stringify(this.entries));
    } catch (err) {
      console.warn("gdsx: could not save REPL history", err);
    }
  }

  private restore(): HistoryEntry[] {
    try {
      const raw = localStorage.getItem(this.key);
      return raw ? (JSON.parse(raw) as HistoryEntry[]) : [];
    } catch {
      return [];
    }
  }
}

export interface ReplPanelOptions {
  api: Remote<GdsxWorker>;
  designReady: Promise<DesignClient>;
  puzzleId: string;
}

export function replPanel(options: ReplPanelOptions): PanelDef {
  return {
    id: "repl",
    title: "Python",
    render(container: HTMLElement) {
      container.classList.add("repl-panel");
      container.innerHTML = `
        <div class="repl-loading">waiting on the analysis engine…</div>
        <div class="repl-body" hidden>
          <div class="repl-output"></div>
          <div class="repl-input-row">
            <span class="repl-prompt">&gt;&gt;&gt;</span>
            <textarea class="repl-input" rows="1" spellcheck="false" placeholder="nl.instances[0], graph.d_pin('dfrtp_2_83'), design.registers(ordered=True)…"></textarea>
            <button type="button" class="repl-run">run</button>
          </div>
        </div>`;

      const loadingEl = container.querySelector(".repl-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".repl-body") as HTMLDivElement;
      const outputEl = container.querySelector(".repl-output") as HTMLDivElement;
      const inputEl = container.querySelector(".repl-input") as HTMLTextAreaElement;
      const runBtn = container.querySelector(".repl-run") as HTMLButtonElement;

      const history = new ReplHistory(options.puzzleId);
      let handle: string | null = null;
      let disposed = false;
      let running = false;

      function appendEntry(entry: HistoryEntry): void {
        const block = el("div", "repl-entry");
        block.append(el("div", "repl-echo", `>>> ${entry.source}`));
        if (entry.stdout) block.append(el("pre", "repl-stdout", entry.stdout));
        if (entry.rendered) block.append(el("pre", "repl-rendered", entry.rendered));
        else if (entry.repr) block.append(el("pre", "repl-repr", entry.repr));
        if (entry.error) block.append(el("pre", "repl-error", entry.error));
        outputEl.append(block);
        outputEl.scrollTop = outputEl.scrollHeight;
      }

      async function run(): Promise<void> {
        if (running || !handle) return;
        const source = inputEl.value;
        if (!source.trim()) return;
        running = true;
        runBtn.disabled = true;
        try {
          const result = await options.api.evalPython(handle, source);
          const entry: HistoryEntry = { source, ...result, at: Date.now() };
          history.push(entry);
          appendEntry(entry);
          inputEl.value = "";
          inputEl.style.height = "auto";
        } catch (err) {
          const entry: HistoryEntry = {
            source,
            stdout: "",
            repr: "",
            rendered: null,
            error: err instanceof Error ? err.message : String(err),
            at: Date.now(),
          };
          history.push(entry);
          appendEntry(entry);
        } finally {
          running = false;
          runBtn.disabled = false;
        }
      }

      inputEl.addEventListener("input", () => {
        inputEl.style.height = "auto";
        inputEl.style.height = `${Math.min(200, inputEl.scrollHeight)}px`;
      });
      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void run();
        }
      });
      runBtn.addEventListener("click", () => void run());

      for (const entry of history.entries) appendEntry(entry);

      options.designReady
        .then((d) => {
          if (disposed) return;
          handle = d.handle;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          runBtn.disabled = false;
          inputEl.focus();
        })
        .catch((err: Envelope | Error) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : JSON.stringify(err)}`;
        });

      return {
        dispose() {
          disposed = true;
        },
      };
    },
  };
}