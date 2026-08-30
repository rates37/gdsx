// The right-click menu for a net on the die, shared by the 2D and 3D views.
//
// Both views ask the same question of a wire and should offer the same
// answers, so the entries live here rather than being built twice. The 3D
// view passes one extra item (its layer-by-layer trace sweep); everything
// else is identical, including the order, which is what makes the menu feel
// like one feature of the die view rather than two.
//
// Navigation is deliberately a two-step: `coneRootBus.open` says *what* to
// inspect and `onFocusPanel` says *where to look*. The buses have no handle
// on the workspace -- panels are constructed before it exists -- so focusing
// is injected from `boot.ts`, the same way the Cone Walker already receives
// `onFocusWaveform`.

import { openLabelEditor } from "./chips";
import { openContextMenu, type MenuEntry } from "./context-menu";
import { coneRootBus } from "../store/selection";
import { highlightBus } from "../store/highlight";
import { labels } from "../store/labels";

export interface NetMenuOptions {
  /** Brings a workspace panel to the front. Absent = no navigation items. */
  onFocusPanel?: (id: string) => void;
  /** View-specific entries, appended after the navigation block. */
  extra?: MenuEntry[];
}

/**
 * Opens the menu for `net` at viewport coordinates `(x, y)`. Pass `null` for
 * the net when the click landed on empty die: the menu then offers only what
 * still makes sense there, and nothing at all if there is no selection to
 * clear.
 */
export function openNetMenu(
  x: number,
  y: number,
  net: string | null,
  opts: NetMenuOptions = {},
): void {
  if (net === null) {
    if (highlightBus.pinned() === null) return;
    openContextMenu(x, y, [
      { label: "Clear selection", onSelect: () => highlightBus.pin(null) },
    ]);
    return;
  }

  const entries: MenuEntry[] = [
    {
      label: "Open in Cone Walker",
      onSelect: () => {
        coneRootBus.open(net);
        opts.onFocusPanel?.("cone-walker");
      },
    },
    {
      label: "Show in Netlist Browser",
      onSelect: () => {
        coneRootBus.open(net);
        opts.onFocusPanel?.("netlist");
      },
    },
  ];

  if (opts.extra?.length) entries.push("separator", ...opts.extra);

  entries.push(
    "separator",
    {
      label: labels.get("net", net) === null ? "Label this net…" : "Rename this net…",
      // No element to hang the editor off -- the click was on a canvas -- so
      // it anchors to the point that was clicked.
      onSelect: () => openLabelEditor("net", net, new DOMRect(x, y, 0, 0)),
    },
    {
      label: "Copy name",
      onSelect: () => {
        // Clipboard access can be refused (insecure context, denied
        // permission). Copying a net name is not worth an error dialog.
        void navigator.clipboard?.writeText(net).catch(() => {});
      },
    },
    { label: "Clear selection", onSelect: () => highlightBus.pin(null) },
  );

  openContextMenu(x, y, entries);
}