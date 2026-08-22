// The Register Inspector (game-plan.md §4.5): `analyse.find_registers` +
// `sequential` classification + `guards` in one view, plus two things
// neither of those give on their own:
//
// - a **truth table** for any flop's D-cone, reusing the notebook's own
//   `SliceRunner` (notebook/truth-table.ts) rather than a second cone
//   enumerator -- small cones are exact, larger ones sample and say so, and
//   the panel never blurs that line;
// - an **orbit view** (`analysis.decode.orbit`, L36): apply a stimulus
//   repeatedly from reset and watch the state sequence, which is how a
//   player discovers "saturating counter" instead of being told.

import { instanceChip } from "./chips.ts";
import { attachPythonCallButton } from "./python-call.ts";
import { VirtualList } from "./virtual-list.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { DesignClient, GuardsResult } from "../design/client.ts";
import { computeTruthTable, type TruthRow } from "../notebook/truth-table.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

interface RegisterEntry {
  name: string;
  flops: string[];
  width: number;
  kind: string;
  description: string;
  role: { kind: string; evidence: string; feeds: string[]; fed_by: string[] } | null;
}

export interface RegisterInspectorOptions {
  designReady: Promise<DesignClient>;
}

export function registerInspectorPanel(options: RegisterInspectorOptions): PanelDef {
  return {
    id: "register-inspector",
    title: "Registers",
    render(container: HTMLElement) {
      container.classList.add("ri-panel");
      container.innerHTML = `
        <div class="ri-loading">waiting on the analysis engine…</div>
        <div class="ri-body" hidden>
          <div class="ri-toolbar"><span class="ri-count"></span><span class="py-call-slot"></span></div>
          <div class="ri-main">
            <div class="ri-list-col"><div class="ri-list-viewport"></div></div>
            <div class="ri-detail">
              <div class="ri-detail-header"><span class="ri-pick-a-register">pick a register</span></div>
              <div class="ri-detail-body"></div>
            </div>
          </div>
        </div>`;

      const loadingEl = container.querySelector(".ri-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".ri-body") as HTMLDivElement;
      const countEl = container.querySelector(".ri-count") as HTMLSpanElement;
      const listViewport = container.querySelector(".ri-list-viewport") as HTMLDivElement;
      const headerEl = container.querySelector(".ri-detail-header") as HTMLDivElement;
      const detailBody = container.querySelector(".ri-detail-body") as HTMLDivElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      let design: DesignClient | null = null;
      let disposed = false;
      let registers: RegisterEntry[] = [];
      let guards: GuardsResult["guards"] = [];
      let inputs: string[] = [];
      let selected: RegisterEntry | null = null;
      let lastCall: string | null = null;

      attachPythonCallButton(callSlot, () => lastCall);

      const list = new VirtualList<RegisterEntry>(listViewport, 40, (reg) => {
        const row = el("div", "ri-row" + (reg === selected ? " ri-row-selected" : ""));
        row.append(el("span", "ri-row-name", reg.name));
        row.append(el("span", "ri-row-role", reg.role?.kind ?? reg.kind));
        row.append(el("span", "ri-row-width", `×${reg.width}`));
        row.addEventListener("click", () => select(reg));
        return row;
      });

      function select(reg: RegisterEntry): void {
        selected = reg;
        list.refresh();
        renderDetail();
      }

      function renderDetail(): void {
        headerEl.replaceChildren();
        detailBody.replaceChildren();
        if (!selected || !design) {
          headerEl.append(el("span", "ri-pick-a-register", "pick a register"));
          return;
        }
        const reg = selected;
        headerEl.append(
          el("span", "ri-title", reg.name),
          el("span", "ri-badge", reg.role?.kind ?? reg.kind),
          el("span", "ri-sub", `width ${reg.width} · ${reg.flops.length} flops`),
        );
        detailBody.append(el("div", "ri-description", reg.description));
        if (reg.role?.evidence) {
          detailBody.append(el("div", "ri-evidence", `evidence: ${reg.role.evidence}`));
        }

        const own = new Set(reg.flops);
        const flopGuards = guards.filter((g) => own.has(String(g.flop)));
        const guardBox = el("div", "ri-section");
        guardBox.append(el("div", "ri-section-title", "enable condition"));
        if (flopGuards.length === 0) {
          guardBox.append(el("div", "ri-hint", "ungated: no dominant guard found for these flops"));
        } else {
          for (const g of flopGuards) {
            const line = el("div", "ri-guard-line");
            line.append(
              instanceChip(String(g.flop), { onClick: false }),
              el("span", "ri-guard-cond", ` holds while ${g.condition}`),
            );
            guardBox.append(line);
          }
        }
        detailBody.append(guardBox);

        const flopsBox = el("div", "ri-section");
        flopsBox.append(el("div", "ri-section-title", "flops (bit order)"));
        const flopsRow = el("div", "ri-flops-row");
        for (const f of reg.flops) {
          flopsRow.append(
            instanceChip(f, { className: "ri-flop-chip", onClick: () => void loadTruthTable(f) }),
          );
        }
        flopsBox.append(flopsRow);
        detailBody.append(flopsBox);

        const truthBox = el("div", "ri-section ri-truth-section");
        truthBox.append(el("div", "ri-section-title", "truth table — click a flop above"));
        detailBody.append(truthBox);

        const orbitBox = el("div", "ri-section ri-orbit-section");
        orbitBox.append(el("div", "ri-section-title", "orbit — apply a stimulus repeatedly from reset"));
        const stimForm = el("div", "ri-orbit-form");
        const toggles = new Map<string, HTMLButtonElement>();
        for (const port of inputs) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = `${port}=0`;
          btn.dataset.value = "0";
          btn.addEventListener("click", () => {
            const next = btn.dataset.value === "0" ? "1" : "0";
            btn.dataset.value = next;
            btn.textContent = `${port}=${next}`;
            btn.classList.toggle("active", next === "1");
          });
          toggles.set(port, btn);
          stimForm.append(btn);
        }
        const runOrbitBtn = document.createElement("button");
        runOrbitBtn.type = "button";
        runOrbitBtn.textContent = "▶ walk orbit";
        stimForm.append(runOrbitBtn);
        const orbitOut = document.createElement("div");
        orbitOut.className = "ri-orbit-out";
        orbitBox.append(stimForm, orbitOut);
        detailBody.append(orbitBox);

        runOrbitBtn.addEventListener("click", () => {
          const stimulus: Record<string, number> = {};
          for (const [port, btn] of toggles) stimulus[port] = Number(btn.dataset.value);
          void runOrbit(reg, stimulus, orbitOut);
        });
      }

      async function loadTruthTable(flop: string): Promise<void> {
        if (!design) return;
        const box = detailBody.querySelector(".ri-truth-section") as HTMLDivElement;
        box.replaceChildren(el("div", "ri-section-title", `truth table — ${flop}`));
        box.append(el("div", "ri-hint", "computing…"));
        try {
          const { data: inst } = await design.instances([flop]);
          const qNet = inst[0]?.connections?.["Q"];
          if (!qNet) throw new Error(`${flop} has no Q pin connected`);
          const { data: dNet, call: dCall } = await design.flopDNet(qNet);
          const { data: slice, call: sliceCall } = await design.coneSlice(dNet.net!);
          lastCall = `${dCall}\n${sliceCall}`;
          const table = computeTruthTable(slice);
          renderTruthTable(box, flop, table);
        } catch (err) {
          box.replaceChildren(
            el("div", "ri-section-title", `truth table — ${flop}`),
            el("div", "ri-hint bad", err instanceof Error ? err.message : String(err)),
          );
        }
      }

      function renderTruthTable(box: HTMLDivElement, flop: string, table: ReturnType<typeof computeTruthTable>): void {
        box.replaceChildren();
        box.append(el("div", "ri-section-title", `truth table — D(${flop}) over ${table.free.length} leaves`));
        const note =
          table.method === "exhaustive"
            ? `exhaustive: all ${table.cases} case${table.cases === 1 ? "" : "s"} enumerated`
            : `SAMPLED: ${table.rows.length} of 2^${table.free.length} cases — this is not exhaustive, do not read it as one`;
        box.append(el("div", table.method === "exhaustive" ? "ri-truth-note" : "ri-truth-note ri-sampled", note));
        if (table.truncated && table.method === "exhaustive") {
          box.append(el("div", "ri-hint", `showing the first ${table.rows.length} of ${table.cases} rows`));
        }
        const header = el("div", "ri-truth-row ri-truth-header");
        for (const name of table.free) header.append(el("span", "ri-truth-cell", name));
        header.append(el("span", "ri-truth-cell ri-truth-value", "D"));
        box.append(header);
        const rowsBox = el("div", "ri-truth-rows");
        rowsBox.style.maxHeight = "220px";
        rowsBox.style.overflowY = "auto";
        for (const row of table.rows.slice(0, 500)) rowsBox.append(renderTruthRow(table.free, row));
        box.append(rowsBox);
      }

      function renderTruthRow(free: string[], row: TruthRow): HTMLElement {
        const line = el("div", "ri-truth-row");
        for (const name of free) line.append(el("span", "ri-truth-cell", String(row.leaves[name])));
        line.append(el("span", "ri-truth-cell ri-truth-value", String(row.value)));
        return line;
      }

      async function runOrbit(
        reg: RegisterEntry,
        stimulus: Record<string, number>,
        out: HTMLDivElement,
      ): Promise<void> {
        if (!design) return;
        out.replaceChildren(el("div", "ri-hint", "walking…"));
        try {
          const { data, call } = await design.decodeOrbit(reg.flops, stimulus, null);
          lastCall = call;
          out.replaceChildren();
          out.append(el("div", "ri-orbit-kind", `classification: ${data.kind}`));
          if (data.kind === "counter") {
            out.append(el("div", "ri-hint", "step budget hit before a repeat — not a final answer yet"));
          }
          if (data.kind === "unknown") {
            out.append(el("div", "ri-hint", "a real cycle was found, but it fits none of the named shapes"));
          }
          const states = el("div", "ri-orbit-states");
          for (const [i, state] of data.states.entries()) {
            states.append(el("span", "ri-orbit-state", `${i}:[${state.join("")}]`));
          }
          out.append(states);
        } catch (err) {
          out.replaceChildren(el("div", "ri-hint bad", err instanceof Error ? err.message : String(err)));
        }
      }

      options.designReady
        .then(async (d) => {
          if (disposed) return;
          design = d;
          const [regs, gds, vocab] = await Promise.all([d.registers(), d.guards(), d.claimVocabulary()]);
          if (disposed) return;
          const roleMap = new Map<string, any>();
          for (const role of regs.data.roles as any[]) roleMap.set(role.register, role);
          registers = regs.data.registers.map((r: any) => ({
            name: r.name,
            flops: r.flops,
            width: r.width,
            kind: r.kind,
            description: r.description,
            role: roleMap.get(r.name) ?? null,
          }));
          guards = gds.data.guards;
          inputs = vocab.data.inputs;
          countEl.textContent = `${registers.length} registers`;
          list.setItems(registers);
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          if (registers.length) select(registers[0]);
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
        },
      };
    },
  };
}