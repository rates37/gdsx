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

/** The bit of a die pick this menu needs: the net's name, and whether the
 *  netlist knows it at all. Both views' hit types are assignable to this. */
export interface NetPick {
  name: string;
  /** False for traced metal that reaches no logic cell pin. It is drawn and
   *  named like any other net, but extraction dropped it, so every panel
   *  that asks Python about a net would answer "no such net". */
  extracted: boolean;
}

export interface NetMenuOptions {
  /** Brings a workspace panel to the front. Absent = no navigation items. */
  onFocusPanel?: (id: string) => void;
  /** View-specific entries, appended after the navigation block. */
  extra?: MenuEntry[];
}

/**
 * Opens the menu for `pick` at viewport coordinates `(x, y)`. Pass `null` for
 * the pick when the click landed on empty die: the menu then offers only what
 * still makes sense there, and nothing at all if there is no selection to
 * clear.
 */
export function openNetMenu(
  x: number,
  y: number,
  pick: NetPick | null,
  opts: NetMenuOptions = {},
): void {
  if (pick === null) {
    if (highlightBus.pinned() === null) return;
    openContextMenu(x, y, [
      { label: "Clear selection", onSelect: () => highlightBus.pin(null) },
    ]);
    return;
  }

  const net = pick.name;

  // Not every piece of metal on the die survives extraction: a net that
  // reaches no logic cell's pin -- nearly always li1 wiring inside a standard
  // cell -- is drawn and pickable but absent from the netlist. Offering to
  // open it in a panel that can only answer "no such net" is worse than
  // saying so here, so the two navigation items stay visible and greyed,
  // with the reason in their place.
  const entries: MenuEntry[] = pick.extracted
    ? [
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
      ]
    : [
        { label: "Open in Cone Walker", onSelect: () => {}, disabled: true },
        { label: "Show in Netlist Browser", onSelect: () => {}, disabled: true },
        {
          label: "not in the netlist — reaches no cell pin",
          onSelect: () => {},
          disabled: true,
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