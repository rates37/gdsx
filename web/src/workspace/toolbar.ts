// A slim strip above the dockview grid: a macOS/Windows-style menu bar for
// opening panels grouped by kind, the Notebook as its own button (it is the
// only scored surface, so it does not hide in a menu), and reset the layout
// back to its default arrangement. dockview's own tab close button (×) has
// no undo, and the default layout is otherwise only reachable by clearing
// localStorage by hand -- both are one click here.

import type { MenuGroup, Workspace } from "./workspace.ts";
import type { PuzzleChecks } from "../puzzles/catalog.ts";
import type { Submission, Verdict } from "../puzzles/answer-check.ts";

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

/**
 * The submit surface's data: whether this puzzle's answer can be checked at
 * all, what shape of answer it wants, and how to check one.
 *
 * Supplied by main.ts, which owns the descriptor and the running `SimStore`
 * a `sequence`/`constant` answer needs simulated; the toolbar only draws the
 * widget `checks.kind` calls for and reports what `submit` returns. This is
 * deliberately not an eleventh panel (game-plan.md §8 puts the result in the
 * notebook, not a new tab) -- it is the toolbar button 10.1 left this slot
 * for, beside `guide`.
 */
export interface SubmitControl {
  /** Null hides the button entirely: a puzzle whose answer kind has no
   *  `checks` derivation yet (game-plan.md §6b lists more kinds than this
   *  block covers) cannot offer a widget that means anything. */
  checks: PuzzleChecks | null;
  /** The sequence editor's input ports, in the order a `sequence` answer's
   *  one-field-per-port widget offers them. Unused for every other kind. */
  trackPorts: readonly string[];
  /** What is currently driven on `port`, to prefill a `sequence` field so
   *  confirming an answer already found in the sequence editor is not a
   *  retype. `""` before the simulator has loaded. */
  currentBits: (port: string) => string;
  submit: (submission: Submission) => Promise<Verdict>;
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
  submit?: SubmitControl,
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

  attachSubmitButton(bar.querySelector(".gdsx-submit-slot") as HTMLSpanElement, submit);

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

/** One text field, with a label, in a submit popover. */
function submitRow(label: string, ...controls: HTMLElement[]): HTMLElement {
  const row = document.createElement("div");
  row.className = "gdsx-submit-row";
  const labelEl = document.createElement("label");
  labelEl.className = "gdsx-submit-label";
  labelEl.textContent = label;
  row.append(labelEl, ...controls);
  return row;
}

/** A bit-string field for one `sequence` track. Plain text -- not a number,
 *  so it gets no radix control. */
function bitField(port: string, initial: string): { row: HTMLElement; get: () => string } {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "gdsx-submit-input";
  input.placeholder = "0101…";
  input.value = initial;
  return { row: submitRow(port, input), get: () => input.value.trim() };
}

/**
 * A value field for a `constant`/`parameter` answer: a bare digit string
 * plus a radix selector, so a player can type `2A` and pick hex rather than
 * having to remember the `0x` spelling. Typing a prefix directly (`0x2A`,
 * `0b101`) is also honoured, and then the selector is moot -- the check is
 * normaliseAnswer's, which reads any radix the same way regardless of which
 * path produced the string.
 */
function valueField(name: string): { row: HTMLElement; get: () => string } {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "gdsx-submit-input";
  input.placeholder = name === "value" ? "e.g. 2A or 42" : `${name}, e.g. A3000000`;

  const radix = document.createElement("select");
  radix.className = "gdsx-submit-radix";
  for (const [value, label] of [
    ["hex", "hex"],
    ["dec", "dec"],
    ["bin", "bin"],
  ] as const) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    radix.append(option);
  }

  const get = (): string => {
    const raw = input.value.trim();
    if (/^[-+]?0[xXbBoO]/.test(raw)) return raw;
    return radix.value === "hex" ? `0x${raw}` : radix.value === "bin" ? `0b${raw}` : raw;
  };
  return { row: submitRow(name, input, radix), get };
}

/**
 * The submit button and its popover: the widget game-plan.md §6b describes,
 * chosen by `checks.kind` -- one bit-string field per input port for
 * `sequence`, a value+radix field for `constant`, one value+radix field per
 * named parameter for `parameter`. Absent entirely when the puzzle has
 * nothing checkable (`submit` undefined, or `checks: null`).
 *
 * Verification is `submit.submit()`'s job, not this function's -- it only
 * collects the fields into a `Submission`, shows what came back, and never
 * scores it (game-plan.md §8's weights are a separate piece of work). A
 * rejection shows `verdict.reason` AND `verdict.observed` together: this
 * audience wants the measurement, not a buzzer.
 */
