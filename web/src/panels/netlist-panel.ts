// The Netlist Browser: a virtualised table over every
// instance and every net, filterable, cross-highlighting the die view on
// hover and opening the Cone Walker on click. This is the panel that turns
// "728 instances, ~5,000 nets" from an abstraction into something you can
// actually scroll through.

import type { DesignClient } from "../design/client";
import type { InstanceView, NetView } from "../gdsx-types";
import { highlightBus } from "../store/highlight";
import { coneRootBus } from "../store/selection";
import { panelRouteBus } from "../store/panel-route";
import { labels, type LabelKind } from "../store/labels";
import { instanceChip, netChip, openLabelEditor } from "./chips";
import { attachPythonCallButton } from "./python-call";
import { VirtualList } from "./virtual-list";
import { subTabHost, type Mounted } from "./mounts";

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
      return "unknown";
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
export interface NetlistBrowserOptions {
  /** Brings a workspace panel to the front. Absent = the browser still opens
   *  nets on the cone bus, it just cannot navigate to the panel that shows
   *  them. Injected from boot.ts, the same way the die view receives it:
   *  panels are constructed before the workspace exists. */
  onFocusPanel?: (id: string) => void;
}

export function mountNetlistBrowser(
  container: HTMLElement,
  designReady: Promise<DesignClient>,
  options: NetlistBrowserOptions = {},
): Mounted {
    container.classList.add("netlist-panel");
    container.innerHTML = `
      <div class="netlist-toolbar">
        <input class="netlist-filter" type="text" placeholder="filter…" />
        <span class="netlist-count"></span>
        <span class="py-call-slot"></span>
      </div>
      <div class="netlist-loading">waiting on the analysis engine…</div>
      <div class="netlist-body" hidden>
        <div class="netlist-tabhost"></div>
        <div class="netlist-detail">
          <div class="netlist-detail-empty">select a row for detail</div>
        </div>
      </div>`;

    const filterEl = container.querySelector(".netlist-filter") as HTMLInputElement;
    const countEl = container.querySelector(".netlist-count") as HTMLSpanElement;
    const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;
    const loadingEl = container.querySelector(".netlist-loading") as HTMLDivElement;
    const bodyEl = container.querySelector(".netlist-body") as HTMLDivElement;
    const tabHostEl = container.querySelector(".netlist-tabhost") as HTMLDivElement;
    const detailEl = container.querySelector(".netlist-detail") as HTMLDivElement;

    let lastCall: string | null = null;
    attachPythonCallButton(callSlot, () => lastCall);

    let tab: "instances" | "nets" = "instances";
    let allInstances: InstanceView[] = [];
    let allNets: NetView[] = [];
    let selectedName: string | null = null;
    /** The nets currently in the list, in list order -- what a reveal has to
     *  search to know which row to scroll to. */
    let shownNets: NetView[] = [];
    let instList: VirtualList<InstanceView> | null = null;
    let netList: VirtualList<NetView> | null = null;
    let seqOnlyEl: HTMLInputElement | null = null;
    let portsOnlyEl: HTMLInputElement | null = null;
    let kindSelectEl: HTMLSelectElement | null = null;

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
      // The same two-step the die view's context menu makes: the bus says
      // *what* to walk, focusing says *where to look*. Selecting a row fires
      // the bus alone and deliberately does not navigate -- a click in a list
      // of 5,000 nets should not throw you out of the list.
      openBtn.addEventListener("click", () => {
        coneRootBus.open(net.name);
        options.onFocusPanel?.("cone-walker");
      });
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

    function applyFilter(): void {
      const q = filterEl.value.trim().toLowerCase();
      if (tab === "instances" && instList) {
        const seqOnly = seqOnlyEl?.checked ?? false;
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
      } else if (tab === "nets" && netList) {
        const portsOnly = portsOnlyEl?.checked ?? false;
        const kind = kindSelectEl?.value ?? "";
        const filtered = allNets.filter((n) => {
          if (portsOnly && !n.is_port) return false;
          if (kind === "combinational" && n.leaf !== null) return false;
          if (kind && kind !== "combinational" && n.leaf !== kind) return false;
          if (!q) return true;
          return n.name.toLowerCase().includes(q) || labels.matches("net", n.name, q);
        });
        countEl.textContent = `${filtered.length} / ${allNets.length} nets`;
        shownNets = filtered;
        netList.setItems(filtered);
      }
    }

    /**
     * "Show me this net" from somewhere else in the workspace -- the die
     * view's right-click menu, a chip, a cone node.
     *
     * Routes to the Nets sub-tab, drops any filter that would hide the net
     * (revealing nothing is worse than not revealing), scrolls the row into
     * view and opens its detail. Both routes are needed: the outer host owns
     * browser-vs-labels, the inner one instances-vs-nets, and either may not
     * be mounted yet -- `panelRouteBus` remembers the request for whichever
     * mounts later.
     */
    function revealNet(name: string): void {
      const net = allNets.find((n) => n.name === name);
      if (!net) return;
      panelRouteBus.open("netlist", "browser");
      panelRouteBus.open("netlist-browser", "nets");

      selectedName = name;
      showNetDetail(net);

      if (!shownNets.some((n) => n.name === name)) {
        filterEl.value = "";
        if (portsOnlyEl) portsOnlyEl.checked = false;
        if (kindSelectEl) kindSelectEl.value = "";
      }
      applyFilter();

      const index = shownNets.findIndex((n) => n.name === name);
      if (index >= 0) netList?.scrollToIndex(index);
      netList?.refresh();
    }

    /** The Instances sub-tab: its own "sequential only" checkbox above its
     *  own virtualised list. Filter text, count and detail pane are the
     *  host's shared chrome (see `applyFilter` and the `onShow` hooks below). */
    function mountInstancesTab(host: HTMLElement): Mounted {
      host.innerHTML = `
        <div class="netlist-tab-body">
          <label class="netlist-check"><input type="checkbox" class="seq-only" /> sequential</label>
          <div class="netlist-list-instances"></div>
        </div>`;
      seqOnlyEl = host.querySelector(".seq-only") as HTMLInputElement;
      const listEl = host.querySelector(".netlist-list-instances") as HTMLDivElement;
      seqOnlyEl.addEventListener("change", applyFilter);
      instList = new VirtualList<InstanceView>(listEl, ROW_H, (inst) => {
        const row = el("div", "nrow" + (inst.name === selectedName ? " active" : ""));
        row.append(nameCell("instance", inst.name));
        row.append(el("span", "nrow-badge", inst.generic));
        if (inst.is_sequential) row.append(el("span", "nrow-seq", "seq"));
        row.addEventListener("click", () => {
          selectedName = inst.name;
          showInstanceDetail(inst);
          instList!.refresh();
        });
        return row;
      });
      return {};
    }

    /** The Nets sub-tab: its own "ports only" checkbox and kind filter above
     *  its own virtualised list. */
    function mountNetsTab(host: HTMLElement): Mounted {
      host.innerHTML = `
        <div class="netlist-tab-body">
          <label class="netlist-check"><input type="checkbox" class="ports-only" /> ports only</label>
          <select class="kind-select">
            <option value="">any kind</option>
            <option value="primary_in">primary input</option>
            <option value="const0">constant 0</option>
            <option value="const1">constant 1</option>
            <option value="flop_q">flop output</option>
            <option value="undriven">undriven</option>
            <option value="combinational">combinational</option>
          </select>
          <div class="netlist-list-nets"></div>
        </div>`;
      portsOnlyEl = host.querySelector(".ports-only") as HTMLInputElement;
      kindSelectEl = host.querySelector(".kind-select") as HTMLSelectElement;
      const listEl = host.querySelector(".netlist-list-nets") as HTMLDivElement;
      portsOnlyEl.addEventListener("change", applyFilter);
      kindSelectEl.addEventListener("change", applyFilter);
      netList = new VirtualList<NetView>(listEl, ROW_H, (net) => {
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
          netList!.refresh();
        });
        return row;
      });
      return {};
    }

    filterEl.addEventListener("input", applyFilter);

    let disposed = false;
    let tabs: Mounted | null = null;
    designReady
      .then(async (design) => {
        if (disposed) return;
        const [inst, nets] = await Promise.all([design.instances(), design.nets()]);
        if (disposed) return;
        allInstances = inst.data;
        allNets = nets.data;
        loadingEl.hidden = true;
        bodyEl.hidden = false;
        tabs = subTabHost(tabHostEl, "netlist-browser", [
          {
            id: "instances",
            title: "Instances",
            keepAlive: true,
            mount: mountInstancesTab,
            onShow: () => {
              tab = "instances";
              lastCall = "gdsx.api.instances(handle)";
              applyFilter();
            },
          },
          {
            id: "nets",
            title: "Nets",
            keepAlive: true,
            mount: mountNetsTab,
            onShow: () => {
              tab = "nets";
              lastCall = "gdsx.api.nets(handle)";
              applyFilter();
            },
          },
        ]);
      })
      .catch((err) => {
        loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
      });

    const unsub = highlightBus.subscribe(() => {
      instList?.refresh();
      netList?.refresh();
    });

    // The other half of the cross-panel selection: this panel has always
    // *fired* `coneRootBus` and never listened to it, so a net opened
    // elsewhere left the browser sitting on whatever was last clicked here.
    // The `selectedName` guard is what stops a row click -- which fires the
    // bus itself -- from bouncing back through the reveal.
    const unsubRoot = coneRootBus.subscribe((net) => {
      if (net === selectedName) return;
      revealNet(net);
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
        unsubRoot();
        unsubLabels();
        tabs?.dispose?.();
      },
    };
}
