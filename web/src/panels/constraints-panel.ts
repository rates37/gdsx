// The Constraints panel (game-plan.md §4.13, needs L38): accumulates
// constraint rows the player has derived -- today, from the Sensitivity
// panel's row clicks (via `constraintsInbox`) or typed in directly -- into
// one `analysis.constraints.System`, and shows how many solutions exist, or
// "more than N", and `unconstrained_elements()`: watched elements measured
// but never given a row, which is the trap `System`'s own docstring warns
// about (the original investigation's `work/test.py` walked straight past it
// by omitting the epoch and spacing constraints).
//
// This panel is explicitly worth nothing: the design brief is direct that
// using it is not cheating -- the rows in it are things the player measured
// -- but the points are in the deriving, not the solving, so there is no
// notebook-claim button here and the panel says so in its own toolbar.

import { attachPythonCallButton } from "./python-call.ts";
import { constraintsInbox, type ConstraintCandidate } from "../store/constraints-inbox.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { DesignClient } from "../design/client.ts";
import type { SystemView } from "../gdsx-types.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

interface Row {
  candidate: ConstraintCandidate;
  k: number;
  included: boolean;
}

export interface ConstraintsPanelOptions {
  designReady: Promise<DesignClient>;
}

export function constraintsPanel(options: ConstraintsPanelOptions): PanelDef {
  return {
    id: "constraints",
    title: "Constraints",
    render(container: HTMLElement) {
      container.classList.add("cs-panel");
      container.innerHTML = `
        <div class="cs-toolbar">
          <span class="cs-note">this panel scores nothing — the points are in deriving the rows, not in solving them</span>
          <span class="py-call-slot"></span>
        </div>
        <div class="cs-add">
          <input type="text" class="cs-name" placeholder="name" />
          <input type="text" class="cs-elements" placeholder="candidate cycles, comma separated" />
          <input type="number" class="cs-k" placeholder="exactly k" value="1" />
          <button type="button" class="cs-add-btn">+ add row</button>
        </div>
        <div class="cs-rows"></div>
        <div class="cs-summary"></div>
        <div class="cs-solve-bar">
          <button type="button" class="cs-solve" disabled>solve (dfs)</button>
          <span class="cs-solve-status"></span>
        </div>
        <div class="cs-solutions"></div>`;

      const rowsEl = container.querySelector(".cs-rows") as HTMLDivElement;
      const summaryEl = container.querySelector(".cs-summary") as HTMLDivElement;
      const solveBtn = container.querySelector(".cs-solve") as HTMLButtonElement;
      const solveStatus = container.querySelector(".cs-solve-status") as HTMLSpanElement;
      const solutionsEl = container.querySelector(".cs-solutions") as HTMLDivElement;
      const nameInput = container.querySelector(".cs-name") as HTMLInputElement;
      const elementsInput = container.querySelector(".cs-elements") as HTMLInputElement;
      const kInput = container.querySelector(".cs-k") as HTMLInputElement;
      const addBtn = container.querySelector(".cs-add-btn") as HTMLButtonElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      let design: DesignClient | null = null;
      let disposed = false;
      let rows: Row[] = [];
      let system: SystemView | null = null;
      let lastCall: string | null = null;

      attachPythonCallButton(callSlot, () => lastCall);

      function renderRows(): void {
        rowsEl.replaceChildren();
        for (const [i, row] of rows.entries()) {
          const line = el("div", "cs-row");
          const check = document.createElement("input");
          check.type = "checkbox";
          check.checked = row.included;
          check.addEventListener("change", () => {
            row.included = check.checked;
            void rebuild();
          });
          const kBox = document.createElement("input");
          kBox.type = "number";
          kBox.className = "cs-row-k";
          kBox.value = String(row.k);
          kBox.addEventListener("change", () => {
            row.k = Math.max(0, Math.round(Number(kBox.value) || 0));
            void rebuild();
          });
          const removeBtn = document.createElement("button");
          removeBtn.type = "button";
          removeBtn.textContent = "×";
          removeBtn.addEventListener("click", () => {
            rows.splice(i, 1);
            renderRows();
            void rebuild();
          });
          line.append(
            check,
            el("span", "cs-row-name", row.candidate.name),
            el("span", "cs-row-source", `(${row.candidate.source})`),
            el("span", "cs-row-elements", `{${row.candidate.elements.join(",")}}`),
            el("span", "cs-row-exactly", "exactly"),
            kBox,
            removeBtn,
          );
          rowsEl.append(line);
        }
      }

      async function rebuild(): Promise<void> {
        if (!design) return;
        const included = rows.filter((r) => r.included);
        const hits: Record<string, number[]> = {};
        const targets: Record<string, number> = {};
        const watched = rows.map((r) => r.candidate.name);
        for (const row of included) {
          hits[row.candidate.name] = row.candidate.elements;
          targets[row.candidate.name] = row.k;
        }
        try {
          const { data, call } = await design.constraintsBuild(hits, watched, targets);
          system = data;
          lastCall = call;
          renderSummary();
          solveBtn.disabled = system.constraints.length === 0;
        } catch (err) {
          summaryEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      function renderSummary(): void {
        summaryEl.replaceChildren();
        if (!system) return;
        summaryEl.append(
          el("div", "cs-summary-line", `${system.constraints.length} row(s) over ${system.variables.length} candidate cycle(s)`),
        );
        if (system.unconstrained.length) {
          summaryEl.append(
            el(
              "div",
              "cs-warning",
              `⚠ measured but never given a row: ${system.unconstrained.join(", ")} — a row you should have and don't`,
            ),
          );
        }
      }

      addBtn.addEventListener("click", () => {
        const name = nameInput.value.trim();
        const elements = elementsInput.value
          .split(/[\s,]+/)
          .map((s) => s.trim())
          .filter(Boolean)
          .map(Number)
          .filter((n) => Number.isFinite(n));
        const k = Math.max(0, Math.round(Number(kInput.value) || 0));
        if (!name || !elements.length) return;
        rows.push({ candidate: { name, elements, source: "typed" }, k, included: true });
        nameInput.value = "";
        elementsInput.value = "";
        renderRows();
        void rebuild();
      });

      solveBtn.addEventListener("click", async () => {
        if (!design || !system) return;
        solveBtn.disabled = true;
        solveStatus.textContent = "solving…";
        try {
          const { data, call } = await design.constraintsSolve(system, "dfs", 50);
          lastCall = call;
          solveStatus.textContent = data.capped
            ? `at least ${data.solutions.length} solutions (stopped at the limit)`
            : `exactly ${data.solutions.length} solution(s)`;
          solutionsEl.replaceChildren();
          for (const sol of data.solutions.slice(0, 20)) {
            solutionsEl.append(el("div", "cs-solution", `{${sol.join(", ")}}`));
          }
        } catch (err) {
          solveStatus.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        } finally {
          solveBtn.disabled = false;
        }
      });

      function absorb(items: readonly ConstraintCandidate[]): void {
        const known = new Set(rows.map((r) => r.candidate.name));
        for (const item of items) {
          if (known.has(item.name)) continue;
          known.add(item.name);
          rows.push({ candidate: item, k: 1, included: true });
        }
        renderRows();
        void rebuild();
      }

      const unsubInbox = constraintsInbox.subscribe((items) => absorb(items));
      absorb(constraintsInbox.all());

      options.designReady
        .then((d) => {
          if (disposed) return;
          design = d;
          if (rows.length) void rebuild();
        })
        .catch((err) => {
          summaryEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          unsubInbox();
        },
      };
    },
  };
}