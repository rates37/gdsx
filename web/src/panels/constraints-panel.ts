// The Constraints panel: accumulates
// constraint rows the player has derived -- from the Sensitivity panel's row
// clicks (via `constraintsInbox`), typed in directly, or produced by one of
// the two generators below -- into one `analysis.constraints.System`, and
// shows how many solutions exist, or "more than N", and
// `unconstrained_elements()`: watched elements measured but never given a
// row, which is the trap `System`'s own docstring warns about.
//
// This panel is explicitly worth nothing: the design brief is direct that
// using it is not cheating -- the rows in it are things the player measured
// -- but the points are in the deriving, not the solving, so there is no
// notebook-claim button here and the panel says so in its own toolbar.
//
// Where the line sits, since a solver in the app is a game-design boundary:
// the panel solves the system the player *states*; it never derives one.
// The generators exist because a spacing rule is hundreds of forbidden pairs
// and a partition rule is eleven sets of eleven cycles, and neither is
// something anyone will type. Every field in them starts blank and is filled
// by the player from their own measurement. Nothing is prefilled from the
// design, nothing inspects the rows already present to suggest a rule shape,
// and there is no "work out the system for me" button. A generator is a
// typing aid over a rule the player already stated, exactly as the sweep's
// "→ constraints" button is a typing aid over a sweep they already ran.

import { attachPythonCallButton } from "./python-call.ts";
import { partitionRows, spacingRows } from "../experiments/constraint-rules.ts";
import { constraintsInbox, type ConstraintCandidate } from "../store/constraints-inbox.ts";
import type { Mounted } from "./mounts.ts";
import type { DesignClient } from "../design/client.ts";
import type { SimStore } from "../sim/store.ts";
import type { ConstraintView, SystemView } from "../gdsx-types.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

/**
 * One entry in the panel's list, which is one *or many* constraint rows.
 *
 * A measured or typed row is a group of one and behaves exactly as it used
 * to, `k` editable in place. A generator produces a group of eleven or of
 * four hundred, which is why the list is grouped at all: four hundred lines
 * of `0 <= a + b <= 1` is not a readable statement of "no two pulses may sit
 * next to each other", and the whole value of the panel is that the player
 * can read back what they have claimed.
 */
interface RowGroup {
  id: string;
  label: string;
  source: string;
  rows: ConstraintView[];
  included: boolean;
  /** Present only for a group of one `exact` row, whose k is editable. */
  editableK: boolean;
  /** Set for rows that name a watched element, so `unconstrained_elements()`
   *  has something to check against. Generator rows name a rule, not an
   *  element, and are deliberately not watched. */
  watched: boolean;
}

export interface ConstraintsPanelOptions {
  designReady: Promise<DesignClient>;
  storeReady: Promise<SimStore>;
}

let nextGroupId = 0;

/**
 * The constraints system, as a drawer inside the Experiments panel.
 *
 * It sits next to the sweep that produces its rows rather than in a tab of
 * its own: on its own it was a dead end with one upstream feeder and nothing
 * downstream. Mounted lazily by `drawer()`, so the Python dependency here
 * never delays the gate-tape sweep above it.
 */
