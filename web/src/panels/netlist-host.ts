// The Netlist panel: the browser and the label glossary, under one tab.
//
// They are about the same objects. The browser lists every instance and net
// the extractor recovered; the glossary lists the names the player has since
// given some of those instances and nets. Keeping them as two top-level tabs
// made the player carry that relationship themselves, and cost a slot in a tab
// strip that had already overflowed.

import type { DesignClient } from "../design/client";
import { mountNetlistBrowser } from "./netlist-panel";
import { mountLabels } from "./labels-panel";
import { subTabHost } from "./mounts";
import type { PanelDef } from "../workspace/workspace";

export const NETLIST_SECTIONS = ["browser", "labels"] as const;

export function netlistPanel(designReady: Promise<DesignClient>): PanelDef {
  return {
    id: "netlist",
    title: "Netlist",
    render(container: HTMLElement) {
      return subTabHost(container, "netlist", [
        {
          id: "browser",
          title: "Browser",
          mount: (host) => mountNetlistBrowser(host, designReady),
          // Both sections hold state that is expensive to rebuild (the browser
          // re-fetches 728 instances and 5,000 nets from Pyodide) or annoying
          // to lose (selection, scroll, filter). Neither holds a GPU context.
          keepAlive: true,
        },
        {
          id: "labels",
          title: "Labels",
          mount: (host) => mountLabels(host),
          keepAlive: true,
        },
      ]);
    },
  };
}