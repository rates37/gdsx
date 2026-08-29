// A slim strip above the dockview grid: a macOS/Windows-style menu bar for
// opening panels grouped by kind, the Notebook as its own button (it is the
// only scored surface, so it does not hide in a menu), and reset the layout
// back to its default arrangement. dockview's own tab close button (×) has
// no undo, and the default layout is otherwise only reachable by clearing
// localStorage by hand -- both are one click here.

import type { MenuGroup, Workspace } from "./workspace.ts";
import type { HintTier, PuzzleChecks } from "../puzzles/catalog.ts";
import type { Submission, Verdict } from "../puzzles/answer-check.ts";
import { goalFor } from "../puzzles/goals.ts";

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

/** Whether this puzzle is already solved, as `store/progress.ts` records it.
 *  Null when it is not -- the bar then says nothing rather than saying "not
 *  solved", which is the state of most puzzles most of the time. */
export interface SolvedState {
  /** `YYYY-MM-DD`, or null if the saved timestamp could not be read. */
  solvedOn: string | null;
  attempts: number;
}

/**
 * The hints button's wiring. Supplied by main.ts, which owns the puzzle's
 * `hints` tiers and the progress store; the toolbar only draws the button
 * and popover. Absent entirely for a puzzle baked before hints.json existed
 * (empty `tiers`), same convention as `submit`'s `checks: null`.
 *
 * game-plan.md §8 is explicit that hints are always available and never
 * gated behind progress -- there is deliberately no "unlock" state here, only
 * "not yet revealed" and "revealed". A revealed tier stays revealed: taking a
 * hint is a decision the player made, so `revealedCount` is backed by
 * store/progress.ts and survives a reload.
 */
export interface HintsControl {
  tiers: readonly HintTier[];
  /** How many tiers (from the front) are currently revealed. Re-read on
   *  every popover open/reveal rather than cached, same reason `checks`'s
   *  `currentBits` is a function and not a value. */
  revealedCount: () => number;
  /** Reveal the next tier and persist it. The caller (this file) never calls
   *  this past the last tier -- the "reveal" control is hidden once
   *  `revealedCount() >= tiers.length`. */
  revealNext: () => void;
}

/** The way back to the level menu. A plain href rather than a callback: it is
 *  a navigation to a URL the routing rule already defines (`menuUrl`), so
 *  middle-click and open-in-new-tab work without the toolbar handling them. */
export interface HomeLink {
  href: string;
}

/** What the readiness pill shows. `detail` is the line boot.ts already keeps
 *  ("python: ready in 1140 ms") and becomes the pill's tooltip. */
export interface Readiness {
  phase: "booting" | "ready" | "failed";
  detail?: string;
}

/** The handles the shell keeps after the toolbar is built. Each is a setter
 *  rather than an exposed element: the toolbar owns its own markup. */
export interface ToolbarHandle {
  status: (text: string) => void;
  readiness: (state: Readiness) => void;
  solved: (state: SolvedState) => void;
}

/** Everything the toolbar draws beyond the panel menus. Grouped into one
 *  object rather than trailing positional parameters -- there are seven of
 *  them now, and `attachToolbar(host, ws, menus, levels, undefined, obj,
 *  undefined, submit)` is not a call anyone can read. */
export interface ToolbarOptions {
  levels?: LevelPicker;
  guide?: GuideControl;
  objective?: Objective;
  submit?: SubmitControl;
  home?: HomeLink;
  /** Absent for a puzzle that has never been solved. */
  solved?: SolvedState;
  hints?: HintsControl;
}

export function attachToolbar(
  host: HTMLElement,
  workspace: Workspace,
  menus: MenuGroup[],
  opts: ToolbarOptions = {},
): ToolbarHandle {
  const { levels, guide, objective, submit, home } = opts;
  const bar = document.createElement("div");
  bar.className = "gdsx-toolbar";
  bar.innerHTML = `
    <span class="gdsx-toolbar-title">DIESHARK</span>
    <span class="gdsx-home-wrap"></span>
    <nav class="gdsx-menubar"></nav>
    <span class="gdsx-level-wrap"></span>
    <span class="gdsx-solved"></span>
    <span class="gdsx-objective"></span>
    <span class="gdsx-toolbar-status"></span>
    <div class="gdsx-toolbar-spacer"></div>
    <span class="gdsx-ready-wrap"></span>
    <button class="gdsx-notebook-btn" type="button" title="your notebook — the only scored surface in the game">
      notebook
    </button>
    <span class="gdsx-submit-slot"></span>
    <span class="gdsx-hints-slot"></span>
    <button class="gdsx-guide-btn" type="button" title="a step-by-step walkthrough of every panel, on the tutorial puzzle">
      guide
    </button>
    <button class="gdsx-reset-btn" type="button" title="restore the default panel arrangement">
      reset layout
    </button>
  `;
  host.prepend(bar);

  attachHomeLink(bar.querySelector(".gdsx-home-wrap") as HTMLSpanElement, home);

  attachLevelPicker(bar.querySelector(".gdsx-level-wrap") as HTMLSpanElement, levels);

  const setSolved = attachSolvedChip(bar.querySelector(".gdsx-solved") as HTMLSpanElement);
  if (opts.solved) setSolved(opts.solved);

  attachObjective(bar.querySelector(".gdsx-objective") as HTMLSpanElement, objective);

  const setReadiness = attachReadinessPill(bar.querySelector(".gdsx-ready-wrap") as HTMLSpanElement);

  const updateMenuChecks = attachMenuBar(bar.querySelector(".gdsx-menubar") as HTMLElement, workspace, menus);

  const notebookBtn = bar.querySelector(".gdsx-notebook-btn") as HTMLButtonElement;
  notebookBtn.addEventListener("click", () => workspace.focus("notebook"));
  const updateNotebookState = (): void => {
    notebookBtn.classList.toggle("on", workspace.isOpen("notebook"));
  };

  attachSubmitButton(bar.querySelector(".gdsx-submit-slot") as HTMLSpanElement, submit);

  attachHintsButton(bar.querySelector(".gdsx-hints-slot") as HTMLSpanElement, opts.hints);

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
  return {
    status: (text: string) => {
      statusEl.textContent = text;
    },
    readiness: setReadiness,
    solved: setSolved,
  };
}

