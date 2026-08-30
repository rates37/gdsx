// The Model Builder (game-plan.md §6): write a behavioural model, diff it
// against the real gate tape, find out exactly how it is wrong.
//
// The loop this panel exists to produce: your model is wrong → you find out
// which vector it is wrong on → you load that vector into the waveform and look
// → you fix the model → the number goes up. Everything here serves that, and
// three details carry it:
//
//   * **First divergence, not a pass/fail.** "87% agreement" tells you nothing;
//     "vector 41, {3,7,9,…}, success: your model says 1, the design says 0" is
//     something you can act on, and it is one click from the waveform.
//   * **Observables must be real signals.** A model naming something the design
//     does not have is not wrong, it is unchecked, and the run stops and says
//     so rather than scoring it either way (`diff.ts`).
//   * **The badge is agreement, and says so.** "model validated · 200 vectors",
//     never "proven". The key space is 2^cycles and no vector set exhausts it.
//
// The editor is a plain textarea. A syntax-highlighting editor is a dependency
// and a bundle-size argument for something whose whole job is 40 lines of
// straightforward code, and the player has a real editor a paste away.

import type { SimStore } from "../sim/store.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import { compare, tracksOf, type DesignContext, type DiffReport } from "../model/diff.ts";
import { ModelHost, starterFor, type Language } from "../model/host.ts";
import { ModelStore, VALIDATION_VECTORS, badgeView } from "../model/store.ts";
import { generate, pulsesOf, type Pulses } from "../model/vectors.ts";
import { POINTS } from "../notebook/scoring.ts";

const SEED = 0x5eed;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

export interface ModelPanelOptions {
  storeReady: Promise<SimStore>;
  puzzleId: string;
  /** The net the printed `diff_models` call watches when a run has not set
   *  one yet. From the descriptor rather than the literal `"success"`: this
   *  string is copied into the REPL, so a wrong name there is a call that
   *  does not run. */
  successNet: string | null;
  /** The port the driver says carries the key, for the printed call's
   *  stimulus. Null for a puzzle with no data input. */
  keyPort: string | null;
}

