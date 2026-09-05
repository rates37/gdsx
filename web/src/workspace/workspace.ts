// The panel workspace shell: a dockview grid whose layout persists to
// localStorage. Panels register a `component` id -> factory; the shell
// itself knows nothing about what a die view or a net list is.
//
// Panels are floatable/resizable/dockable and the
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
import {
  attachToolbar,
  type Readiness,
  type SolvedState,
  type ToolbarHandle,
  type ToolbarOptions,
} from "./toolbar.ts";

// v2: the default arrangement changed from a four-row grid to a single
// tabbed group. The key is versioned so a returning player gets the new
// default once rather than keeping a saved copy of the old one -- their own
// deliberate splits, made after this lands, still persist as before.
//
// v3: the blob records which puzzle it was saved on. The layout is still one
// arrangement per browser rather than one per puzzle -- dragging the notebook
// wide once should not have to be redone for every level -- but restoring it
// verbatim onto a *different* puzzle silently defeated that puzzle's tool
// gating (web/src/puzzles/tools.ts). Opening First Light and then Warm Start
// gave Warm Start a Sequence Editor and an Experiments tab it does not ask
// for and cannot use: it has no input tracks to paint. `defaultPanelIds` only
// ever reached a player's very first puzzle, or one who had just pressed
// "reset layout".
//
// So the arrangement carries across, and the *set of panels* is reconciled
// against the puzzle being opened -- but only when the layout came from
// somewhere else. A layout saved on this same puzzle is the player's own
// choice about this puzzle and is restored untouched, including any panel
// they opened from a menu that its manifest never named.
const STORAGE_KEY = "gdsx.workspace.layout.v3";

/** What v3 stores: the dockview blob, and the puzzle it was arranged on. */
interface SavedLayout {
  /** Absent on a blob written before this field existed, which is then
   *  treated as "from somewhere else" and reconciled. */
  puzzleId?: string;
  layout: SerializedDockview;
}

export interface PanelDef {
  id: string;
  title: string;
  /** Builds the panel's DOM content. Called once per panel instance. */
  render: (container: HTMLElement) => { dispose?: () => void };
}

/** One menu bar dropdown: a label and the panel ids it lists, in order.
 *  The Notebook is deliberately never named in a group -- it has its own
 *  toolbar button. */
export interface MenuGroup {
  label: string;
  items: string[];
}

/** Everything the shell needs beyond its panels. `menus` and
 *  `defaultPanelIds` are the two the workspace itself reads; the rest it
 *  hands straight to the toolbar. */
export interface WorkspaceOptions extends ToolbarOptions {
  /** The menu bar: each group's label and the panel ids it lists. Every panel
   *  named here is reachable from a menu regardless of `defaultPanelIds` --
   *  gating only ever changes what opens by default, never what is reachable.
   *  A group may name a panel that does not exist yet. */
  menus: MenuGroup[];
  /** Default layout: every named panel as a tab in one group, left to right,
   *  with the first one active. Deliberately a plain id list rather than
   *  derived from `menus` -- the caller (boot.ts) may need to open a panel by
   *  default that no menu names, e.g. the Notebook, or fewer than every panel
   *  a puzzle's `tools_enabled` does not ask for (web/src/puzzles/tools.ts).
   *  Filtered to panels this build actually registers. */
  defaultPanelIds: string[];
  /** The puzzle being opened. Only used to tell "this arrangement is the
   *  player's own choice about this puzzle" from "this arrangement arrived
   *  from another level and its panel set should be reconciled against what
   *  this one asks for". See the v3 note above `STORAGE_KEY`. */
  puzzleId: string;
}

export class Workspace {
  readonly api: DockviewApi;
  private readonly defs = new Map<string, PanelDef>();
  private readonly toolbar: ToolbarHandle;
  private readonly menus: MenuGroup[];
  private readonly order: string[];
  private readonly puzzleId: string;

