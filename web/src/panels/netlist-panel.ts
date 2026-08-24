// The Netlist Browser (game-plan.md §4.3): a virtualised table over every
// instance and every net, filterable, cross-highlighting the die view on
// hover and opening the Cone Walker on click. This is the panel that turns
// "728 instances, ~5,000 nets" from an abstraction into something you can
// actually scroll through.

import type { DesignClient } from "../design/client";
import type { InstanceView, NetView } from "../gdsx-types";
import { highlightBus } from "../store/highlight";
import { coneRootBus } from "../store/selection";
import { labels, type LabelKind } from "../store/labels";
import { instanceChip, netChip, openLabelEditor } from "./chips";
import { attachPythonCallButton } from "./python-call";
import { VirtualList } from "./virtual-list";
import type { Mounted } from "./mounts";

const ROW_H = 24;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

/**
 * The heading of a detail box: the label if there is one, the raw name if
 * not, and a button to change that. The raw name is always shown -- as the
 * heading itself, or dimmed beside the label -- because this is the panel
 * where a player decides *what* something is, and hiding the extracted name
 * behind their own guess is exactly the wrong trade here.
 */
function titleRow(kind: LabelKind, name: string): HTMLElement {
  const row = el("div", "detail-title");
  const label = labels.get(kind, name);
  row.append(el("h3", undefined, label ?? name));
  if (label !== null) row.append(el("span", "detail-rawname", name));
  const btn = el("button", "detail-label-btn", label === null ? "label" : "relabel");
  (btn as HTMLButtonElement).type = "button";
  btn.title = `give ${name} a name you'll recognise`;
  btn.addEventListener("click", () => openLabelEditor(kind, name, btn));
  row.append(btn);
  return row;
}

function driverText(net: NetView): string {
  if (net.driver) return `${net.driver.instance}.${net.driver.pin}`;
  switch (net.leaf) {
    case "primary_in":
      return "primary input";
    case "const0":
      return "constant 0";
    case "const1":
      return "constant 1";
    case "undriven":
      return "undriven";
    default:
      return "—";
  }
}

/**
 * The netlist browser, as a section of the Netlist panel.
 *
 * Was a panel in its own right until Labels joined it under one tab; the
 * body below is unchanged from that `render`, only de-indented. See
 * `netlist-host.ts` for the host and `mounts.ts` for why sections are
 * mounted and disposed rather than hidden.
 */
