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
//
// It is also where progress is managed, because it is the only screen that
// can see every puzzle at once. Everything it deletes goes through
// store/progress.ts, which owns the `gdsx.*` namespace; this file never names
// a storage key.

import { findPuzzle, lastPlayedId, urlFor, type PuzzleDescriptor } from "../puzzles/catalog.ts";
import {
  allProgress,
  clearAll,
  clearPuzzle,
  storageUsage,
  type ProgressRecord,
  type StorageUsage,
} from "../store/progress.ts";
import { plateElement } from "../puzzles/plate.ts";
import { confirmDestructive } from "./confirm.ts";
import { continueEntry, menuEntries, type MenuEntry } from "./entries.ts";

export interface MenuOptions {
  catalog: PuzzleDescriptor[];
  /** The last-played id, in whatever spelling was saved. Read from storage on
   *  every redraw when not given -- clearing progress deletes it, and the
   *  Continue banner has to go with it. */
  lastPlayed?: string | null;
  /** Injectable for tests; defaults to the real progress store. */
  progress?: ProgressRecord[];
}

/**
 * Where the written guides live.
 */
const MANUAL_URL = "https://github.com/rates37/gdsx/blob/main/docs/manual.md";

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

/** Sizes as the storage quota counts them. Rounded hard: this is a "nothing
 *  has run away" reassurance, not an accounting figure. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function items(usage: StorageUsage): string {
  const plural = usage.keys === 1 ? "item" : "items";
  return `${usage.keys} saved ${plural}, ${formatBytes(usage.bytes)}`;
}

/**
 * "Clear this puzzle", offered only when there is something to clear.
 *
 * The confirmation names the puzzle, lists what a clear takes with it, and
 * says how much of it there is -- a player is being asked to throw away hours
 * of their own work, and "are you sure?" does not describe that.
 */
function clearButton(entry: MenuEntry, usage: StorageUsage, onDone: () => void): HTMLElement {
  const button = el("button", "menu-card-clear", "clear");
  button.type = "button";
  button.title = `delete your saved work on ${entry.title} (${items(usage)})`;
  button.addEventListener("click", () => {
    void confirmDestructive({
      title: `Clear ${entry.title}?`,
      body: [
        `This deletes everything you have done on ${entry.title}: notebook claims, ` +
          `labels, the model you built, saved evidence, sticky-flop ` +
          `classifications, the sequence you were driving, and its solved record.`,
        `${items(usage)}. Your other levels, your panel layout and your app settings ` +
          `are not touched.`,
        "This cannot be undone.",
      ],
      confirmLabel: `clear ${entry.title}`,
    }).then((confirmed) => {
      if (!confirmed) return;
      clearPuzzle(entry.id);
      onDone();
    });
  });
  return button;
}

/**
 * "copy link" — the toolbar's picker lists puzzles by title and the URL takes
 * ids, and nothing else connects the two, so a player has no way to bookmark
 * or share a level without reading the page source. Every card gets one,
 * unconditionally: unlike `clear`, there is no state to be absent.
 */
function copyLinkButton(entry: MenuEntry): HTMLElement {
  const button = el("button", "menu-card-copylink", "copy link");
  button.type = "button";
  button.title = `copy a link to ${entry.title}`;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    navigator.clipboard?.writeText(urlFor(entry.id)).catch(() => {});
    button.textContent = "copied";
    setTimeout(() => (button.textContent = "copy link"), 1000);
  });
  return button;
}