  /**
   * Everything in `opts` beyond `menus`, `defaultPanelIds` and `puzzleId` is
   * handed straight to the toolbar -- the level picker, the guide and
   * briefing buttons, the submit surface, the way back to the menu and the
   * solved marker. The shell still does not know what a puzzle *is*; it only knows
   * which one this layout belongs to.
   */
  constructor(container: HTMLElement, panels: PanelDef[], opts: WorkspaceOptions) {
    for (const p of panels) this.defs.set(p.id, p);
    this.menus = opts.menus;
    this.order = opts.defaultPanelIds.filter((id) => this.defs.has(id));
    this.puzzleId = opts.puzzleId;

    const dockMount = document.createElement("div");
    dockMount.className = "gdsx-dock-mount";
    container.append(dockMount);

    this.api = createDockview(dockMount, {
      theme: themeAbyss,
      createComponent: (options: CreateComponentOptions): IContentRenderer => {
        const def = this.defs.get(options.name);
        // A saved layout can name a panel this build no longer has -- a merged
        // or renamed one. Throwing here makes dockview tear the *whole*
        // deserialisation down and revert, so one stale id would cost the
        // player their entire arrangement. `restore` prunes these before
        // `fromJSON`; this is the belt to that pair of braces, covering
        // anything the prune misses (floating and popout groups).
        if (!def) {
          const element = document.createElement("div");
          element.className = "gdsx-panel gdsx-panel-missing";
          element.textContent = `“${options.name}” is no longer part of this build.`;
          return { element, init: () => {} };
        }
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

    this.toolbar = attachToolbar(container, this, this.menus, opts);
  }

  /** The readiness pill: `booting`, then `ready`, then out of the way. Driven
   *  by boot.ts, which is the only thing that knows when the design handle
   *  exists. */
  setReadiness(state: Readiness): void {
    this.toolbar.readiness(state);
  }

  /** Show the solved marker in the toolbar. Called at load for a puzzle
   *  already solved, and again the moment a submission is accepted -- the bar
   *  should not still be claiming an unsolved puzzle while the popover says
   *  "✓ accepted". */
  setSolved(state: SolvedState): void {
    this.toolbar.solved(state);
  }

  /** A registered panel's title, or undefined if this build does not have
   *  it -- the menu bar skips a group's item when its id names a panel that
   *  does not exist yet. */
  panelTitle(id: string): string | undefined {
    return this.defs.get(id)?.title;
  }

  /** Whether a registered panel is currently open -- the menu bar's
   *  checkmark and the notebook button's active state both track this. */
  isOpen(id: string): boolean {
    return this.api.panels.some((p) => p.id === id);
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

  /** Brings a panel to the front, reopening it first if the player has closed
   *  it. What the guided walkthrough drives; nothing else should need to move
   *  the player's focus for them. */
  focus(id: string): void {
    if (!this.defs.has(id)) return;
    if (!this.api.panels.some((p) => p.id === id)) this.reopen(id);
    this.api.getPanel(id)?.api.setActive();
  }

  /** Fires whenever the set of open panels (or their arrangement) changes -- what the toolbar redraws on. */
  onChange(fn: () => void): () => void {
    const disposable = this.api.onDidLayoutChange(fn);
    return () => disposable.dispose();
  }

  /**
   * The default arrangement: every panel in `order` as a tab in a single
   * group, first one active. A multi-pane cockpit is the sketched intent,
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
      const saved: SavedLayout = { puzzleId: this.puzzleId, layout: this.api.toJSON() };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage full or disabled -- the workspace still works, it just
      // won't remember the layout next time. Not worth surfacing to the
      // player.
    }
  }

  /**
   * Drops saved panels whose component this build no longer registers.
   *
   * dockview's own deserialiser treats an unknown component as fatal: it
   * unwinds every group it has built, calls `clear()` and rethrows, so a
   * single stale id costs the player their whole arrangement. Pruning first
   * turns that into "the panel that went away is gone, everything else is
   * where you left it" -- which is what a merged or renamed panel should cost.
   *
   * The group's own view list and active panel are left alone on purpose:
   * dockview already filters views down to the panels that survived, and
   * re-opens a group's last panel when its `activePanel` is not among them.
   */
  private prune(data: SerializedDockview, keep: (component: string) => boolean): SerializedDockview {
    const panels = data.panels as Record<string, { contentComponent?: string }> | undefined;
    if (!panels) return data;
    const dropped = Object.keys(panels).filter((id) => {
      const component = panels[id]?.contentComponent;
      return component !== undefined && !keep(component);
    });
    if (dropped.length === 0) return data;
    console.warn(`gdsx: dropping ${dropped.join(", ")} from the saved layout`);
    for (const id of dropped) delete panels[id];
    return data;
  }

  private restore(): boolean {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    try {
      const saved = JSON.parse(raw) as SavedLayout;
      if (!saved?.layout) return false;
      // A layout arranged on this same puzzle is the player's own decision
      // about this puzzle: every panel in it stays, including one they opened
      // from a menu that the manifest never named. One that arrived from
      // another level carries the arrangement but not the panel set -- that
      // is what `defaultPanelIds` is for, and restoring verbatim was silently
      // overriding it.
      const ownLayout = saved.puzzleId === this.puzzleId;
      const allowed = new Set(this.order);
      const data = this.prune(saved.layout, (component) =>
        this.defs.has(component) && (ownLayout || allowed.has(component)),
      );
      this.api.fromJSON(data);
      if (this.api.panels.length === 0) return false;
      // …and the reconciliation runs both ways: a puzzle that asks for a panel
      // the borrowed arrangement never had (the Sequence Editor, for a puzzle
      // whose answer is a stimulus) must still open with it.
      if (!ownLayout) {
        for (const id of this.order) this.reopen(id);
      }
      return true;
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