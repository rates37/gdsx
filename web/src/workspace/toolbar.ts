// A slim strip above the dockview grid: a macOS/Windows-style menu bar for
// opening panels grouped by kind, the Notebook as its own button (it is the
// only scored surface, so it does not hide in a menu), and reset the layout
// back to its default arrangement. dockview's own tab close button (×) has
// no undo, and the default layout is otherwise only reachable by clearing
// localStorage by hand -- both are one click here.

import type { MenuGroup, Workspace } from "./workspace.ts";

/** The guided-walkthrough button's wiring. Supplied by main.ts, which owns
 *  the Guide; the toolbar only renders a button for it. Absent when the
 *  build has no tutorial puzzle to offer. */
export interface GuideControl {
  /** True when the walkthrough is already on screen. */
  isOpen: () => boolean;
  /** Open it, restart it, or -- from another level -- switch to the tutorial
   *  puzzle and open it there. */
  toggle: () => void;
  /** Re-renders the button when the guide opens or closes by other means. */
  subscribe: (fn: () => void) => void;
}

/** The level picker's data: what to offer, what is open, and what to do
 *  about a choice. Supplied by main.ts, which owns the catalog; the toolbar
 *  only renders it. */
export interface LevelPicker {
  puzzles: { id: string; title: string; blurb?: string; parMinutes?: number | null }[];
  currentId: string;
  onSelect: (id: string) => void;
}

/** Sets the toolbar's status readout. Returns a setter rather than exposing
 *  the element: the notebook's coverage is the only thing that writes here so
 *  far (game-plan.md §3 puts it in the title bar), and keeping it a function
 *  means the toolbar owns its own markup. */
/**
 * What the loaded puzzle is asking for. Until this existed the objective was
 * reachable only as a `title=` tooltip on an option inside the level
 * `<select>` -- so a player who never opened that dropdown was never told what
 * they were trying to do.
 */
export interface Objective {
  /** `manifest.blurb`. */
  blurb: string;
  /** `solution.answer_kind`, or null for a puzzle that declares none. */
  answerKind: string | null;
  parMinutes?: number | null;
}

/** The answer kinds of game-plan.md §6b, said in the imperative. The point is
 *  to tell the player what *shape* of answer ends the puzzle: "find the input
 *  sequence" and "recover a value" are very different sessions. */
const ANSWER_GOAL: Record<string, string> = {
  sequence: "find the input sequence",
  constant: "recover a value",
  parameter: "recover the parameters",
  model: "build a working model",
  function: "recover the function",
  location: "find the cells",
  patch: "repair the design",
  state: "find the register state",
};

export function attachToolbar(
  host: HTMLElement,
  workspace: Workspace,
  menus: MenuGroup[],
  levels?: LevelPicker,
  guide?: GuideControl,
  objective?: Objective,
): (text: string) => void {
  const bar = document.createElement("div");
  bar.className = "gdsx-toolbar";
  bar.innerHTML = `
    <span class="gdsx-toolbar-title">DIESHARK</span>
    <nav class="gdsx-menubar"></nav>
    <span class="gdsx-level-wrap"></span>
    <span class="gdsx-objective"></span>
    <span class="gdsx-toolbar-status"></span>
    <div class="gdsx-toolbar-spacer"></div>
    <button class="gdsx-notebook-btn" type="button" title="your notebook — the only scored surface in the game">
      notebook
    </button>
    <span class="gdsx-submit-slot"></span>
    <button class="gdsx-guide-btn" type="button" title="a step-by-step walkthrough of every panel, on the tutorial puzzle">
      guide
    </button>
    <button class="gdsx-reset-btn" type="button" title="restore the default panel arrangement">
      reset layout
    </button>
  `;
  host.prepend(bar);

  attachLevelPicker(bar.querySelector(".gdsx-level-wrap") as HTMLSpanElement, levels);

  attachObjective(bar.querySelector(".gdsx-objective") as HTMLSpanElement, objective);

  const updateMenuChecks = attachMenuBar(bar.querySelector(".gdsx-menubar") as HTMLElement, workspace, menus);

  const notebookBtn = bar.querySelector(".gdsx-notebook-btn") as HTMLButtonElement;
  notebookBtn.addEventListener("click", () => workspace.focus("notebook"));
  const updateNotebookState = (): void => {
    notebookBtn.classList.toggle("on", workspace.isOpen("notebook"));
  };

  attachGuideButton(bar.querySelector(".gdsx-guide-btn") as HTMLButtonElement, guide);

  const resetBtn = bar.querySelector(".gdsx-reset-btn") as HTMLButtonElement;
  resetBtn.addEventListener("click", () => workspace.resetLayout());

  workspace.onChange(() => {
    updateMenuChecks();
    updateNotebookState();
  });
  updateMenuChecks();
  updateNotebookState();

  const statusEl = bar.querySelector(".gdsx-toolbar-status") as HTMLSpanElement;
  return (text: string) => {
    statusEl.textContent = text;
  };
}

/**
 * The menu bar: one button per group, each opening a dropdown of its panels.
 * macOS/Windows conventions: one menu open at a time, `pointerenter` on a
 * sibling button swaps which one without a click, Escape and an outside
 * click both close.
 *
 * Items are built once, at construction. `Workspace.onDidLayoutChange` (what
 * `onChange` wraps) fires on every drag and resize, not just open/close, so
 * the returned function only toggles a class on the checkmark already in the
 * DOM -- rebuilding the menu on that cadence would rebind a click listener
 * per pointer move across a drag.
 */
