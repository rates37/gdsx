// The briefing card: what this level is, before you start on it.
//
// It exists because the level's description had nowhere else to live. The
// toolbar carried the blurb and the goal phrase inline, which meant the one
// elastic thing in a bar of fixed-width buttons: at any ordinary window width
// the sentence was ellipsised down to a few words, and the bar itself
// overflowed. A description is not a status readout, and the fix was not a
// narrower font -- it was to stop putting a paragraph in a 28px strip.
//
// Everything it says comes from `puzzles/briefing.ts`, which derives it from
// the catalog descriptor and is tested under plain Node. This file is only
// the rendering, plus the one piece of state a data module cannot own: when
// to open by itself.
//
// It is a real `<dialog>`, opened with `showModal()`. That buys the top
// layer, the backdrop, focus containment, `inert` on the page behind and
// Escape-to-close from the browser, all of which the popovers elsewhere in
// this file's neighbourhood implement by hand -- correctly, but only because
// each of them is small. A modal is the case where doing it by hand goes
// wrong, and it is also the case the platform already handles.

import type { Briefing } from "../puzzles/briefing.ts";
import type { SolvedState } from "./toolbar.ts";
import { plateElement } from "../puzzles/plate.ts";

export interface BriefingOptions {
  brief: Briefing;
  /** Read on every open, so a card opened after a solve says so. */
  solved: () => SolvedState | undefined;
}

export interface BriefingHandle {
  open: () => void;
  close: () => void;
  isOpen: () => boolean;
  /** Fired when it opens or closes, so the toolbar button can mark itself. */
  subscribe: (fn: () => void) => void;
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

/** One `LABEL / value` row of the facts grid. Appends nothing when there is
 *  no value -- a puzzle with no checkable answer should not show an empty
 *  "to solve" row. */
function fact(host: HTMLElement, label: string, value: string | HTMLElement | null): void {
  if (value === null) return;
  host.append(el("div", "briefing-fact-label", label));
  const cell = el("div", "briefing-fact-value");
  if (typeof value === "string") cell.textContent = value;
  else cell.append(value);
  host.append(cell);
}

/** A backstory paragraph, with any bare URL in it turned into a link.
 *
 *  Split-and-append rather than `innerHTML`, the same way the guided
 *  walkthrough renders its `{{net}}` spans: the text is authored in a
 *  manifest, and a manifest is a file this app parses, not markup it should
 *  ever be executing. Trailing sentence punctuation is left outside the link,
 *  so a URL that ends a sentence does not swallow the full stop. */
function storyParagraph(text: string): HTMLElement {
  const p = el("p", "briefing-story");
  for (const part of text.split(/(https?:\/\/[^\s]+)/g)) {
    if (!part) continue;
    if (!/^https?:\/\//.test(part)) {
      p.append(document.createTextNode(part));
      continue;
    }
    const trimmed = part.replace(/[.,;:)\]]+$/, "");
    const link = document.createElement("a");
    link.className = "briefing-link";
    link.href = trimmed;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = trimmed;
    p.append(link);
    if (trimmed.length < part.length) p.append(document.createTextNode(part.slice(trimmed.length)));
  }
  return p;
}

/**
 * Builds the card and attaches it to `document.body`. Nothing is shown until
 * `open()`.
 */
export function createBriefing(options: BriefingOptions): BriefingHandle {
  const { brief } = options;
  const listeners = new Set<() => void>();

  const dialog = document.createElement("dialog");
  dialog.className = "briefing";
  dialog.dataset.puzzle = brief.id;
  dialog.setAttribute("aria-label", `${brief.title} — briefing`);

  // ---- head: the mark, the band of metadata, the title, the goal ----
  const head = el("div", "briefing-head");
  head.append(plateElement(brief.id, "briefing-plate"));

  const eyebrow = el("div", "briefing-eyebrow");
  const solvedChip = el("span", "briefing-solved");
  solvedChip.hidden = true;
  // Separated by a middot rather than by the gap alone: uppercased and
  // letter-spaced, "medium par 35m" reads as one phrase.
  const meta = [brief.difficulty, brief.par].filter((part): part is string => Boolean(part));
  eyebrow.append(el("span", undefined, meta.join(" · ")));
  eyebrow.append(solvedChip);
  head.append(eyebrow);
  head.append(el("h2", "briefing-title", brief.title));
  if (brief.goal) head.append(el("div", "briefing-goal", brief.goal));

  // ---- body: the description, where it came from, then the facts ----
  const body = el("div", "briefing-body");
  if (brief.blurb) body.append(el("p", "briefing-blurb", brief.blurb));
  for (const paragraph of brief.backstory) {
    body.append(storyParagraph(paragraph));
  }

  const facts = el("div", "briefing-facts");
  fact(facts, "to solve", brief.winCondition);
  fact(facts, "your answer", brief.answerShape);
  if (facts.childElementCount > 0) body.append(facts);

  // ---- foot ----
  const foot = el("div", "briefing-foot");
  foot.append(el("span", "briefing-foot-note", "reopen this any time from the briefing button"));
  const startBtn = el("button", "briefing-start", "got it");
  startBtn.type = "button";
  // Otherwise `showModal` puts the initial focus on the first focusable thing
  // in the card, which on the original puzzle is the link in its backstory --
  // so the card opens with a URL ringed in blue rather than with its dismiss
  // button ready under Enter.
  startBtn.autofocus = true;
  foot.append(startBtn);

  dialog.append(head, body, foot);
  document.body.append(dialog);

  function notify(): void {
    for (const fn of listeners) fn();
  }

  function close(): void {
    if (dialog.open) dialog.close();
  }

  function open(): void {
    if (dialog.open) return;
    const solved = options.solved();
    solvedChip.hidden = solved === undefined;
    if (solved) {
      solvedChip.textContent =
        solved.score === undefined ? "· solved" : `· solved · ${solved.score}`;
    }
    // `showModal` throws if the dialog is not connected, and would also throw
    // on a second call while open -- both are guarded above. It never throws
    // for a browser without dialog support, because there is none: every
    // target of this app has had it for years.
    dialog.showModal();
    notify();
  }

  startBtn.addEventListener("click", close);
  // `close` fires for Escape and for the button alike, so the button does not
  // notify separately.
  dialog.addEventListener("close", notify);
  // A dialog's backdrop is part of the dialog element, so a click landing on
  // the element itself (rather than on any of its children) is a click
  // outside the card. Same dismissal the popovers offer.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });

  return {
    open,
    close,
    isOpen: () => dialog.open,
    subscribe: (fn) => listeners.add(fn),
  };
}