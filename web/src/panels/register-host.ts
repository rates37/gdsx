// The Registers panel: what the flops of this design are, and which of them
// latch one way.
//
// Two sections rather than the three top-level tabs this used to be. The
// Register Decoder is gone entirely -- its weights, orbit and select-value
// tools now sit in the detail pane of whichever register is selected (see
// `group-tools.ts`), and its "type some flop names" entry point survives as
// the ad-hoc group row beside the discovered list. That was the whole panel,
// and on its own it opened as a blank screen next to a tab already listing
// the flop names it wanted you to type.
//
// Sticky Flops stays a sibling section rather than moving into the detail
// pane: it is a flat list about *every* flop, unrelated to whichever register
// happens to be selected.

import type { DesignClient } from "../design/client";
import { registerInspectorPanel } from "./register-inspector-panel";
import { mountStickyFlops } from "./sticky-flops-panel";
import { subTabHost } from "./mounts";
import type { WinConditionSource } from "../design/win-condition";
import type { PanelDef } from "../workspace/workspace";

export const REGISTER_SECTIONS = ["registers", "sticky"] as const;

export interface RegisterHostOptions {
  designReady: Promise<DesignClient>;
  puzzleId: string;
  successNet: string | null;
  winCondition: WinConditionSource;
  clockPort: string;
  resetPort: string | null;
}

export function registerPanel(options: RegisterHostOptions): PanelDef {
  const inspector = registerInspectorPanel({
    designReady: options.designReady,
    puzzleId: options.puzzleId,
    clockPort: options.clockPort,
    resetPort: options.resetPort,
  });
  return {
    id: "register-inspector",
    title: "Registers",
    render(container: HTMLElement) {
      return subTabHost(container, "register-inspector", [
        {
          id: "registers",
          title: "Registers",
          // Both sections cost a Python round trip to populate and hold
          // selection state worth keeping across a flip.
          keepAlive: true,
          mount: (host) => inspector.render(host),
        },
        {
          id: "sticky",
          title: "Sticky Flops",
          keepAlive: true,
          mount: (host) =>
            mountStickyFlops(host, {
              designReady: options.designReady,
              puzzleId: options.puzzleId,
              successNet: options.successNet,
              winCondition: options.winCondition,
            }),
        },
      ]);
    },
  };
}