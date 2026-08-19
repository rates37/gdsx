// The Requirements panel (game-plan.md §4.9): "what must be true for this net
// to be 1?" -- `analysis.justify.requirements` (L32), rendered as a checklist.
//
// The one rule this panel exists to keep is the same one `RequirementsView`'s
// own docstring states: `leaves` (forced, every way of reaching the value) and
// `choices` (an OR that stopped the reduction -- any ONE option suffices) are
// never merged into one list. Collapsing a choice into a leaf turns "might"
// into "must", which is exactly the transcription error this panel exists to
// remove.

import { highlightBus } from "../store/highlight.ts";
import { coneRootBus } from "../store/selection.ts";
import { cursorBus } from "../store/cursor.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { DesignClient } from "../design/client.ts";
import type { SimStore } from "../sim/store.ts";
import type { RequirementsView, LeafValue } from "../gdsx-types.ts";
import { Notebook } from "../notebook/store.ts";
import type { Claim } from "../notebook/model.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

/** A leaf's label, decoded: `"0"`/`"1"` (constant), `"<instance>.Q"` (flop),
 *  or the net's own name (primary input) -- `Graph.label`'s own three forms. */
function leafKind(label: string): { kind: "const"; value: 0 | 1 } | { kind: "flop"; instance: string } | { kind: "input"; net: string } {
  if (label === "0" || label === "1") return { kind: "const", value: label === "1" ? 1 : 0 };
  if (label.endsWith(".Q")) return { kind: "flop", instance: label.slice(0, -2) };
  return { kind: "input", net: label };
}

export interface RequirementsPanelOptions {
  designReady: Promise<DesignClient>;
  storeReady: Promise<SimStore>;
  puzzleId: string;
}

export function requirementsPanel(options: RequirementsPanelOptions): PanelDef {
  return {
    id: "requirements",
    title: "Requirements",
    render(container: HTMLElement) {
      container.classList.add("rq-panel");
      container.innerHTML = `
        <div class="rq-toolbar">
          <input type="text" class="rq-net" placeholder="net" value="success" />
          <select class="rq-value"><option value="1">== 1</option><option value="0">== 0</option></select>
          <button type="button" class="rq-go" disabled>▶ derive</button>
          <span class="rq-status"></span>
          <span class="py-call-slot"></span>
        </div>
        <div class="rq-loading">waiting on the analysis engine…</div>
        <div class="rq-body" hidden></div>`;

      const netInput = container.querySelector(".rq-net") as HTMLInputElement;
      const valueSelect = container.querySelector(".rq-value") as HTMLSelectElement;
      const goBtn = container.querySelector(".rq-go") as HTMLButtonElement;
      const statusEl = container.querySelector(".rq-status") as HTMLSpanElement;
      const loadingEl = container.querySelector(".rq-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".rq-body") as HTMLDivElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      let design: DesignClient | null = null;
      let store: SimStore | null = null;
      let disposed = false;
      let lastCall: string | null = null;
      let result: RequirementsView | null = null;
      const notebook = new Notebook(options.puzzleId);

      attachPythonCallButton(callSlot, () => lastCall);

      function satisfied(label: string, wanted: number): boolean | null {
        if (!store) return null;
        const cycle = cursorBus.get();
        const decoded = leafKind(label);
        if (decoded.kind === "const") return decoded.value === wanted;
        if (decoded.kind === "flop") return store.flopValueAt(cycle, decoded.instance) === wanted;
        return store.netValueAt(cycle, decoded.net) === wanted;
      }

      function leafRow(leaf: LeafValue): HTMLElement {
        const row = el("div", "rq-leaf");
        const ok = satisfied(leaf.net, leaf.value);
        row.append(el("span", "rq-check", ok === null ? "·" : ok ? "✓" : "✗"));
        const chip = el("span", "net-chip", leaf.net);
        const decoded = leafKind(leaf.net);
        const highlightName = decoded.kind === "flop" ? decoded.instance : decoded.kind === "input" ? decoded.net : null;
        if (highlightName) {
          chip.addEventListener("pointerenter", () => highlightBus.set({ name: highlightName }));
          chip.addEventListener("pointerleave", () => highlightBus.set(null));
          chip.addEventListener("click", () => coneRootBus.open(highlightName));
        }
        row.append(chip, el("span", "rq-eq", ` == ${leaf.value}`));
        if (decoded.kind === "flop") {
          const claimBtn = document.createElement("button");
          claimBtn.type = "button";
          claimBtn.className = "rq-claim-btn";
          claimBtn.textContent = "pin";
          claimBtn.title = "add as a notebook requirement claim";
          claimBtn.addEventListener("click", () => {
            if (!result) return;
            const claim: Claim = {
              kind: "requirement",
              output: result.net,
              value: result.value,
              flop: decoded.instance,
              flop_value: leaf.value,
            };
            notebook.add(claim);
            claimBtn.textContent = "pinned";
            claimBtn.disabled = true;
          });
          row.append(claimBtn);
        }
        return row;
      }

      function render(): void {
        if (!result) return;
        bodyEl.replaceChildren();
        bodyEl.append(el("div", "rq-summary", `${result.net} == ${result.value}: ${result.consistent ? "consistent" : "INCONSISTENT — conflicting forced values"}`));
        if (!result.consistent) {
          const c = el("div", "rq-conflicts", `conflicting: ${result.conflicts.join(", ")}`);
          bodyEl.append(c);
        }

        const leavesBox = el("div", "rq-section");
        leavesBox.append(el("div", "rq-section-title", `forced (${result.leaves.length}) — every way of reaching ${result.value} needs these`));
        for (const leaf of result.leaves) leavesBox.append(leafRow(leaf));
        bodyEl.append(leavesBox);

        const choicesBox = el("div", "rq-section");
        choicesBox.append(el("div", "rq-section-title", `choices (${result.choices.length}) — an OR: any ONE option below suffices, none is forced`));
        for (const choice of result.choices) {
          const cbox = el("div", "rq-choice");
          cbox.append(el("div", "rq-choice-head", `${choice.net} == ${choice.value}, satisfied by any of:`));
          for (const option of choice.options) {
            const line = el("div", "rq-choice-option");
            line.append(el("span", "rq-choice-bullet", "— "));
            for (const lit of option.literals) {
              const chip = el("span", "net-chip rq-choice-chip", `${lit.net}=${lit.value}`);
              line.append(chip);
            }
            cbox.append(line);
          }
          choicesBox.append(cbox);
        }
        bodyEl.append(choicesBox);
      }

      async function go(): Promise<void> {
        if (!design) return;
        const net = netInput.value.trim();
        if (!net) return;
        const value = Number(valueSelect.value) as 0 | 1;
        goBtn.disabled = true;
        statusEl.textContent = "deriving…";
        statusEl.className = "rq-status";
        try {
          const { data, call } = await design.requirements(net, value);
          result = data;
          lastCall = call;
          statusEl.textContent = `${data.leaves.length} forced · ${data.choices.length} choices`;
          render();
        } catch (err) {
          statusEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
          statusEl.className = "rq-status bad";
        } finally {
          goBtn.disabled = false;
        }
      }

      goBtn.addEventListener("click", () => void go());
      netInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void go();
      });
      const unsubCursor = cursorBus.subscribe(() => render());
      const unsubOpen = coneRootBus.subscribe((net) => {
        netInput.value = net;
        void go();
      });

      Promise.all([options.designReady, options.storeReady])
        .then(([d, s]) => {
          if (disposed) return;
          design = d;
          store = s;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          goBtn.disabled = false;
          void go();
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          unsubCursor();
          unsubOpen();
        },
      };
    },
  };
}