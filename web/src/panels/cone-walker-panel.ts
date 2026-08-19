// The Cone Walker (game-plan.md §4.4) -- the single most-used panel in the
// game. Three things make it that:
//
// 1. Truncated nodes never look like leaves. `ConeNode.truncated` exists
//    specifically so the UI can tell "there is more, we stopped" apart from
//    "this really is a primary input" -- collapsing the two is the exact
//    mistake that cost the original investigation a day (see the api.py
//    docstring this mirrors). Truncated nodes get their own affordance
//    (`···`, load deeper) and are never rendered as a leaf pill.
// 2. "Step through the flop" is explicit and it moves a visible cycle
//    marker. The walk itself never crosses a flop -- past one you're in the
//    previous clock cycle, a different question -- so crossing one is a
//    deliberate click, not something that happens by expanding a node.
// 3. Flatten is one click. `requirements()` (analysis.justify, wired through
//    api.py) reduces a cone to the leaves it forces versus the choices it
//    doesn't, which is the exact hand-derivation that cracked the reference
//    puzzle. Forced and choice are rendered apart on purpose, for the same
//    reason `truncated` is: collapsing "must" into "might" is a lie.

import type { DesignClient } from "../design/client";
import type { ConeNode, RequirementsView } from "../gdsx-types";
import { coneRootBus } from "../store/selection";
import { highlightBus } from "../store/highlight";
import { attachPythonCallButton } from "./python-call";
import type { PanelDef } from "../workspace/workspace";

const DEPTH = 5;

interface UiNode extends Omit<ConeNode, "children"> {
  children: UiNode[];
  uiExpanded: boolean;
  /** Set when this node is the result of stepping through a flop: which
   *  flop, and how many steps back from where the walk started. */
  steppedFrom?: { flopInstance: string; cycle: number };
  stepError?: string;
}

function wrap(node: ConeNode, steppedFrom?: UiNode["steppedFrom"]): UiNode {
  return {
    ...node,
    children: node.children.map((c) => wrap(c)),
    uiExpanded: true,
    steppedFrom,
  };
}

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function netSpan(name: string): HTMLElement {
  const s = el("span", "net-chip", name);
  s.addEventListener("pointerenter", () => highlightBus.set({ name }));
  s.addEventListener("pointerleave", () => highlightBus.set(null));
  return s;
}

const LEAF_LABEL: Record<string, string> = {
  primary_in: "primary input",
  const0: "constant 0",
  const1: "constant 1",
  flop_q: "flop Q",
  undriven: "undriven",
};

const SHORT_CELL = /^sky130_fd_sc_hd__/;