function attachSubmitButton(slot: HTMLSpanElement, submit?: SubmitControl): void {
  if (!submit?.checks) {
    slot.remove();
    return;
  }
  const checks = submit.checks;
  const control = submit;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gdsx-submit-btn";
  btn.textContent = "submit";
  btn.title = "check whether your answer is right";

  const popover = document.createElement("div");
  popover.className = "gdsx-submit-popover";
  popover.hidden = true;

  const header = document.createElement("div");
  header.className = "gdsx-submit-header";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "gdsx-submit-close";
  closeBtn.textContent = "×";
  closeBtn.title = "close";
  header.append(closeBtn);

  const body = document.createElement("div");
  body.className = "gdsx-submit-body";

  const getters: { name: string; get: () => string }[] =
    checks.kind === "latch"
      ? submit.trackPorts.map((port) => {
          const f = bitField(port, "");
          body.append(f.row);
          return { name: port, get: f.get };
        })
      : checks.kind === "bus-at"
        ? [
            (() => {
              const f = valueField("value");
              body.append(f.row);
              return { name: "value", get: f.get };
            })(),
          ]
        : checks.fields.map((field) => {
            const f = valueField(field.name);
            body.append(f.row);
            return { name: field.name, get: f.get };
          });

  const verdictEl = document.createElement("div");
  verdictEl.className = "gdsx-submit-verdict";

  const checkBtn = document.createElement("button");
  checkBtn.type = "button";
  checkBtn.className = "gdsx-submit-check";
  checkBtn.textContent = "check";

  const footer = document.createElement("div");
  footer.className = "gdsx-submit-footer";
  footer.append(checkBtn);

  popover.append(header, body, footer, verdictEl);

  checkBtn.addEventListener("click", () => {
    const values = getters.map((g) => g.get());
    if (values.some((v) => v.length === 0)) {
      verdictEl.textContent = "fill every field in first";
      verdictEl.className = "gdsx-submit-verdict gdsx-submit-bad";
      return;
    }
    checkBtn.disabled = true;
    verdictEl.textContent = "checking…";
    verdictEl.className = "gdsx-submit-verdict gdsx-submit-pending";

    const submission: Submission =
      checks.kind === "latch"
        ? { tracks: Object.fromEntries(getters.map((g, i) => [g.name, values[i]])) }
        : checks.kind === "bus-at"
          ? { value: values[0] }
          : { fields: Object.fromEntries(getters.map((g, i) => [g.name, values[i]])) };

    submit
      .submit(submission)
      .then((verdict) => {
        const parts = [verdict.accepted ? "✓ accepted" : `✗ ${verdict.reason}`];
        if (verdict.observed) parts.push(`observed: ${verdict.observed}`);
        verdictEl.textContent = parts.join(" — ");
        verdictEl.className = `gdsx-submit-verdict ${verdict.accepted ? "gdsx-submit-ok" : "gdsx-submit-bad"}`;
        if (verdict.accepted) btn.classList.add("on");
      })
      .catch((err: unknown) => {
        verdictEl.textContent = err instanceof Error ? err.message : String(err);
        verdictEl.className = "gdsx-submit-verdict gdsx-submit-bad";
      })
      .finally(() => {
        checkBtn.disabled = false;
      });
  });

  function close(): void {
    popover.hidden = true;
  }

  function open(): void {
    popover.hidden = false;
    // Prefilled once, on open, and only into fields nobody has typed into
    // yet -- so reopening the popover never clobbers an edit.
    if (checks.kind === "latch") {
      for (const g of getters) {
        const input = body.querySelector<HTMLInputElement>(
          `.gdsx-submit-row:nth-child(${control.trackPorts.indexOf(g.name) + 1}) .gdsx-submit-input`,
        );
        if (input && input.value === "") input.value = control.currentBits(g.name);
      }
    }
  }

  btn.addEventListener("click", () => (popover.hidden ? open() : close()));
  closeBtn.addEventListener("click", close);
  // Same convention as the menu bar: an outside click or Escape closes it,
  // so a player is never left with a popover pinned open over the panels.
  document.addEventListener("click", (e) => {
    if (!popover.hidden && !slot.contains(e.target as Node)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !popover.hidden) close();
  });

  slot.append(btn, popover);
}