function attachMenuBar(host: HTMLElement, workspace: Workspace, menus: MenuGroup[]): () => void {
  const checks: { id: string; el: HTMLElement }[] = [];
  let openBtn: HTMLButtonElement | null = null;

  function closeMenu(): void {
    if (!openBtn) return;
    openBtn.classList.remove("open");
    (openBtn.nextElementSibling as HTMLElement).hidden = true;
    openBtn = null;
  }

  function openMenu(btn: HTMLButtonElement): void {
    if (openBtn === btn) return;
    closeMenu();
    btn.classList.add("open");
    (btn.nextElementSibling as HTMLElement).hidden = false;
    openBtn = btn;
  }

  for (const group of menus) {
    const wrap = document.createElement("div");
    wrap.className = "gdsx-menu-wrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "gdsx-menu-btn";
    btn.textContent = `${group.label} ▾`;
    btn.addEventListener("click", () => (openBtn === btn ? closeMenu() : openMenu(btn)));
    btn.addEventListener("pointerenter", () => {
      if (openBtn && openBtn !== btn) openMenu(btn);
    });

    const dropdown = document.createElement("div");
    dropdown.className = "gdsx-menu-dropdown";
    dropdown.hidden = true;

    for (const id of group.items) {
      const title = workspace.panelTitle(id);
      if (title === undefined) continue; // named here, not registered yet
      const item = document.createElement("div");
      item.className = "gdsx-menu-item";
      const check = document.createElement("span");
      check.className = "gdsx-menu-check";
      check.textContent = "✓";
      const label = document.createElement("span");
      label.textContent = title;
      item.append(check, label);
      item.addEventListener("click", () => {
        workspace.focus(id);
        closeMenu();
      });
      dropdown.append(item);
      checks.push({ id, el: check });
    }

    wrap.append(btn, dropdown);
    host.append(wrap);
  }

  document.addEventListener("click", (e) => {
    if (openBtn && !openBtn.parentElement!.contains(e.target as Node)) closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMenu();
  });

  return () => {
    for (const { id, el } of checks) el.classList.toggle("checked", workspace.isOpen(id));
  };
}

/**
 * The level dropdown, left of the status readout.
 *
 * A `<select>` rather than a custom dropdown like the menu bar's: this is a
 * one-of-N choice with a current value to display, which is exactly what a
 * select is, and it gets keyboard handling and the native popup for free.
 * Hidden entirely when there is only one puzzle to offer -- a picker with
 * one option is a control that does nothing.
 */
function attachLevelPicker(host: HTMLSpanElement, levels?: LevelPicker): void {
  if (!levels || levels.puzzles.length < 2) {
    host.remove();
    return;
  }

  const select = document.createElement("select");
  select.className = "gdsx-level-select";
  select.title = "switch level";
  for (const puzzle of levels.puzzles) {
    const option = document.createElement("option");
    option.value = puzzle.id;
    option.textContent = puzzle.parMinutes
      ? `${puzzle.title} · par ${puzzle.parMinutes}m`
      : puzzle.title;
    option.title = puzzle.blurb ?? "";
    select.append(option);
  }
  select.value = levels.currentId;

  select.addEventListener("change", () => {
    if (select.value === levels.currentId) return;
    // Loading is a navigation, so the select would otherwise sit on the new
    // title for however long the fetch takes, as though the swap had already
    // happened. Disabling says "this is in progress" with no extra chrome.
    select.disabled = true;
    levels.onSelect(select.value);
  });

  host.append(select);
}
/**
 * The objective, immediately right of the level picker -- the first place you
 * look after "which puzzle am I on".
 *
 * The goal phrase is always drawn; the blurb after it is allowed to ellipsise,
 * because on a narrow window "recover a value" is the half worth keeping. The
 * full text is on the `title` either way.
 */
function attachObjective(host: HTMLSpanElement, objective?: Objective): void {
  if (!objective || (!objective.blurb && !objective.answerKind)) {
    host.remove();
    return;
  }

  const goal = objective.answerKind ? ANSWER_GOAL[objective.answerKind] : null;
  if (goal) {
    const goalEl = document.createElement("span");
    goalEl.className = "gdsx-objective-goal";
    goalEl.textContent = goal;
    host.append(goalEl);
  }
  if (objective.blurb) {
    const blurbEl = document.createElement("span");
    blurbEl.className = "gdsx-objective-blurb";
    blurbEl.textContent = objective.blurb;
    host.append(blurbEl);
  }

  const par = objective.parMinutes ? ` · par ${objective.parMinutes} min` : "";
  host.title = [goal ? `Goal: ${goal}` : null, objective.blurb]
    .filter(Boolean)
    .join("\n") + par;
}

/**
 * The guide button, right of the status readout.
 *
 * Always available, on every level -- "show me how this works" is not a thing
 * a player should have to go back to a menu for. From another puzzle it
 * switches to the tutorial one, which is a navigation, so `toggle` owns that
 * and the button only reports what happened.
 */
function attachGuideButton(button: HTMLButtonElement, guide?: GuideControl): void {
  if (!guide) {
    button.remove();
    return;
  }
  const refresh = (): void => {
    button.textContent = guide.isOpen() ? "guide ✓" : "guide";
    button.classList.toggle("on", guide.isOpen());
  };
  button.addEventListener("click", () => {
    guide.toggle();
    refresh();
  });
  guide.subscribe(refresh);
  refresh();
}