/**
 * The way out of a puzzle, first thing in the bar.
 *
 * Until this existed the only route back to the menu was editing the URL. A
 * real `<a>` rather than a button calling `location.assign`, for the same
 * reason the menu's cards are links: the route is a URL, so the browser's own
 * affordances (middle click, open in a new tab, the status bar preview) come
 * for free.
 */
function attachHomeLink(host: HTMLSpanElement, home?: HomeLink): void {
  if (!home) {
    host.remove();
    return;
  }
  const link = document.createElement("a");
  link.className = "gdsx-home";
  link.href = home.href;
  link.textContent = "‹ levels";
  link.title = "back to the level menu";
  host.append(link);
}

/**
 * "You have solved this one", beside the objective.
 *
 * Drawn only when it is true: a puzzle nobody has solved says nothing rather
 * than carrying a permanent "not solved", which would be the state of most
 * puzzles most of the time and would crowd out the objective it sits next to.
 * The date and attempt count go in the tooltip -- the top bar has room for the
 * fact, not the history.
 */
function attachSolvedChip(host: HTMLSpanElement): (state: SolvedState) => void {
  host.hidden = true;
  return (state: SolvedState) => {
    host.hidden = false;
    host.textContent = "✓ solved";
    const plural = state.attempts === 1 ? "attempt" : "attempts";
    host.title = [
      state.solvedOn ? `first solved ${state.solvedOn}` : "solved",
      `${state.attempts} ${plural}`,
    ].join(" · ");
  };
}

/**
 * The readiness pill, at the right-hand end of the bar.
 *
 * Boot takes 20-30 seconds and until this existed the only readiness
 * indicator anywhere in the app was a line inside the Die View's collapsed
 * `debug info` disclosure; every other panel showed a bare "loading…", which
 * does not distinguish "still starting" from "stuck".
 *
 * It shows `booting…`, then `ready` with the timing in its tooltip, then gets
 * out of the way -- a permanent green light is noise, and the state it
 * reports (the design handle exists) does not come back. A failure stays put,
 * because that one the player needs to keep seeing.
 */
