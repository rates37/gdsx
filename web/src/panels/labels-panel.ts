// The Labels panel: every name the player has given something, in one place.
//
// The individual rename lives on the chip (`chips.ts`) -- you name a thing
// where you are looking at it. This panel is the other half: the glossary as
// an object, which is what a reverse-engineering session actually accumulates
// and what the player wants to re-read an hour later. It is also the only
// place a label can be found again once the thing it names has scrolled out
// of whatever panel it was made in.
//
// Raw name and label are shown side by side on every row, always. A glossary
// that showed only the player's names would be a glossary you cannot use to
// look anything up.

import { labels } from "../store/labels";
import { coneRootBus } from "../store/selection";
import { highlightBus } from "../store/highlight";
import { openLabelEditor } from "./chips";
import type { PanelDef } from "../workspace/workspace";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function labelsPanel(): PanelDef {
  return {
    id: "labels",
    title: "Labels",
    render(container: HTMLElement) {
      container.classList.add("labels-panel");
      container.innerHTML = `
        <div class="labels-toolbar">
          <input class="labels-filter" type="text" placeholder="filter…" />
          <span class="labels-count"></span>
        </div>
        <div class="labels-body"></div>`;

      const filterEl = container.querySelector(".labels-filter") as HTMLInputElement;
      const countEl = container.querySelector(".labels-count") as HTMLSpanElement;
      const bodyEl = container.querySelector(".labels-body") as HTMLDivElement;

      function render(): void {
        const q = filterEl.value.trim().toLowerCase();
        const all = labels.all();
        const shown = q
          ? all.filter(
              (e) => e.label.toLowerCase().includes(q) || e.name.toLowerCase().includes(q),
            )
          : all;

        countEl.textContent = q
          ? `${shown.length} / ${all.length}`
          : `${all.length} label${all.length === 1 ? "" : "s"}`;

        bodyEl.replaceChildren();
        if (all.length === 0) {
          bodyEl.append(
            el(
              "div",
              "labels-empty",
              "No labels yet. Double-click any net or cell name — in the netlist " +
                "browser, the cone walker, the waveform — to give it a name you'll " +
                "recognise. The extracted name is kept and shown alongside.",
            ),
          );
          return;
        }

        for (const entry of shown) {
          const row = el("div", "labels-row");
          row.append(
            el("span", `labels-kind labels-kind-${entry.kind}`, entry.kind === "net" ? "net" : "cell"),
          );

          const label = el("span", "labels-label", entry.label);
          const raw = el("span", "labels-raw", entry.name);
          row.append(label, raw);

          // Hovering a row lights the thing up on the die, same as a chip:
          // the glossary is a navigation surface, not just a list.
          const target = entry.name;
          row.addEventListener("pointerenter", () => highlightBus.set({ name: target }));
          row.addEventListener("pointerleave", () => highlightBus.set(null));

          const actions = el("div", "labels-actions");
          const openBtn = el("button", "labels-open", "open") as HTMLButtonElement;
          openBtn.type = "button";
          openBtn.title = "walk this in the Cone Walker";
          openBtn.addEventListener("click", () => coneRootBus.open(entry.name));

          const editBtn = el("button", "labels-edit", "rename") as HTMLButtonElement;
          editBtn.type = "button";
          editBtn.addEventListener("click", () =>
            openLabelEditor(entry.kind, entry.name, editBtn),
          );

          const dropBtn = el("button", "labels-drop", "×") as HTMLButtonElement;
          dropBtn.type = "button";
          dropBtn.title = `forget this label (${entry.name} keeps its extracted name)`;
          dropBtn.addEventListener("click", () => labels.clear(entry.kind, entry.name));

          actions.append(openBtn, editBtn, dropBtn);
          row.append(actions);
          bodyEl.append(row);
        }
      }

      filterEl.addEventListener("input", render);
      const unsub = labels.subscribe(render);
      render();

      return {
        dispose() {
          unsub();
        },
      };
    },
  };
}