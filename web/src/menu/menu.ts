// The level menu: the screen a bare URL lands on.
//
// It is a screen, not a panel. It is never registered with Workspace, never
// appears in the View/Analyse/Experiment menus, and imports nothing from
// panels/ -- the workspace and everything it drags in (dockview, three.js,
// Pyodide, the render bundle, the gate tape) live behind main.ts's other
// dynamic import and are not in this module's graph. That is what lets the
// menu paint on `/puzzles/index.json` alone.
//
// Every card is a real `<a href="?puzzle=...">`: the routing rule and the
// click target are then the same thing, and open-in-new-tab, middle click and
// the back button all work without any of them being handled here. Following
// a card does not itself write "last played" -- boot.ts does that when the
// puzzle actually opens, so Continue means a puzzle that was opened, not one
// that was clicked at.

import { findPuzzle, type PuzzleDescriptor } from "../puzzles/catalog.ts";
import { allProgress, type ProgressRecord } from "../store/progress.ts";
import { continueEntry, menuEntries, type MenuEntry } from "./entries.ts";

export interface MenuOptions {
  catalog: PuzzleDescriptor[];
  /** The last-played id from storage, in whatever spelling was saved. */
  lastPlayed?: string | null;
  /** Injectable for tests; defaults to the real progress store. */
  progress?: ProgressRecord[];
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chip(text: string, extra?: string): HTMLElement {
  return el("span", extra ? `menu-chip ${extra}` : "menu-chip", text);
}

function card(entry: MenuEntry): HTMLElement {
  const item = el("li", entry.solved ? "menu-card menu-card-is-solved" : "menu-card");
  item.dataset.puzzle = entry.id;

  const link = el("a", "menu-card-link");
  link.href = entry.href;

  const head = el("div", "menu-card-head");
  head.append(el("h2", "menu-card-title", entry.title));
  if (entry.solved) head.append(el("span", "menu-card-tick", "✓"));
  link.append(head);

  const meta = el("div", "menu-card-meta");
  if (entry.difficulty) meta.append(chip(entry.difficulty));
  if (entry.par) meta.append(chip(entry.par));
  if (entry.goal) meta.append(chip(entry.goal, "menu-chip-goal"));
  link.append(meta);

  link.append(el("p", "menu-card-blurb", entry.blurb));
  item.append(link);

  // The foot is always drawn, solved or not: it is where 19.3's per-card
  // clear button goes, and a row that appears only on solved cards would make
  // the grid ragged.
  const foot = el("div", "menu-card-foot");
  if (entry.solved) {
    foot.append(
      el("span", "menu-card-solved", entry.solvedOn ? `✓ solved ${entry.solvedOn}` : "✓ solved"),
    );
  }
  if (entry.attempts > 0) {
    const plural = entry.attempts === 1 ? "attempt" : "attempts";
    foot.append(el("span", "menu-card-attempts", `${entry.attempts} ${plural}`));
  }
  foot.append(el("div", "menu-card-actions"));
  item.append(foot);

  return item;
}

function continueBanner(entry: MenuEntry): HTMLElement {
  const link = el("a", "menu-continue");
  link.href = entry.href;
  link.append(el("span", "menu-continue-label", "CONTINUE"));
  link.append(el("span", "menu-continue-title", entry.title));
  const meta = [entry.solved ? "solved" : null, entry.par].filter(Boolean).join(" · ");
  if (meta) link.append(el("span", "menu-continue-meta", meta));
  return link;
}

/**
 * Builds the menu into `host` and returns a handle that can redraw it.
 *
 * `redraw` is here for 19.3: clearing a puzzle's progress has to update its
 * card without a page reload, and the card is a pure function of the catalog
 * and the progress records.
 */
export function mountMenu(host: HTMLElement, opts: MenuOptions): { redraw: () => void } {
  const screen = el("div", "menu-screen");
  const inner = el("div", "menu-inner");
  screen.append(inner);
  host.append(screen);

  const draw = (): void => {
    inner.replaceChildren();

    const entries = menuEntries(opts.catalog, opts.progress ?? allProgress());
    // Resolved through the catalog rather than compared as a string: a saved
    // id may be a puzzle's directory name, which is a legal `?puzzle=` value.
    const lastPlayed = findPuzzle(opts.catalog, opts.lastPlayed)?.id ?? null;
    const resume = continueEntry(entries, lastPlayed);
    const solved = entries.filter((entry) => entry.solved).length;

    const head = el("div", "menu-head");
    head.append(el("h1", "menu-title", "DIESHARK"));
    head.append(
      el(
        "p",
        "menu-tagline",
        `${entries.length} levels — a gate-level netlist, a lock, and no source. ` +
          `${solved} solved.`,
      ),
    );
    // 19.3's settings affordance mounts into this.
    head.append(el("div", "menu-head-actions"));
    inner.append(head);

    if (resume) inner.append(continueBanner(resume));

    inner.append(el("div", "menu-section-label", "LEVELS"));
    const grid = el("ul", "menu-grid");
    for (const entry of entries) grid.append(card(entry));
    inner.append(grid);

    const foot = el("div", "menu-foot");
    foot.append(
      document.createTextNode("Every level is linkable: a URL like "),
    );
    foot.append(el("code", undefined, `?puzzle=${entries[0]?.id ?? "original-puzzle"}`));
    foot.append(
      document.createTextNode(
        " opens it directly, and the level picker in the toolbar switches between them without" +
          " coming back here.",
      ),
    );
    inner.append(foot);
  };

  draw();
  document.title = "DIESHARK — levels";
  return { redraw: draw };
}