export function modelPanel(options: ModelPanelOptions): PanelDef {
  return {
    id: "model-builder",
    title: "Model Builder",
    render(container: HTMLElement) {
      container.classList.add("mb-panel");
      container.innerHTML = `
        <div class="mb-toolbar">
          <select class="mb-language">
            <option value="javascript">JavaScript</option>
            <option value="python">Python</option>
          </select>
          <label class="xp-inline">key <select class="mb-port"></select></label>
          <label class="xp-inline">vectors <input class="xp-num mb-vectors" type="number" min="1" /></label>
          <button class="mb-run" type="button" disabled>▶ Validate</button>
          <span class="mb-status"></span>
          <span class="py-call-slot"></span>
        </div>
        <div class="mb-loading">waiting on the gate tape…</div>
        <div class="mb-body" hidden>
          <textarea class="mb-source" spellcheck="false"></textarea>
          <div class="mb-badge"></div>
          <div class="mb-report"></div>
          <div class="mb-history"></div>
        </div>`;

      const languageBox = container.querySelector(".mb-language") as HTMLSelectElement;
      const portBox = container.querySelector(".mb-port") as HTMLSelectElement;
      const vectorsInput = container.querySelector(".mb-vectors") as HTMLInputElement;
      const runBtn = container.querySelector(".mb-run") as HTMLButtonElement;
      const statusEl = container.querySelector(".mb-status") as HTMLSpanElement;
      const loadingEl = container.querySelector(".mb-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".mb-body") as HTMLDivElement;
      const sourceEl = container.querySelector(".mb-source") as HTMLTextAreaElement;
      const badgeEl = container.querySelector(".mb-badge") as HTMLDivElement;
      const reportEl = container.querySelector(".mb-report") as HTMLDivElement;
      const historyEl = container.querySelector(".mb-history") as HTMLDivElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      const STARTER = starterFor(options.successNet);
      const model = new ModelStore(options.puzzleId, STARTER.javascript);
      let store: SimStore | null = null;
      let host: ModelHost | null = null;
      let report: DiffReport | null = null;
      let running = false;
      let disposed = false;

      attachPythonCallButton(callSlot, () => callText());

      /** The library call this panel is: `diff_models` over the same vectors. */
      function callText(): string {
        // The selected key port, or the one the descriptor names. Never a
        // literal: this string is copied into the REPL and a wrong port is a
        // call that does not run.
        const port = portBox.value || options.keyPort || "";
        return (
          "from gdsx.sim import diff_models\n" +
          "tape = gdsx.api.sim_compile(handle)   # or: design.tape()\n" +
          "\n" +
          "# your model, as a per-cycle stepper over the same stimulus\n" +
          `vectors = [{${JSON.stringify(port)}: bit} for bit in key]\n` +
          `result = diff_models(tape, my_model, vectors, watch=${JSON.stringify(
            report?.watch ?? (options.successNet ? [options.successNet] : []),
          ).replace(/"/g, "'")})\n` +
          "print(result.agree, result.first)"
        );
      }

      // ---- the design side ----------------------------------------------

      function contextOf(): DesignContext {
        const s = store!;
        const keyPort = portBox.value || s.inputPorts[0];
        const levels: Record<string, number> = {};
        for (const port of s.inputPorts) {
          // Every other port is held at the level it starts the current
          // sequence at -- the same convention the Experiment Runner's idle
          // baseline uses, so a divergence loaded into the waveform is driven
          // the way it was measured.
          levels[port] = s.bitsOf(port)[0] === 1 ? 1 : 0;
        }
        delete levels[keyPort];
        return {
          tape: s.tape,
          resetVector: { ...s.resetVector },
          inputPorts: s.inputPorts,
          cycles: s.cycles,
          keyPort,
          levels,
        };
      }

      function baselinePulses(): Pulses {
        const s = store!;
        const keyPort = portBox.value || s.inputPorts[0];
        return pulsesOf(Array.from(s.bitsOf(keyPort), (b) => (b ? "1" : "0")).join(""));
      }

      // ---- running -------------------------------------------------------

      async function go(): Promise<void> {
        if (!store || running) return;
        const language = languageBox.value as Language;
        const count = Math.max(1, Math.round(Number(vectorsInput.value) || VALIDATION_VECTORS));
        const ctx = contextOf();
        const vectors = generate(baselinePulses(), {
          count,
          cycles: ctx.cycles,
          seed: SEED,
        });

        running = true;
        runBtn.disabled = true;
        statusEl.className = "mb-status";
        statusEl.textContent =
          language === "python" ? "running the model (booting Python)…" : "running the model…";

        try {
          if (!host || host.language !== language) {
            host?.dispose();
            host = new ModelHost(language);
          }
          const answers = await host.run(model.source, vectors);
          statusEl.textContent = "comparing against the gate tape…";
          // One tick, so the status above actually paints before the design
          // side takes the thread for its own few hundred runs.
          await new Promise((resolve) => setTimeout(resolve, 0));

          report = compare(ctx, vectors, answers);
          model.record(report, language, SEED);
          statusEl.textContent = `${report.vectors} vectors in ${report.ms.toFixed(0)} ms`;
          renderReport();
        } catch (err) {
          report = null;
          reportEl.replaceChildren(
            el("div", "mb-error", err instanceof Error ? err.message : String(err)),
          );
          statusEl.textContent = "the run did not finish";
          statusEl.className = "mb-status bad";
          renderBadge();
        } finally {
          running = false;
          runBtn.disabled = false;
        }
      }

      // ---- rendering ------------------------------------------------------

      function renderBadge(): void {
        const view = badgeView(model);
        badgeEl.replaceChildren();
        const line = el("div", "mb-badge-line");
        line.append(el("span", `nb-verdict nb-tone-${view.tone}`, view.label));
        if (view.tone === "green") {
          // Points are shown per item elsewhere in the notebook, so this is
          // consistent -- and the number is the design's own statement that
          // explaining the design beats unlocking it.
          line.append(el("span", "mb-points", `+${POINTS.modelValidated}`));
        }
        badgeEl.append(line, el("div", "mb-badge-detail", view.detail));
      }

      function renderReport(): void {
        renderBadge();
        reportEl.replaceChildren();
        if (!report) return;

        const summary = el("div", "mb-summary");
        const percent = ((report.agreed / report.vectors) * 100).toFixed(1);
        summary.append(
          el("span", "mb-agreement", `${report.agreed} / ${report.vectors} vectors agree (${percent}%)`),
          el("span", "mb-watch", `over ${report.watch.join(", ")}`),
        );
        reportEl.append(summary);

        // Said before the agreement number is read, not after: a signal the
        // design never moved is a signal the model was never asked about.
        if (report.constant.length) {
          reportEl.append(
            el(
              "div",
              "mb-vacuous",
              `⚠ the design held ${report.constant.join(", ")} at one value for all ` +
                `${report.vectors} vectors — agreement about ${
                  report.constant.length === 1 ? "it" : "those"
                } tested nothing`,
            ),
          );
        }

        if (report.first) {
          const first = report.first;
          const box = el("div", "mb-divergence");
          box.append(el("div", "mb-divergence-head", `first divergence — vector ${first.index}`));
          box.append(
            el(
              "div",
              undefined,
              `${first.signal}: your model says ${first.model}, the design says ${first.design}`,
            ),
          );
          box.append(
            el(
              "div",
              "mb-pulses",
              `${first.pulses.length} pulses: {${first.pulses.join(", ")}}`,
            ),
          );
          const load = el("button", "nb-load", "load this vector into the waveform") as HTMLButtonElement;
          load.addEventListener("click", () => {
            if (!store) return;
            const tracks = tracksOf(contextOf(), first.pulses);
            for (const [port, bits] of Object.entries(tracks)) store.setPattern(port, bits);
            statusEl.textContent = `vector ${first.index} loaded into the waveform`;
          });
          box.append(load);
          reportEl.append(box);
        } else {
          reportEl.append(
            el(
              "div",
              "mb-agreed",
              `no divergence in ${report.vectors} vectors — which is agreement, not a proof`,
            ),
          );
        }

        // Which observable is wrong how often: the "my model is right about
        // success but wrong about the counter" answer.
        const mismatched = Object.entries(report.mismatches).filter(([, n]) => n > 0);
        if (mismatched.length) {
          const table = el("div", "mb-mismatches");
          for (const [name, n] of mismatched) {
            table.append(el("div", undefined, `${name}: ${n} of ${report.vectors} vectors disagree`));
          }
          reportEl.append(table);
        }
      }

      function renderHistory(): void {
        const runs = model.runs();
        historyEl.replaceChildren();
        if (runs.length <= 1) return;
        const details = el("details", "nb-history");
        details.append(el("summary", undefined, `${runs.length} runs`));
        for (const run of [...runs].reverse()) {
          details.append(
            el(
              "div",
              "nb-history-row",
              `${new Date(run.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ` +
                `${run.language} · ${run.agreed}/${run.vectors}` +
                (run.validated ? " · validated" : ""),
            ),
          );
        }
        historyEl.append(details);
      }

      // ---- wiring ---------------------------------------------------------

      sourceEl.addEventListener("input", () => model.setSource(sourceEl.value));
      languageBox.addEventListener("change", () => {
        const language = languageBox.value as Language;
        model.setLanguage(language, STARTER[language]);
        sourceEl.value = model.source;
      });
      runBtn.addEventListener("click", () => void go());

      const unsub = model.subscribe(renderHistory);

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
          vectorsInput.value = String(VALIDATION_VECTORS);
          languageBox.value = model.language;
          sourceEl.value = model.source || STARTER[model.language];
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          runBtn.disabled = false;
          renderBadge();
          renderHistory();
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          unsub();
          host?.dispose();
        },
      };
    },
  };
}