function attachReadinessPill(host: HTMLSpanElement): (state: Readiness) => void {
  const pill = document.createElement("span");
  pill.className = "gdsx-ready gdsx-ready-booting";
  pill.textContent = "booting…";
  host.append(pill);

  let hideTimer: ReturnType<typeof setTimeout> | undefined;
  return (state: Readiness) => {
    clearTimeout(hideTimer);
    pill.className = `gdsx-ready gdsx-ready-${state.phase}`;
    pill.textContent =
      state.phase === "booting" ? "booting…" : state.phase === "ready" ? "ready" : "boot failed";
    pill.title = state.detail ?? "";
    if (state.phase === "ready") {
      hideTimer = setTimeout(() => {
        pill.classList.add("gdsx-ready-done");
      }, 4000);
    }
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

  const goal = goalFor(objective.answerKind);
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

/**
 * The hints button and its popover, beside the submit button.
 *
 * Every tier ships different amounts of text -- the survey across all seven
 * puzzles found 4-5 tiers each and tier text from a short one-line summary up
 * to a multi-sentence paragraph (4-nine-lives' tier 4 is 442 characters) -- so
 * the list is plain stacked paragraphs with no fixed height assumption,
 * rather than a layout sized for four short lines.
 *
 * Always rendered when the puzzle has any tiers at all: no solved-state or
 * attempt-count gate, per game-plan.md §8. Removed entirely for a puzzle with
 * none, same convention as `attachSubmitButton`'s `checks: null`.
 */
function attachHintsButton(slot: HTMLSpanElement, hints?: HintsControl): void {
  if (!hints || hints.tiers.length === 0) {
    slot.remove();
    return;
  }
  const control = hints;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "gdsx-hints-btn";
  btn.title =
    "tiered hints -- each is an analysis you could have run yourself. Revealing one costs points and never blocks you.";

  const popover = document.createElement("div");
  popover.className = "gdsx-hints-popover";
  popover.hidden = true;

  const header = document.createElement("div");
  header.className = "gdsx-hints-header";
  const headerLabel = document.createElement("span");
  headerLabel.textContent = "hints";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "gdsx-hints-close";
  closeBtn.textContent = "×";
  closeBtn.title = "close";
  header.append(headerLabel, closeBtn);

  const list = document.createElement("div");
  list.className = "gdsx-hints-list";

  const revealBtn = document.createElement("button");
  revealBtn.type = "button";
  revealBtn.className = "gdsx-hints-reveal";

  popover.append(header, list, revealBtn);

  function refresh(): void {
    const revealed = Math.min(control.revealedCount(), control.tiers.length);
    btn.textContent = `hints (${revealed}/${control.tiers.length})`;
    btn.classList.toggle("on", revealed > 0);
    list.replaceChildren(
      ...control.tiers.slice(0, revealed).map((tier) => {
        const item = document.createElement("p");
        item.className = "gdsx-hints-tier";
        item.textContent = tier.text;
        return item;
      }),
    );
    if (revealed === 0) {
      const empty = document.createElement("p");
      empty.className = "gdsx-hints-empty";
      empty.textContent = "no hints revealed yet";
      list.prepend(empty);
    }
    if (revealed >= control.tiers.length) {
      revealBtn.hidden = true;
    } else {
      revealBtn.hidden = false;
      revealBtn.textContent = `reveal hint ${revealed + 1} of ${control.tiers.length} — costs points`;
    }
  }

  revealBtn.addEventListener("click", () => {
    control.revealNext();
    refresh();
  });

  function close(): void {
    popover.hidden = true;
  }
  function open(): void {
    popover.hidden = false;
    refresh();
  }

  btn.addEventListener("click", () => (popover.hidden ? open() : close()));
  closeBtn.addEventListener("click", close);
  document.addEventListener("click", (e) => {
    if (!popover.hidden && !slot.contains(e.target as Node)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !popover.hidden) close();
  });

  refresh();
  slot.append(btn, popover);
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
 *  so it gets no radix control. `edited()` tracks whether the player has
 *  typed into it since the last programmatic `set` -- an empty *initial*
 *  value is not the same signal, because `currentBits` can legitimately
 *  return a non-empty string (a zero-filled track) the first time the store
 *  is ready, which would otherwise permanently look "already filled in" and
 *  block every later prefill. */
function bitField(
  port: string,
  initial: string,
): { row: HTMLElement; get: () => string; set: (value: string) => void; edited: () => boolean } {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "gdsx-submit-input";
  input.placeholder = "0101…";
  input.value = initial;
  let dirty = false;
  input.addEventListener("input", () => {
    dirty = true;
  });
  return {
    row: submitRow(port, input),
    get: () => input.value.trim(),
    set: (value) => {
      input.value = value;
      dirty = false;
    },
    edited: () => dirty,
  };
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
  const useCurrentBtn = document.createElement("button");
  useCurrentBtn.type = "button";
  useCurrentBtn.className = "gdsx-submit-use-current";
  useCurrentBtn.textContent = "use current sequence";
  useCurrentBtn.title = "refill every field from what the design is driving right now";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "gdsx-submit-close";
  closeBtn.textContent = "×";
  closeBtn.title = "close";
  header.append(closeBtn);

  const body = document.createElement("div");
  body.className = "gdsx-submit-body";

  // Latch fields keep `set`/`edited` around (not just `get`) so `open()` and
  // `useCurrentBtn` can refill them directly, by reference, rather than
  // re-finding the right input in the DOM by position.
  const latchFields: { name: string; set: (value: string) => void; edited: () => boolean }[] = [];

  const getters: { name: string; get: () => string }[] =
    checks.kind === "latch"
      ? submit.trackPorts.map((port) => {
          const f = bitField(port, "");
          body.append(f.row);
          latchFields.push({ name: port, set: f.set, edited: f.edited });
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

  if (checks.kind === "latch") header.prepend(useCurrentBtn);

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
        let text = parts.join(" — ");
        if (verdict.accepted) {
          // This scores the answer, not the Notebook's claims -- "coverage 0%"
          // next to an accepted verdict is correct, not a bug, but reads like
          // one without this line.
          text += "\nthis checks your answer only, not the Notebook's coverage -- see the Notebook for that score.";
        }
        verdictEl.textContent = text;
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
    // Prefilled on every open, into every field the player has not typed
    // into -- `edited()`, not "value is empty": `currentBits` legitimately
    // returns a non-empty (all-zero) string as soon as the store exists,
    // before the player has driven anything, so an emptiness check would
    // treat that first prefill as an edit and never refresh again.
    if (checks.kind === "latch") {
      for (const f of latchFields) {
        if (!f.edited()) f.set(control.currentBits(f.name));
      }
    }
  }

  if (checks.kind === "latch") {
    useCurrentBtn.addEventListener("click", () => {
      for (const f of latchFields) f.set(control.currentBits(f.name));
    });
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