export function mountNetlistBrowser(
  container: HTMLElement,
  designReady: Promise<DesignClient>,
): Mounted {
    container.classList.add("netlist-panel");
    container.innerHTML = `
      <div class="netlist-toolbar">
        <div class="netlist-tabs">
          <button class="tab-btn active" data-tab="instances">Instances</button>
          <button class="tab-btn" data-tab="nets">Nets</button>
        </div>
        <input class="netlist-filter" type="text" placeholder="filter…" />
        <label class="netlist-check inst-only"><input type="checkbox" class="seq-only" /> sequential</label>
        <label class="netlist-check net-only" hidden><input type="checkbox" class="ports-only" /> ports only</label>
        <select class="kind-select net-only" hidden>
          <option value="">any kind</option>
          <option value="primary_in">primary input</option>
          <option value="const0">constant 0</option>
          <option value="const1">constant 1</option>
          <option value="flop_q">flop output</option>
          <option value="undriven">undriven</option>
          <option value="combinational">combinational</option>
        </select>
        <span class="netlist-count"></span>
        <span class="py-call-slot"></span>
      </div>
      <div class="netlist-loading">waiting on the analysis engine…</div>
      <div class="netlist-body" hidden>
        <div class="netlist-list-instances"></div>
        <div class="netlist-list-nets" hidden></div>
        <div class="netlist-detail">
          <div class="netlist-detail-empty">select a row for detail</div>
        </div>
      </div>`;

    const tabBtns = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".tab-btn"),
    );
    const filterEl = container.querySelector(".netlist-filter") as HTMLInputElement;
    const seqOnlyEl = container.querySelector(".seq-only") as HTMLInputElement;
    const portsOnlyEl = container.querySelector(".ports-only") as HTMLInputElement;
    const kindSelectEl = container.querySelector(".kind-select") as HTMLSelectElement;
    const countEl = container.querySelector(".netlist-count") as HTMLSpanElement;
    const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;
    const loadingEl = container.querySelector(".netlist-loading") as HTMLDivElement;
    const bodyEl = container.querySelector(".netlist-body") as HTMLDivElement;
    const instListEl = container.querySelector(".netlist-list-instances") as HTMLDivElement;
    const netListEl = container.querySelector(".netlist-list-nets") as HTMLDivElement;
    const detailEl = container.querySelector(".netlist-detail") as HTMLDivElement;

    let lastCall: string | null = null;
    attachPythonCallButton(callSlot, () => lastCall);

    let tab: "instances" | "nets" = "instances";
    let allInstances: InstanceView[] = [];
    let allNets: NetView[] = [];
    let selectedName: string | null = null;

    function showInstanceDetail(inst: InstanceView): void {
      const box = el("div", "detail-box");
      box.append(titleRow("instance", inst.name));
      const meta = el("div", "detail-meta");
      meta.append(
        el("div", undefined, `cell: ${inst.cell}`),
        el("div", undefined, `base: ${inst.base_cell}  generic: ${inst.generic}`),
        el("div", undefined, inst.is_sequential ? "sequential" : "combinational"),
      );
      box.append(meta);
      if (Object.keys(inst.functions).length) {
        box.append(el("h4", undefined, "function"));
        for (const [pin, fn] of Object.entries(inst.functions)) {
          box.append(el("div", "detail-fn", `${pin} = ${fn}`));
        }
      }
      box.append(el("h4", undefined, "connections"));
      const conns = el("div", "detail-conns");
      for (const [pin, net] of Object.entries(inst.connections)) {
        const row = el("div", "detail-conn-row");
        row.append(el("span", "detail-pin", pin), netChip(net));
        conns.append(row);
      }
      box.append(conns);
      detailEl.replaceChildren(box);
    }

    function showNetDetail(net: NetView): void {
      const box = el("div", "detail-box");
      box.append(titleRow("net", net.name));
      const meta = el("div", "detail-meta");
      meta.append(el("div", undefined, `driver: ${driverText(net)}`));
      if (net.is_port) meta.append(el("div", undefined, `port: ${net.is_port}`));
      if (net.leaf) meta.append(el("div", undefined, `leaf: ${net.leaf}`));
      box.append(meta);
      const openBtn = el(
        "button",
        "detail-open-cone",
        "open in Cone Walker",
      ) as HTMLButtonElement;
      openBtn.type = "button";
      openBtn.addEventListener("click", () => coneRootBus.open(net.name));
      box.append(openBtn);
      box.append(el("h4", undefined, `readers (${net.readers.length})`));
      const readers = el("div", "detail-conns");
      for (const r of net.readers) {
        const row = el("div", "detail-conn-row");
        row.append(
          instanceChip(r.instance, { onClick: false }),
          el("span", "detail-pin", `.${r.pin}`),
        );
        readers.append(row);
      }
      box.append(readers);
      detailEl.replaceChildren(box);
    }

    /** A row's name cell: the label in front, the raw name kept beside it
     *  so a scan of the list still shows what the netlist actually calls
     *  each thing. */
    function nameCell(kind: LabelKind, name: string): HTMLElement {
      const cell = el("span", "nrow-name");
      const label = labels.get(kind, name);
      if (label === null) {
        cell.textContent = name;
      } else {
        cell.append(el("span", "nrow-label", label), el("span", "nrow-rawname", name));
      }
      return cell;
    }

    const instList = new VirtualList<InstanceView>(instListEl, ROW_H, (inst) => {
      const row = el("div", "nrow" + (inst.name === selectedName ? " active" : ""));
      row.append(nameCell("instance", inst.name));
      row.append(el("span", "nrow-badge", inst.generic));
      if (inst.is_sequential) row.append(el("span", "nrow-seq", "seq"));
      row.addEventListener("click", () => {
        selectedName = inst.name;
        showInstanceDetail(inst);
        instList.refresh();
      });
      return row;
    });

    const netList = new VirtualList<NetView>(netListEl, ROW_H, (net) => {
      const row = el("div", "nrow" + (net.name === selectedName ? " active" : ""));
      row.append(nameCell("net", net.name));
      row.append(el("span", "nrow-badge", driverText(net)));
      if (net.is_port) row.append(el("span", "nrow-port", net.is_port));
      row.addEventListener("pointerenter", () =>
        highlightBus.set({ name: net.name }),
      );
      row.addEventListener("pointerleave", () => highlightBus.set(null));
      row.addEventListener("click", () => {
        selectedName = net.name;
        showNetDetail(net);
        coneRootBus.open(net.name);
        netList.refresh();
      });
      return row;
    });

    function applyFilter(): void {
      const q = filterEl.value.trim().toLowerCase();
      if (tab === "instances") {
        const seqOnly = seqOnlyEl.checked;
        const filtered = allInstances.filter((i) => {
          if (seqOnly && !i.is_sequential) return false;
          if (!q) return true;
          // Labels are searchable too, or naming a cell would make it
          // harder to find rather than easier.
          return (
            i.name.toLowerCase().includes(q) ||
            i.base_cell.toLowerCase().includes(q) ||
            i.generic.toLowerCase().includes(q) ||
            labels.matches("instance", i.name, q)
          );
        });
        countEl.textContent = `${filtered.length} / ${allInstances.length} instances`;
        instList.setItems(filtered);
      } else {
        const portsOnly = portsOnlyEl.checked;
        const kind = kindSelectEl.value;
        const filtered = allNets.filter((n) => {
          if (portsOnly && !n.is_port) return false;
          if (kind === "combinational" && n.leaf !== null) return false;
          if (kind && kind !== "combinational" && n.leaf !== kind) return false;
          if (!q) return true;
          return n.name.toLowerCase().includes(q) || labels.matches("net", n.name, q);
        });
        countEl.textContent = `${filtered.length} / ${allNets.length} nets`;
        netList.setItems(filtered);
      }
    }

    function switchTab(next: "instances" | "nets"): void {
      tab = next;
      for (const b of tabBtns) b.classList.toggle("active", b.dataset.tab === tab);
      instListEl.hidden = tab !== "instances";
      netListEl.hidden = tab !== "nets";
      for (const e of Array.from(container.querySelectorAll<HTMLElement>(".inst-only"))) {
        e.hidden = tab !== "instances";
      }
      for (const e of Array.from(container.querySelectorAll<HTMLElement>(".net-only"))) {
        e.hidden = tab !== "nets";
      }
      lastCall =
        tab === "instances" ? "gdsx.api.instances(handle)" : "gdsx.api.nets(handle)";
      applyFilter();
    }

    for (const b of tabBtns) {
      b.addEventListener("click", () => switchTab(b.dataset.tab as "instances" | "nets"));
    }
    filterEl.addEventListener("input", applyFilter);
    seqOnlyEl.addEventListener("change", applyFilter);
    portsOnlyEl.addEventListener("change", applyFilter);
    kindSelectEl.addEventListener("change", applyFilter);

    let disposed = false;
    designReady
      .then(async (design) => {
        if (disposed) return;
        const [inst, nets] = await Promise.all([design.instances(), design.nets()]);
        if (disposed) return;
        allInstances = inst.data;
        allNets = nets.data;
        loadingEl.hidden = true;
        bodyEl.hidden = false;
        switchTab("instances");
      })
      .catch((err) => {
        loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
      });

    const unsub = highlightBus.subscribe(() => {
      instList.refresh();
      netList.refresh();
    });

    // A rename has to re-run the filter, not just repaint: it can change
    // which rows match the current query.
    const unsubLabels = labels.subscribe(() => {
      applyFilter();
      const selected =
        tab === "instances"
          ? allInstances.find((i) => i.name === selectedName)
          : allNets.find((n) => n.name === selectedName);
      if (!selected) return;
      if (tab === "instances") showInstanceDetail(selected as InstanceView);
      else showNetDetail(selected as NetView);
    });

    return {
      dispose() {
        disposed = true;
        unsub();
        unsubLabels();
      },
    };
}