export function mountConstraints(
  container: HTMLElement,
  options: ConstraintsPanelOptions,
): Mounted {
  container.classList.add("cs-panel");
  container.innerHTML = `
      <div class="cs-toolbar">
        <span class="cs-note">this panel scores nothing — the points are in deriving the rows, not in solving them. A notebook claim is about the design, and the design settles it; a row here is about the key, and nothing settles it — rows are conjoined and searched.</span>
        <span class="py-call-slot"></span>
      </div>
      <div class="cs-add">
        <input type="text" class="cs-name" placeholder="name" />
        <input type="text" class="cs-elements" placeholder="candidate cycles, comma separated" />
        <input type="number" class="cs-k" placeholder="exactly k" value="1" />
        <button type="button" class="cs-add-btn">+ add row</button>
      </div>
      <div class="cs-gen">
        <div class="cs-gen-line">
          <span class="cs-gen-label">partition</span>
          <span class="cs-gen-text">cycles</span>
          <input type="number" class="cs-p-from" placeholder="from" />
          <span class="cs-gen-text">…</span>
          <input type="number" class="cs-p-to" placeholder="to" />
          <span class="cs-gen-text">by cycle</span>
          <select class="cs-p-mode">
            <option value="div">div</option>
            <option value="mod">mod</option>
          </select>
          <input type="number" class="cs-p-n" placeholder="n" />
          <span class="cs-gen-text">, exactly</span>
          <input type="number" class="cs-p-k" placeholder="k" />
          <span class="cs-gen-text">in each</span>
          <button type="button" class="cs-p-add">+ add</button>
        </div>
        <div class="cs-gen-line">
          <span class="cs-gen-label">spacing</span>
          <span class="cs-gen-text">cycles</span>
          <input type="number" class="cs-s-from" placeholder="from" />
          <span class="cs-gen-text">…</span>
          <input type="number" class="cs-s-to" placeholder="to" />
          <span class="cs-gen-text">, epoch length</span>
          <input type="number" class="cs-s-len" placeholder="len" />
          <span class="cs-gen-text">, forbid pairs with |Δposition| ≤</span>
          <input type="number" class="cs-s-dp" placeholder="dp" />
          <span class="cs-gen-text">and |Δepoch| ≤</span>
          <input type="number" class="cs-s-de" placeholder="de" />
          <button type="button" class="cs-s-add">+ add</button>
        </div>
        <div class="cs-gen-note">both start blank on purpose — the numbers are yours to measure, not the app's to know</div>
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
  let store: SimStore | null = null;
  let disposed = false;
  let groups: RowGroup[] = [];
  let system: SystemView | null = null;
  let lastCall: string | null = null;

  attachPythonCallButton(callSlot, () => lastCall);

  /** A number typed into a generator field, or `null` if it is blank or not
   *  a number. Every generator refuses to build until all of its fields
   *  answer this -- there are no defaults to fall back on by design. */
  function typed(input: HTMLInputElement): number | null {
    if (!input.value.trim()) return null;
    const n = Number(input.value);
    return Number.isFinite(n) ? Math.round(n) : null;
  }

  /** A row's candidate cycles, count first. The count is the load-bearing
   *  half: a truncated list reads the same whether it is thirteen cycles or
   *  every cycle in the window, and "exactly 2 of these" means something
   *  very different in those two cases. */
  function summarise(elements: readonly number[]): string {
    const shown = elements.slice(0, 12).join(",");
    const list = elements.length > 12 ? `{${shown},…}` : `{${shown}}`;
    return `${elements.length} ${elements.length === 1 ? "cycle" : "cycles"} ${list}`;
  }

  function renderRows(): void {
    rowsEl.replaceChildren();
    for (const group of groups) {
      const line = el("div", "cs-row");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = group.included;
      check.addEventListener("change", () => {
        group.included = check.checked;
        void rebuild();
      });
      line.append(
        check,
        el("span", "cs-row-name", group.label),
        el("span", "cs-row-source", `(${group.source})`),
      );

      if (group.rows.length === 1 && group.editableK) {
        const row = group.rows[0];
        const kBox = document.createElement("input");
        kBox.type = "number";
        kBox.className = "cs-row-k";
        kBox.value = String(row.lb);
        kBox.addEventListener("change", () => {
          const k = Math.max(0, Math.round(Number(kBox.value) || 0));
          row.lb = k;
          row.ub = k;
          void rebuild();
        });
        line.append(
          el("span", "cs-row-elements", summarise(row.elements)),
          el("span", "cs-row-exactly", "exactly"),
          kBox,
        );
      } else {
        // A generated group: say what it means and how big it is, and put
        // the rows themselves behind a disclosure rather than in the way.
        line.append(
          el("span", "cs-row-elements", `${group.rows.length} rows`),
        );
        const expand = el("button", "cs-row-expand", "show rows") as HTMLButtonElement;
        expand.type = "button";
        const detail = el("div", "cs-row-detail");
        detail.hidden = true;
        expand.addEventListener("click", () => {
          if (detail.hidden && !detail.childElementCount) {
            for (const row of group.rows) {
              detail.append(
                el(
                  "div",
                  "cs-row-detail-line",
                  `${row.name}: ${row.lb} ≤ Σ${summarise(row.elements)} ≤ ${row.ub}`,
                ),
              );
            }
          }
          detail.hidden = !detail.hidden;
          expand.textContent = detail.hidden ? "show rows" : "hide rows";
        });
        line.append(expand);
        rowsEl.append(line, detail);
        appendRemove(line, group);
        continue;
      }

      appendRemove(line, group);
      rowsEl.append(line);
    }
  }

  function appendRemove(line: HTMLElement, group: RowGroup): void {
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => {
      groups = groups.filter((g) => g !== group);
      renderRows();
      void rebuild();
    });
    line.append(removeBtn);
  }

  async function rebuild(): Promise<void> {
    if (!design) return;
    const rows = groups.filter((g) => g.included).flatMap((g) => g.rows);
    const watched = groups
      .filter((g) => g.watched)
      .flatMap((g) => g.rows.map((r) => r.name));
    try {
      const { data, call } = await design.constraintsSystem(rows, watched);
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
      el(
        "div",
        "cs-summary-line",
        `${system.constraints.length} row(s) over ${system.variables.length} candidate cycle(s)`,
      ),
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
    groups.push({
      id: `g${nextGroupId++}`,
      label: name,
      source: "typed",
      rows: [{ name, elements, lb: k, ub: k }],
      included: true,
      editableK: true,
      watched: true,
    });
    nameInput.value = "";
    elementsInput.value = "";
    renderRows();
    void rebuild();
  });

  const pFrom = container.querySelector(".cs-p-from") as HTMLInputElement;
  const pTo = container.querySelector(".cs-p-to") as HTMLInputElement;
  const pMode = container.querySelector(".cs-p-mode") as HTMLSelectElement;
  const pN = container.querySelector(".cs-p-n") as HTMLInputElement;
  const pK = container.querySelector(".cs-p-k") as HTMLInputElement;
  const genStatus = container.querySelector(".cs-gen-note") as HTMLDivElement;
  const genNote = genStatus.textContent ?? "";

  function complain(message: string): void {
    genStatus.textContent = message;
    genStatus.classList.add("cs-warning");
  }

  function clearComplaint(): void {
    genStatus.textContent = genNote;
    genStatus.classList.remove("cs-warning");
  }

  (container.querySelector(".cs-p-add") as HTMLButtonElement).addEventListener(
    "click",
    () => {
      const from = typed(pFrom);
      const to = typed(pTo);
      const n = typed(pN);
      const k = typed(pK);
      if (from === null || to === null || n === null || k === null) {
        complain("fill every field — the partition needs a range, a divisor and a count");
        return;
      }
      if (to < from || n < 1 || k < 0) {
        complain("a range runs upwards, the divisor is at least 1 and the count is not negative");
        return;
      }
      const mode = pMode.value === "mod" ? "mod" : "div";
      const rows = partitionRows(from, to, n, mode, k);
      clearComplaint();
      groups.push({
        id: `g${nextGroupId++}`,
        label: `partition ${from}…${to} by cycle ${mode} ${n}, exactly ${k}`,
        source: "partition",
        rows,
        included: true,
        editableK: false,
        watched: false,
      });
      renderRows();
      void rebuild();
    },
  );

  const sFrom = container.querySelector(".cs-s-from") as HTMLInputElement;
  const sTo = container.querySelector(".cs-s-to") as HTMLInputElement;
  const sLen = container.querySelector(".cs-s-len") as HTMLInputElement;
  const sDp = container.querySelector(".cs-s-dp") as HTMLInputElement;
  const sDe = container.querySelector(".cs-s-de") as HTMLInputElement;

  (container.querySelector(".cs-s-add") as HTMLButtonElement).addEventListener(
    "click",
    () => {
      const from = typed(sFrom);
      const to = typed(sTo);
      const len = typed(sLen);
      const dp = typed(sDp);
      const de = typed(sDe);
      if (from === null || to === null || len === null || dp === null || de === null) {
        complain("fill every field — the spacing rule needs a range, an epoch length and both deltas");
        return;
      }
      if (to < from || len < 1 || dp < 0 || de < 0) {
        complain("a range runs upwards, the epoch length is at least 1 and the deltas are not negative");
        return;
      }
      const rows = spacingRows(from, to, len, dp, de);
      if (!rows.length) {
        complain("those deltas forbid nothing — no pair of cycles in that range is that close");
        return;
      }
      clearComplaint();
      groups.push({
        id: `g${nextGroupId++}`,
        label: `spacing ${from}…${to}, epoch ${len}: no two with |Δp| ≤ ${dp} and |Δe| ≤ ${de}`,
        source: "spacing",
        rows,
        included: true,
        editableK: false,
        watched: false,
      });
      renderRows();
      void rebuild();
    },
  );

  /** Write a solution's cycles onto one input track, so the answer the
   *  player's own rows produced can be looked at in the waveform instead of
   *  transcribed by hand. This is transcription, not derivation: the cycles
   *  are already on screen. */
  function loadIntoEditor(cycles: readonly number[], port: string): string {
    if (!store) return "no simulation yet";
    const bits = new Uint8Array(store.cycles);
    let dropped = 0;
    for (const c of cycles) {
      if (c >= 0 && c < bits.length) bits[c] = 1;
      else dropped++;
    }
    store.setPattern(port, bits);
    const wrote = `loaded ${cycles.length - dropped} pulses onto ${port}`;
    return dropped ? `${wrote} — ${dropped} fell outside the ${store.cycles}-cycle run` : wrote;
  }

  solveBtn.addEventListener("click", async () => {
    if (!design || !system) return;
    solveBtn.disabled = true;
    solveStatus.textContent = "solving…";
    try {
      const { data, call } = await design.constraintsSolve(system, "dfs", 50);
      lastCall = call;
      if (data.capped) {
        solveStatus.textContent = `at least ${data.solutions.length} solutions (stopped at the limit)`;
      } else if (data.solutions.length === 1) {
        // The verdict the whole panel exists to reach: not "here is an
        // answer" but "here is the answer, and the search ran to exhaustion
        // to say so".
        solveStatus.textContent = "exactly 1 solution — these rules have a unique answer";
      } else {
        solveStatus.textContent = `exactly ${data.solutions.length} solution(s)`;
      }
      solutionsEl.replaceChildren();
      for (const sol of data.solutions.slice(0, 20)) {
        const line = el("div", "cs-solution", `{${sol.join(", ")}}`);
        if (store && store.inputPorts.length) {
          const ports = document.createElement("select");
          ports.className = "cs-solution-port";
          for (const port of store.inputPorts) {
            const opt = document.createElement("option");
            opt.value = port;
            opt.textContent = port;
            ports.append(opt);
          }
          const load = el("button", "cs-solution-load", "→ sequence editor") as HTMLButtonElement;
          load.type = "button";
          const said = el("span", "cs-solution-said");
          load.addEventListener("click", () => {
            said.textContent = loadIntoEditor(sol, ports.value);
          });
          line.append(ports, load, said);
        }
        solutionsEl.append(line);
      }
    } catch (err) {
      solveStatus.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      solveBtn.disabled = false;
    }
  });

  function absorb(items: readonly ConstraintCandidate[]): void {
    const known = new Set(groups.map((g) => g.label));
    for (const item of items) {
      if (known.has(item.name)) continue;
      known.add(item.name);
      groups.push({
        id: `g${nextGroupId++}`,
        label: item.name,
        source: item.source,
        rows: [{ name: item.name, elements: item.elements, lb: 1, ub: 1 }],
        included: true,
        editableK: true,
        watched: true,
      });
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
      if (groups.length) void rebuild();
    })
    .catch((err) => {
      summaryEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
    });

  options.storeReady
    .then((s) => {
      if (disposed) return;
      store = s;
    })
    .catch(() => {
      // The panel is fully usable without a simulation: only the
      // "→ sequence editor" button on a solution needs one, and it is simply
      // not offered when there is none.
    });

  return {
    dispose() {
      disposed = true;
      unsubInbox();
    },
  };
}