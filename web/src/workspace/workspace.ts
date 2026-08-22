// The panel workspace shell: a dockview grid whose layout persists to
// localStorage. Panels register a `component` id -> factory; the shell
// itself knows nothing about what a die view or a net list is.
//
// Per game-plan.md §3, panels are floatable/resizable/dockable and the
// layout persists per browser, not per puzzle -- a player who drags the
// notebook wide once should not have to redo that every session.

import {
  createDockview,
  themeAbyss,
  type DockviewApi,
  type IContentRenderer,
  type CreateComponentOptions,
  type SerializedDockview,
} from "dockview-core";
import "./dockview.css";
import { attachToolbar, type LevelPicker } from "./toolbar.ts";

// v2: the default arrangement changed from a four-row grid to a single
// tabbed group. The key is versioned so a returning player gets the new
// default once rather than keeping a saved copy of the old one -- their own
// deliberate splits, made after this lands, still persist as before.
const STORAGE_KEY = "gdsx.workspace.layout.v2";

export interface PanelDef {
  id: string;
  title: string;
  /** Builds the panel's DOM content. Called once per panel instance. */
  render: (container: HTMLElement) => { dispose?: () => void };
}

export class Workspace {
  readonly api: DockviewApi;
  private readonly defs = new Map<string, PanelDef>();
  private readonly status: (text: string) => void;

  /**
   * @param order Default layout: every named panel as a tab in one group,
   *   left to right, with the first one active. Panels not named here are
   *   still registered (so they can be added later) but are left out of the
   *   default arrangement.
   * @param levels The level picker's contents, if there is more than one
   *   puzzle to offer. The shell hands it straight to the toolbar; it does
   *   not itself know which puzzle is loaded.
   */
  constructor(
    container: HTMLElement,
    panels: PanelDef[],
    private readonly order: string[],
    levels?: LevelPicker,
  ) {
    for (const p of panels) this.defs.set(p.id, p);

    const dockMount = document.createElement("div");
    dockMount.className = "gdsx-dock-mount";
    container.append(dockMount);

    this.api = createDockview(dockMount, {
      theme: themeAbyss,
      createComponent: (options: CreateComponentOptions): IContentRenderer => {
        const def = this.defs.get(options.name);
        if (!def) throw new Error(`no panel registered for component "${options.name}"`);
        const element = document.createElement("div");
        element.className = "gdsx-panel";
        let handle: { dispose?: () => void } | null = null;
        return {
          element,
          init: () => {
            handle = def.render(element);
          },
          dispose: () => handle?.dispose?.(),
        };
      },
    });

    if (!this.restore()) this.defaultLayout();

    this.api.onDidLayoutChange(() => this.persist());
    window.addEventListener("beforeunload", () => this.persist());

    this.status = attachToolbar(container, this, levels);
  }

  /** The toolbar's status readout -- the coverage percentage, per §3's title
   *  bar. A panel calls this; nothing reads it back. */
  setStatus(text: string): void {
    this.status(text);
  }

  /** Every registered panel that is not currently open, title included --
   *  what "reopen tab" offers. */
  closedPanels(): { id: string; title: string }[] {
    const open = new Set(this.api.panels.map((p) => p.id));
    return [...this.defs.values()].filter((d) => !open.has(d.id)).map((d) => ({ id: d.id, title: d.title }));
  }

  /** Reopens a closed panel as a tab next to whichever panel is active, so
   *  it comes back where you're looking rather than in a fresh sliver. */
  reopen(id: string): void {
    const def = this.defs.get(id);
    if (!def || this.api.panels.some((p) => p.id === id)) return;
    const active = this.api.activePanel;
    this.api.addPanel({
      id: def.id,
      component: def.id,
      title: def.title,
      position: active ? { referencePanel: active.id, direction: "within" } : undefined,
    });
  }

  /** Fires whenever the set of open panels (or their arrangement) changes -- what the toolbar redraws on. */
  onChange(fn: () => void): () => void {
    const disposable = this.api.onDidLayoutChange(fn);
    return () => disposable.dispose();
  }

  /**
   * The default arrangement: every panel in `order` as a tab in a single
   * group, first one active. game-plan.md §3 sketches a multi-pane cockpit,
   * and the workspace can still be dragged into one -- but opening on a
   * dozen panes at once gives every one of them too little room to be read,
   * so the shipped default is one panel at a time and the tab strip as the
   * way between them.
   */
  private defaultLayout(): void {
    const ids = this.order.filter((id) => this.defs.has(id));
    if (ids.length === 0) return;

    const [first, ...rest] = ids;
    this.api.addPanel({ id: first, component: first, title: this.defs.get(first)!.title });
    for (const id of rest) {
      this.api.addPanel({
        id,
        component: id,
        title: this.defs.get(id)!.title,
        position: { referencePanel: first, direction: "within" },
      });
    }
    this.api.getPanel(first)?.api.setActive();
  }

  private persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.api.toJSON()));
    } catch {
      // Storage full or disabled -- the workspace still works, it just
      // won't remember the layout next time. Not worth surfacing to the
      // player.
    }
  }

  private restore(): boolean {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    try {
      const data = JSON.parse(raw) as SerializedDockview;
      this.api.fromJSON(data);
      return this.api.panels.length > 0;
    } catch (err) {
      console.warn("gdsx: discarding unreadable saved layout", err);
      localStorage.removeItem(STORAGE_KEY);
      return false;
    }
  }

  resetLayout(): void {
    localStorage.removeItem(STORAGE_KEY);
    this.api.clear();
    this.defaultLayout();
  }
}