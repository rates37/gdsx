// "Open panel X, on section Y."
//
// Once a panel hosts more than one former panel, focusing it is no longer
// enough to say where the player should land: the guided walkthrough's
// "labels" step wants the Netlist panel *on its Labels sub-tab*, not on
// whichever sub-tab was last used. `Workspace.focus(id)` handles the panel
// half; this carries the section half the rest of the way, to whichever
// sub-tab host is listening.
//
// Same shape as `coneRootBus` and `highlightBus` -- a fire-and-forget bus
// rather than a return value -- because the sender (the guide, later the menu
// bar) has no handle on the host it is addressing, and the host may not even
// be mounted at the moment the request is made.

export interface PanelRoute {
  panel: string;
  /** Sub-tab id within that panel. Absent = "just the panel, leave the
   *  section alone", which is what plain navigation wants. */
  section?: string;
}

type Listener = (route: PanelRoute) => void;

class PanelRouteBus {
  private readonly listeners = new Set<Listener>();
  /** The last route per panel, so a host that mounts *after* the request was
   *  made still lands in the right place. Without this, a step that focuses a
   *  closed panel would open it on its remembered sub-tab and ignore the
   *  section it asked for. */
  private readonly pending = new Map<string, PanelRoute>();

  open(panel: string, section?: string): void {
    const route: PanelRoute = section === undefined ? { panel } : { panel, section };
    this.pending.set(panel, route);
    for (const listener of this.listeners) listener(route);
  }

  /** The route most recently requested for `panel`, if any. A host calls this
   *  once on mount; it is not cleared, so re-mounting the same panel keeps
   *  honouring the last explicit request. */
  latest(panel: string): PanelRoute | undefined {
    return this.pending.get(panel);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

export const panelRouteBus = new PanelRouteBus();