export function coneWalkerPanel(designReady: Promise<DesignClient>): PanelDef {
  return {
    id: "cone-walker",
    title: "Cone Walker",
    render(container: HTMLElement) {
      container.classList.add("cone-panel");
      container.innerHTML = `
        <div class="cw-toolbar">
          <input class="cw-net-input" type="text" placeholder="net to walk…" />
          <button class="cw-go" type="button">walk</button>
          <div class="cw-direction">
            <button class="cw-dir-btn active" data-dir="in" type="button">fan-in</button>
            <button class="cw-dir-btn" data-dir="out" type="button">fan-out</button>
          </div>
          <span class="py-call-slot"></span>
        </div>
        <div class="cw-flatten-bar">
          <span class="cw-focus">focus: <span class="cw-focus-net">—</span></span>
          <button class="cw-flatten-btn" type="button" disabled>flatten AND/OR tree</button>
          <div class="cw-value-toggle">
            <button class="cw-val-btn active" data-val="1" type="button">→ 1</button>
            <button class="cw-val-btn" data-val="0" type="button">→ 0</button>
          </div>
        </div>
        <div class="cw-loading">waiting on the analysis engine…</div>
        <div class="cw-body" hidden>
          <div class="cw-tree"></div>
          <div class="cw-flatten-panel" hidden>
            <div class="cw-flatten-header">
              <span class="cw-flatten-title"></span>
              <button class="cw-flatten-close" type="button">×</button>
            </div>
            <div class="cw-flatten-body"></div>
          </div>
        </div>`;

      const netInput = container.querySelector(".cw-net-input") as HTMLInputElement;
      const goBtn = container.querySelector(".cw-go") as HTMLButtonElement;
      const dirBtns = Array.from(
        container.querySelectorAll<HTMLButtonElement>(".cw-dir-btn"),
      );
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;
      const focusNetEl = container.querySelector(".cw-focus-net") as HTMLSpanElement;
      const flattenBtn = container.querySelector(".cw-flatten-btn") as HTMLButtonElement;
      const valBtns = Array.from(
        container.querySelectorAll<HTMLButtonElement>(".cw-val-btn"),
      );
      const loadingEl = container.querySelector(".cw-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".cw-body") as HTMLDivElement;
      const treeEl = container.querySelector(".cw-tree") as HTMLDivElement;
      const flattenPanel = container.querySelector(".cw-flatten-panel") as HTMLDivElement;
      const flattenTitle = container.querySelector(".cw-flatten-title") as HTMLSpanElement;
      const flattenBody = container.querySelector(".cw-flatten-body") as HTMLDivElement;
      const flattenClose = container.querySelector(".cw-flatten-close") as HTMLButtonElement;

      let lastCall: string | null = null;
      attachPythonCallButton(callSlot, () => lastCall);

      let design: DesignClient | null = null;
      let direction: "in" | "out" = "in";
      let root: UiNode | null = null;
      let focusNet: string | null = null;
      let flattenValue: 0 | 1 = 1;
      let disposed = false;

      function setFocus(net: string): void {
        focusNet = net;
        focusNetEl.textContent = net;
        flattenBtn.disabled = direction !== "in";
      }

      function renderTree(): void {
        treeEl.replaceChildren();
        if (!root) return;
        treeEl.append(buildNode(root, 0));
      }

      function buildNode(node: UiNode, depth: number): HTMLElement {
        const wrapEl = el("div", "cw-node");
        const row = el("div", "cw-row");
        row.style.marginLeft = `${depth * 18}px`;

        if (node.steppedFrom) {
          row.append(
            el(
              "span",
              "cw-cycle-badge",
              `${node.steppedFrom.flopInstance}  T-${node.steppedFrom.cycle}`,
            ),
          );
        }

        if (node.leaf) {
          row.append(el("span", "cw-expander cw-expander-none", "·"));
          row.append(netSpan(node.net));
          row.append(el("span", `cw-leaf cw-leaf-${node.leaf}`, LEAF_LABEL[node.leaf] ?? node.leaf));
          if (node.leaf === "flop_q") {
            const stepBtn = el("button", "cw-step-btn", "step through flop ↦");
            (stepBtn as HTMLButtonElement).type = "button";
            stepBtn.addEventListener("click", (e) => {
              e.stopPropagation();
              void stepThroughFlop(node);
            });
            row.append(stepBtn);
          }
          if (node.stepError) row.append(el("span", "cw-step-error", node.stepError));
        } else if (node.truncated) {
          const btn = el("button", "cw-expander cw-truncated", "···");
          (btn as HTMLButtonElement).type = "button";
          btn.title = "truncated: there is more below, this is not a leaf";
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            void loadDeeper(node);
          });
          row.append(btn);
          row.append(netSpan(node.net));
          row.append(el("span", "cw-truncated-label", "truncated — click ··· to load deeper"));
        } else {
          const hasChildren = node.children.length > 0;
          const toggle = el(
            "button",
            "cw-expander",
            hasChildren ? (node.uiExpanded ? "▾" : "▸") : "·",
          );
          (toggle as HTMLButtonElement).type = "button";
          (toggle as HTMLButtonElement).disabled = !hasChildren;
          toggle.addEventListener("click", (e) => {
            e.stopPropagation();
            node.uiExpanded = !node.uiExpanded;
            renderTree();
          });
          row.append(toggle);
          row.append(netSpan(node.net));
          if (node.gate) {
            row.append(el("span", "cw-cell", node.gate.cell.replace(SHORT_CELL, "")));
            row.append(el("span", "cw-fn", node.gate.fn));
            const pins = el("span", "cw-pins");
            for (const [pin, net] of Object.entries(node.pins)) {
              pins.append(el("span", "cw-pin-label", `${pin}:`));
              pins.append(netSpan(net));
            }
            row.append(pins);
          }
        }

        row.addEventListener("click", (e) => {
          if ((e.target as HTMLElement).closest("button, .net-chip")) return;
          setFocus(node.net);
        });

        wrapEl.append(row);
        if (!node.leaf && !node.truncated && node.uiExpanded) {
          for (const child of node.children) wrapEl.append(buildNode(child, depth + 1));
        }
        return wrapEl;
      }

      async function loadDeeper(node: UiNode): Promise<void> {
        if (!design) return;
        const res = await design.cone(node.net, { depth: DEPTH, direction });
        lastCall = res.call;
        const fresh = wrap(res.data, node.steppedFrom);
        node.gate = fresh.gate;
        node.pins = fresh.pins;
        node.leaf = fresh.leaf;
        node.truncated = fresh.truncated;
        node.children = fresh.children;
        node.uiExpanded = true;
        renderTree();
      }

      async function stepThroughFlop(node: UiNode): Promise<void> {
        if (!design) return;
        const step = await design.flopDNet(node.net);
        lastCall = step.call;
        const { instance, net: dNet } = step.data;
        if (dNet === null) {
          node.stepError = `${instance}'s D pin has no single net (scan flop?)`;
          renderTree();
          return;
        }
        const cycle = (node.steppedFrom?.cycle ?? 0) + 1;
        const res = await design.cone(dNet, { depth: DEPTH, direction });
        lastCall = res.call;
        const fresh = wrap(res.data, { flopInstance: instance, cycle });
        Object.assign(node, fresh);
        renderTree();
      }

      async function loadRoot(net: string): Promise<void> {
        if (!design || !net) return;
        loadingEl.hidden = false;
        loadingEl.textContent = `walking ${net}…`;
        bodyEl.hidden = true;
        flattenPanel.hidden = true;
        try {
          const res = await design.cone(net, { depth: DEPTH, direction });
          lastCall = res.call;
          root = wrap(res.data);
          setFocus(net);
          loadingEl.hidden = true;
          bodyEl.hidden = false;
          renderTree();
        } catch (err) {
          loadingEl.hidden = false;
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      function renderFlatten(r: RequirementsView): void {
        flattenTitle.textContent = `${r.net} == ${r.value}`;
        flattenBody.replaceChildren();

        const summary = el(
          "div",
          "cw-flatten-summary",
          `${r.leaves.length} ${r.leaves.length === 1 ? "leaf" : "leaves"} forced` +
            (r.choices.length
              ? `, ${r.choices.length} choice${r.choices.length === 1 ? "" : "s"} unresolved`
              : ", tree is pure AND/OR") +
            (r.consistent ? "" : " — INCONSISTENT (conflicting requirements)"),
        );
        flattenBody.append(summary);

        if (r.leaves.length) {
          const list = el("div", "cw-flatten-leaves");
          for (const leaf of r.leaves) {
            const row = el("div", "cw-flatten-leaf");
            row.append(el("span", `cw-polarity cw-polarity-${leaf.value}`, String(leaf.value)));
            row.append(netSpan(leaf.net));
            list.append(row);
          }
          flattenBody.append(list);
        }

        if (r.choices.length) {
          const cbox = el("div", "cw-flatten-choices");
          cbox.append(el("h4", undefined, "not forced — any one option suffices"));
          for (const c of r.choices) {
            const crow = el("div", "cw-choice");
            crow.append(el("div", "cw-choice-head", `${c.net} == ${c.value}:`));
            for (const opt of c.options) {
              const text = opt.literals.map((l) => `${l.net}=${l.value}`).join("  &  ");
              crow.append(el("div", "cw-choice-option", text || "(always true)"));
            }
            cbox.append(crow);
          }
          flattenBody.append(cbox);
        }

        if (r.conflicts.length) {
          flattenBody.append(
            el("div", "cw-flatten-conflicts", `conflicting: ${r.conflicts.join(", ")}`),
          );
        }

        flattenPanel.hidden = false;
      }

      async function runFlatten(): Promise<void> {
        if (!design || !focusNet || direction !== "in") return;
        flattenTitle.textContent = `flattening ${focusNet}…`;
        flattenBody.replaceChildren();
        flattenPanel.hidden = false;
        try {
          const res = await design.requirements(focusNet, flattenValue);
          lastCall = res.call;
          renderFlatten(res.data);
        } catch (err) {
          flattenBody.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      goBtn.addEventListener("click", () => void loadRoot(netInput.value.trim()));
      netInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void loadRoot(netInput.value.trim());
      });

      for (const b of dirBtns) {
        b.addEventListener("click", () => {
          direction = b.dataset.dir as "in" | "out";
          for (const x of dirBtns) x.classList.toggle("active", x === b);
          flattenBtn.disabled = direction !== "in";
          flattenPanel.hidden = true;
          if (root) void loadRoot(root.net);
        });
      }

      for (const b of valBtns) {
        b.addEventListener("click", () => {
          flattenValue = Number(b.dataset.val) as 0 | 1;
          for (const x of valBtns) x.classList.toggle("active", x === b);
        });
      }

      flattenBtn.addEventListener("click", () => void runFlatten());
      flattenClose.addEventListener("click", () => (flattenPanel.hidden = true));

      const unsubRoot = coneRootBus.subscribe((net) => {
        netInput.value = net;
        void loadRoot(net);
      });

      designReady
        .then((d) => {
          if (disposed) return;
          design = d;
          loadingEl.textContent = "type a net above, or click one anywhere in the app";
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
          unsubRoot();
        },
      };
    },
  };
}