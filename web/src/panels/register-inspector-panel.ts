// The Register Inspector: `analyse.find_registers` +
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
import { mountGroupTools } from "./group-tools.ts";
import { notebookFor } from "../notebook/store.ts";
import type { Mounted } from "./mounts.ts";
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
  /** For the `pin as role` button on an orbit result. */
  puzzleId: string;
  /** Excluded from the stimulus rows: driving the clock or reset as a decode
   *  stimulus is meaningless. */
  clockPort: string;
  resetPort: string | null;
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
            <div class="ri-list-col">
              <div class="ri-list-viewport"></div>
              <div class="ri-adhoc">
                <input type="text" class="ri-adhoc-flops" placeholder="ad-hoc group: flop names, comma separated" />
                <button type="button" class="ri-adhoc-find">group</button>
                <span class="ri-adhoc-status"></span>
              </div>
            </div>
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
      const adhocInput = container.querySelector(".ri-adhoc-flops") as HTMLInputElement;
      const adhocBtn = container.querySelector(".ri-adhoc-find") as HTMLButtonElement;
      const adhocStatus = container.querySelector(".ri-adhoc-status") as HTMLSpanElement;

      let design: DesignClient | null = null;
      let disposed = false;
      let registers: RegisterEntry[] = [];
      let guards: GuardsResult["guards"] = [];
      let inputs: string[] = [];
      let selected: RegisterEntry | null = null;
      let lastCall: string | null = null;
      let groupTools: Mounted | null = null;
      const notebook = notebookFor(options.puzzleId);

      attachPythonCallButton(callSlot, () => lastCall);

      const list = new VirtualList<RegisterEntry>(listViewport, 40, (reg) => {
        const row = el("div", "ri-row" + (reg === selected ? " ri-row-selected" : ""));
        row.append(el("span", "ri-row-name", reg.name));
        row.append(el("span", "ri-row-role", reg.role?.kind ?? reg.kind));
        row.append(el("span", "ri-row-width", `×${reg.width}`));
        row.addEventListener("click", () => select(reg));
        return row;
      });

      /**
       * Decode a group the player named rather than one `find_registers`
       * discovered.
       *
       * On a large design the interesting group is often precisely the one the
       * recovery pass did not find, so this has to stay reachable -- it was
       * the whole of the old Register Decoder panel. What has changed is that
       * it is now an entry beside the discovered list rather than a blank
       * screen in a tab of its own.
       */
      async function groupAdHoc(): Promise<void> {
        if (!design) return;
        const flops = adhocInput.value.split(",").map((f) => f.trim()).filter(Boolean);
        if (!flops.length) return;
        adhocStatus.textContent = "grouping…";
        adhocStatus.className = "ri-adhoc-status";
        try {
          const { data, call } = await design.grouping(flops);
          lastCall = call;
          const groups = data.groups;
          if (!groups.length) {
            adhocStatus.textContent = "no groups";
            return;
          }
          adhocStatus.textContent = `${groups.length} group${groups.length === 1 ? "" : "s"}`;
          // Present each as a pseudo-register so the detail pane, the flop
          // chips and the group tools all work on it unchanged.
          const pseudo: RegisterEntry[] = groups.map((flopNames, i) => ({
            name: `ad-hoc ${i + 1}`,
            flops: flopNames,
            width: flopNames.length,
            kind: "ad-hoc",
            description: `grouped from ${flops.length} flop name(s) you supplied`,
          }) as unknown as RegisterEntry);
          registers = [...pseudo, ...registers.filter((r) => r.kind !== "ad-hoc")];
          countEl.textContent = `${registers.length} registers`;
          list.setItems(registers);
          select(registers[0]);
        } catch (err) {
          adhocStatus.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
          adhocStatus.className = "ri-adhoc-status bad";
        }
      }

      adhocBtn.addEventListener("click", () => void groupAdHoc());
      adhocInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void groupAdHoc();
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
        truthBox.append(el("div", "ri-section-title", "truth table: click a flop above"));
        detailBody.append(truthBox);

        // Bit weights, orbit and select values, against the register this
        // panel discovered. Shared with the ad-hoc group entry below rather
        // than written twice -- the Register Decoder used to be a whole panel
        // for this, reachable only by typing flop names into a blank screen.
        if (design) {
          groupTools?.dispose?.();
          const toolsBox = el("div", "ri-section");
          detailBody.append(toolsBox);
          groupTools = mountGroupTools(toolsBox, {
            design,
            notebook,
            inputs,
            clockPort: options.clockPort,
            resetPort: options.resetPort,
            group: reg.flops,
            onCall: (call) => {
              lastCall = call;
            },
          });
        }
      }

      async function loadTruthTable(flop: string): Promise<void> {
        if (!design) return;
        const box = detailBody.querySelector(".ri-truth-section") as HTMLDivElement;
        box.replaceChildren(el("div", "ri-section-title", `truth table: ${flop}`));
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
            el("div", "ri-section-title", `truth table: ${flop}`),
            el("div", "ri-hint bad", err instanceof Error ? err.message : String(err)),
          );
        }
      }

      function renderTruthTable(box: HTMLDivElement, flop: string, table: ReturnType<typeof computeTruthTable>): void {
        box.replaceChildren();
        box.append(el("div", "ri-section-title", `truth table: D(${flop}) over ${table.free.length} leaves`));
        const note =
          table.method === "exhaustive"
            ? `exhaustive: all ${table.cases} case${table.cases === 1 ? "" : "s"} enumerated`
            : `SAMPLED: ${table.rows.length} of 2^${table.free.length} cases. This is not exhaustive, do not read it as one`;
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