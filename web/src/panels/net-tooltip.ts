// The cursor read-out that names the net under the pointer.
//
// Shared by both die renderers deliberately. The 2D and 3D views answer the
// same question ("what is this wire?") and had no reason to answer it in two
// different shapes; keeping it here is what stops them drifting when one of
// them gains a line the other does not.
//
// It draws the *label* when the player has named the net, with the extracted
// name beside it -- the same rule `chips.ts` follows, and for the same
// reason: the raw name is what every other panel and the bus carry, so
// hiding it behind a nickname makes the two impossible to line up.

import { labels } from "../store/labels";
// An unextracted net is real metal that lands on no cell pin -- nearly always
// li1 wiring inside a standard cell. It has no driver, no cone and no netlist
// row, and naming the cell it belongs to is what stops a player reading the
// greyed-out menu items as a bug. The menu says the same sentence, from the
// same function, so the two cannot drift.
import { unextractedReason } from "./net-menu";

export interface NetTooltip {
  /** Show (or move) the tooltip for `net`, at viewport coordinates. Pass
   *  `extracted: false` for metal the netlist has no entry for, and the hint
   *  line says so instead of advertising actions that cannot run. `owner`
   *  names the cell that metal lives inside, when the bundle knows it. */
  show(
    clientX: number,
    clientY: number,
    net: string,
    extracted?: boolean,
    owner?: string,
  ): void;
  hide(): void;
  dispose(): void;
}

const HINT = "click to select · right-click for actions";

/** Mounts a tooltip into `wrap`, which must be a positioned element (both
 *  die views' canvas wrappers are `position: absolute; inset: 0`). */
export function netTooltip(wrap: HTMLElement): NetTooltip {
  const element = document.createElement("div");
  element.className = "panel-overlay die-tooltip";
  element.hidden = true;
  const nameEl = document.createElement("div");
  nameEl.className = "die-tooltip-name";
  const rawEl = document.createElement("div");
  rawEl.className = "die-tooltip-raw";
  const hintEl = document.createElement("div");
  hintEl.className = "die-tooltip-hint";
  hintEl.textContent = HINT;
  element.append(nameEl, rawEl, hintEl);
  wrap.append(element);

  let current: string | null = null;
  let currentExtracted = true;
  let currentOwner = "";

  function paint(): void {
    if (current === null) return;
    const label = labels.get("net", current);
    nameEl.textContent = label ?? current;
    rawEl.textContent = label === null ? "" : current;
    rawEl.hidden = label === null;
    hintEl.textContent = currentExtracted ? HINT : unextractedReason(currentOwner);
    element.classList.toggle("die-tooltip-unextracted", !currentExtracted);
  }

  // A rename while the tooltip is open should be visible immediately -- the
  // label editor is a popover, so the pointer never left the canvas and no
  // pointermove is coming to repaint this.
  const unsubscribe = labels.subscribe(paint);

  return {
    show(clientX, clientY, net, extracted = true, owner = "") {
      if (net !== current || extracted !== currentExtracted || owner !== currentOwner) {
        current = net;
        currentExtracted = extracted;
        currentOwner = owner;
        paint();
      }
      element.hidden = false;

      // Positioned against the wrapper, not the page: the panel can be
      // anywhere in the dockview layout.
      const box = wrap.getBoundingClientRect();
      const x = clientX - box.left;
      const y = clientY - box.top;
      const w = element.offsetWidth;
      const h = element.offsetHeight;
      // Offset off the cursor so the tooltip never covers the wire it is
      // describing, and flip back over it at the right and bottom edges.
      const left = x + 16 + w < box.width ? x + 16 : Math.max(4, x - 16 - w);
      const top = y + 14 + h < box.height ? y + 14 : Math.max(4, y - 14 - h);
      element.style.left = `${left}px`;
      element.style.top = `${top}px`;
    },
    hide() {
      element.hidden = true;
      current = null;
    },
    dispose() {
      unsubscribe();
      element.remove();
    },
  };
}