function card(entry: MenuEntry, onCleared: () => void): HTMLElement {
  const item = el("li", entry.solved ? "menu-card menu-card-is-solved" : "menu-card");
  item.dataset.puzzle = entry.id;

  const link = el("a", "menu-card-link");
  link.href = entry.href;
  link.append(plateElement(entry.id, "menu-card-plate"));

  // The body is the middle column: what the level is called and what it is.
  // The meta chips move out of it into a column of their own, so a long blurb
  // and a long chip list stop competing for the same line.
  const body = el("div", "menu-card-body");
  const head = el("div", "menu-card-head");
  head.append(el("h2", "menu-card-title", entry.title));
  if (entry.solved) head.append(el("span", "menu-card-tick", "solved"));
  body.append(head);
  body.append(el("p", "menu-card-blurb", entry.blurb));
  link.append(body);

  const meta = el("div", "menu-card-meta");
  if (entry.difficulty) meta.append(chip(entry.difficulty));
  if (entry.par) meta.append(chip(entry.par));
  if (entry.goal) meta.append(chip(entry.goal, "menu-chip-goal"));
  link.append(meta);

  item.append(link);

  // The foot is always drawn, cleared or not, so the grid does not go ragged
  // as puzzles are played.
  const foot = el("div", "menu-card-foot");
  if (entry.solved) {
    foot.append(
      el("span", "menu-card-solved", entry.solvedOn ? `✓ solved ${entry.solvedOn}` : "✓ solved"),
    );
  }
  if (entry.score !== null) {
    // The number only. What it is made of is in that puzzle's write-up, which
    // needs the design loaded -- and this screen deliberately loads none of it.
    foot.append(el("span", "menu-card-score", `${entry.score} points`));
  }
  if (entry.attempts > 0) {
    const plural = entry.attempts === 1 ? "attempt" : "attempts";
    foot.append(el("span", "menu-card-attempts", `${entry.attempts} ${plural}`));
  }

  const actions = el("div", "menu-card-actions");
  actions.append(copyLinkButton(entry));
  // Only when the puzzle owns something. A clear button on a level nobody has
  // opened is an affordance that does nothing, and it would also be the only
  // thing on an otherwise untouched card.
  const usage = storageUsage(entry.id);
  if (usage.keys > 0) actions.append(clearButton(entry, usage, onCleared));
  foot.append(actions);
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
 * The settings affordance: what the game is holding, and the way to throw all
 * of it away.
 *
 * The usage line is the unobtrusive half -- storage here is bounded and small,
 * and a player who has solved everything should be able to confirm that
 * nothing has run away without opening devtools.
 */
function settingsButton(puzzleCount: number, onDone: () => void): HTMLElement {
  const wrap = el("div", "menu-settings");

  const button = el("button", "menu-settings-btn", "settings");
  button.type = "button";
  button.title = "storage and progress";

  const popover = el("div", "menu-settings-popover");
  popover.hidden = true;

  const usage = storageUsage();
  popover.append(el("div", "menu-settings-heading", "STORAGE"));
  popover.append(el("div", "menu-settings-usage", items(usage)));
  popover.append(
    el(
      "div",
      "menu-settings-note",
      "Everything the game saves lives in this browser: your notebooks, labels, " +
        "models, evidence, layout and solved records. Nothing is sent anywhere.",
    ),
  );

  const clearAllBtn = el("button", "menu-settings-clear", "clear all progress…");
  clearAllBtn.type = "button";
  clearAllBtn.addEventListener("click", () => {
    void confirmDestructive({
      title: "Clear all progress?",
      body: [
        `This deletes your progress on all ${puzzleCount} puzzles — every notebook, ` +
          `label, model, saved evidence file, sticky-flop classification, ` +
          `driven sequence and solved record.`,
        "It also clears the app's own state: your saved panel layout, the guided " +
          "walkthrough's progress, your die view preferences, and which level you " +
          "last opened.",
        `${items(usage)}. This cannot be undone.`,
      ],
      confirmLabel: "clear everything",
    }).then((confirmed) => {
      if (!confirmed) return;
      clearAll();
      onDone();
    });
  });
  popover.append(clearAllBtn);

  const close = (): void => {
    popover.hidden = true;
    button.classList.remove("open");
  };
  button.addEventListener("click", () => {
    popover.hidden = !popover.hidden;
    button.classList.toggle("open", !popover.hidden);
  });
  // Same convention as the workspace toolbar's menus: an outside click or
  // Escape closes it.
  document.addEventListener("click", (event) => {
    if (!popover.hidden && !wrap.contains(event.target as Node)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  wrap.append(button, popover);
  return wrap;
}

/**
 * Builds the menu into `host` and returns a handle that can redraw it.
 *
 * `redraw` is what a clear calls: a card is a pure function of the catalog and
 * what is in storage, so the screen shows the result immediately rather than
 * asking the player to reload to find out whether it worked. Clearing cannot
 * disturb a running workspace from here -- that is a different document -- and
 * a workspace open on the cleared puzzle in another tab reloads itself
 * (progress.ts's `onCleared`, wired up in boot.ts).
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
    const saved = opts.lastPlayed !== undefined ? opts.lastPlayed : lastPlayedId();
    const resume = continueEntry(entries, findPuzzle(opts.catalog, saved)?.id ?? null);
    const solved = entries.filter((entry) => entry.solved).length;

    const head = el("div", "menu-head");
    head.append(el("h1", "menu-title", "GateCrasher"));
    head.append(el("p", "menu-tagline", `${entries.length} levels · ${solved} solved`));
    const actions = el("div", "menu-head-actions");
    const manualLink = el("a", "menu-manual-link", "manual");
    manualLink.href = MANUAL_URL;
    manualLink.target = "_blank";
    manualLink.rel = "noopener";
    manualLink.title = "how to play, panel by panel -- opens in a new tab";
    actions.append(manualLink);
    actions.append(settingsButton(entries.length, draw));
    head.append(actions);
    inner.append(head);

    // An empty catalog is a build that has not been synced, not a game with
    // no levels, and the screen says which. It used to be impossible to reach
    // because `loadCatalog` invented a puzzle rather than returning nothing;
    // that copy of the original puzzle's data is gone, so this is now the
    // honest end of that path.
    if (entries.length === 0) {
      const empty = el("div", "menu-empty");
      empty.append(el("p", "menu-empty-title", "No puzzles are bundled with this build."));
      const note = el("p", "menu-empty-note");
      note.append(document.createTextNode("Bake or sync them with "));
      note.append(el("code", undefined, "npm run sync-assets"));
      note.append(document.createTextNode(" and reload."));
      empty.append(note);
      inner.append(empty);
      return;
    }

    if (resume) inner.append(continueBanner(resume));

    inner.append(el("div", "menu-section-label", "LEVELS"));
    const grid = el("ul", "menu-grid");
    for (const entry of entries) grid.append(card(entry, draw));
    inner.append(grid);

    const foot = el("div", "menu-foot");
    foot.append(document.createTextNode("Every level is linkable: a URL like "));
    // The first level in the catalog, not a name written down here. There is
    // no puzzle id in this file for the same reason there is no descriptor.
    foot.append(el("code", undefined, `?puzzle=${entries[0].id}`));
    foot.append(
      document.createTextNode(
        " opens it directly, and the level picker in the toolbar switches between them without" +
          " coming back here.",
      ),
    );
    inner.append(foot);
  };

  draw();
  document.title = "GATECRASHER — levels";
  return { redraw: draw };
}