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

interface BranchNode {
  type: "branch";
  size: number;
  data: GridNode[];
}
interface LeafNode {
  type: "leaf";
  size: number;
  data: unknown;
}
type GridNode = BranchNode | LeafNode;

export class Workspace {
  readonly api: DockviewApi;
  private readonly defs = new Map<string, PanelDef>();

  /**
   * @param rows Default layout, top row first: `rows[0][0]` is the anchor
   *   panel (typically the die view), the rest of `rows[0]` sit in a column
   *   to its right, and any further rows stack below, each spanning the
   *   full width. Panels not named here are still registered (so they can
   *   be added later) but are left out of the default arrangement.
   */
  constructor(
    private readonly container: HTMLElement,
    panels: PanelDef[],
    private readonly rows: string[][],
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
   * Builds `rows` top row first, but *adds panels row by row before column
   * by column*: the anchor starts owning the whole grid, so splitting a
   * further row off it with `direction: "below"` is a full-width split; only
   * once every row exists does it fill in the first row's remaining
   * columns, which by then only ever divides the region the anchor still
   * owns. Doing it the other way around -- columns first -- would leave a
   * later "below" split confined to whichever column it was asked of.
   */
  private defaultLayout(): void {
    const rows = this.rows.filter((row) => row.length > 0 && this.defs.has(row[0]));
    if (rows.length === 0) return;

    const anchor = rows[0][0];
    this.api.addPanel({ id: anchor, component: anchor, title: this.defs.get(anchor)!.title });

    let rowAnchor = anchor;
    for (const row of rows.slice(1)) {
      const first = row[0];
      if (!this.defs.has(first)) continue;
      this.api.addPanel({
        id: first,
        component: first,
        title: this.defs.get(first)!.title,
        position: { referencePanel: rowAnchor, direction: "below" },
      });
      let colAnchor = first;
      for (const id of row.slice(1)) {
        if (!this.defs.has(id)) continue;
        this.api.addPanel({
          id,
          component: id,
          title: this.defs.get(id)!.title,
          position: { referencePanel: colAnchor, direction: "right" },
        });
        colAnchor = id;
      }
      rowAnchor = first;
    }

    let colAnchor = anchor;
    for (const id of rows[0].slice(1)) {
      if (!this.defs.has(id)) continue;
      this.api.addPanel({
        id,
        component: id,
        title: this.defs.get(id)!.title,
        position: { referencePanel: colAnchor, direction: "right" },
      });
      colAnchor = id;
    }

    if (rows.some((row) => row.length > 1) || rows.length > 1) this.balanceSizes(rows);
  }

  /**
   * `addPanel`'s own sizing (`initialWidth`, or calling a group's `setSize`
   * right after adding it) does not stick in this dockview version -- a
   * freshly split group ends up pinned at the library's bare minimum
   * (100px) regardless of what was asked for, and its sibling keeps the
   * rest. Round-tripping the whole tree's sizes through `toJSON`/`fromJSON`
   * does not have that problem: it's the same code path that already
   * restores a user's own drag-resized layout correctly (see `restore`), so
   * this reuses it instead of fighting `addPanel`'s sizing directly.
   *
   * First equalises every split in the tree (which alone fixes the 100px
   * bug everywhere), then biases the two splits an even split leaves too
   * cramped: the anchor panel wants more width than its row-mates, and the
   * top row wants more height than any row below it.
   */
  private balanceSizes(rows: string[][]): void {
    const json = this.api.toJSON();
    const root = json.grid.root as unknown as GridNode;

    const equalize = (node: GridNode): void => {
      if (node.type !== "branch" || node.data.length === 0) return;
      const total = node.data.reduce((sum, child) => sum + child.size, 0);
      const each = Math.floor(total / node.data.length);
      let used = 0;
      node.data.forEach((child, i) => {
        child.size = i === node.data.length - 1 ? total - used : each;
        used += child.size;
        equalize(child);
      });
    };
    equalize(root);

    if (rows.length > 1 && root.type === "branch" && root.data.length >= 2) {
      const total = root.data.reduce((sum, child) => sum + child.size, 0);
      root.data[0].size = Math.round(total * 0.6);
      root.data[1].size = total - root.data[0].size;
    }
    const topRow = rows.length > 1 && root.type === "branch" ? root.data[0] : root;
    if (rows[0].length > 1 && topRow.type === "branch") {
      const total = topRow.data.reduce((sum, child) => sum + child.size, 0);
      topRow.data[0].size = Math.round(total * 0.55);
      const rest = total - topRow.data[0].size;
      const each = Math.floor(rest / (topRow.data.length - 1));
      let used = 0;
      for (let i = 1; i < topRow.data.length; i++) {
        topRow.data[i].size = i === topRow.data.length - 1 ? rest - used : each;
        used += topRow.data[i].size;
      }
    }

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