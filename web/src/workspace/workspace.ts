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

const STORAGE_KEY = "gdsx.workspace.layout.v1";

export interface PanelDef {
  id: string;
  title: string;
  /** Builds the panel's DOM content. Called once per panel instance. */
  render: (container: HTMLElement) => { dispose?: () => void };
}

export class Workspace {
  readonly api: DockviewApi;
  private readonly defs = new Map<string, PanelDef>();

  constructor(
    private readonly container: HTMLElement,
    panels: PanelDef[],
  ) {
    for (const p of panels) this.defs.set(p.id, p);

    this.api = createDockview(container, {
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
  }

  /**
   * The die view anchors the left; every other panel stacks in a column to
   * its right, first one across from it, the rest beneath -- so a second
   * panel (game-plan.md §3 puts the netlist browser there) sits side by
   * side with the die, and further panels (the cone walker) don't crowd it
   * out, they queue up underneath where they're one click away.
   */
  private defaultLayout(): void {
    const ids = [...this.defs.keys()];
    if (ids.length === 0) return;

    const first = ids[0];
    this.api.addPanel({ id: first, component: first, title: this.defs.get(first)!.title });

    let columnAnchor: string | undefined;
    for (const id of ids.slice(1)) {
      const def = this.defs.get(id)!;
      this.api.addPanel({
        id,
        component: id,
        title: def.title,
        position: columnAnchor
          ? { referencePanel: columnAnchor, direction: "below" }
          : { referencePanel: first, direction: "right" },
      });
      columnAnchor = id;
    }

    if (ids.length > 1) this.widenFirstColumn();
  }

  /**
   * `addPanel`'s own sizing (`initialWidth`, or calling a group's
   * `setSize` right after adding it) does not stick in this dockview
   * version -- the freshly split group ends up pinned at the library's
   * bare minimum (100px) regardless of what was asked for, and the die
   * view keeps the rest. Round-tripping the two top-level split sizes
   * through `toJSON`/`fromJSON` does not have that problem: it's the same
   * code path that already restores a user's own drag-resized layout
   * correctly (see `restore`), so this reuses it instead of fighting
   * `addPanel`'s sizing directly.
   */
  private widenFirstColumn(): void {
    const json = this.api.toJSON();
    const branches = (json.grid.root as { data: { size: number }[] }).data;
    if (branches.length < 2) return;
    const total = branches.reduce((sum, b) => sum + b.size, 0);
    branches[0].size = Math.round(total * 0.58);
    branches[1].size = total - branches[0].size;
    this.api.fromJSON(json);
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