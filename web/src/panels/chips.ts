// The net/instance chip: the one place a name is drawn in the UI, so it is
// the one place labels have to be applied.
//
// Ten panels were each building `el("span", "net-chip", name)` with their own
// copy of the hover-highlight wiring. They now all call through here, which is
// what makes "label this net" a thing you can do *anywhere* a name appears
// rather than a feature of one panel.
//
// Chips re-label themselves without their panel knowing. A chip records what
// it is showing in data attributes, and `refreshChips` re-derives the text of
// every chip in the document when the glossary changes. The DOM is the
// registry: panels that build chips and forget about them (most of them) do
// not need a subscription, and there is nothing to leak when their nodes are
// dropped.

import { highlightBus } from "../store/highlight";
import { coneRootBus } from "../store/selection";
import { labels, type LabelKind } from "../store/labels";

export interface ChipOptions {
  /** Extra classes, e.g. "sf-flop". */
  className?: string;
  /**
   * What hovering and clicking act on, when that is not the chip's own name
   * -- the requirements panel draws a chip for the leaf `dfrtp_2_50.Q` but
   * wants the die view to highlight the instance. `null` means inert: no
   * hover, no click.
   */
  target?: string | null;
  /** Click behaviour. Defaults to opening the target in the Cone Walker;
   *  pass a function to do something else, or `false` for no click. */
  onClick?: (() => void) | false;
  /** Rendered dim after the name, e.g. "=1". Not part of the label. */
  suffix?: string;
}

function labelHint(kind: LabelKind, name: string): string {
  const label = labels.get(kind, name);
  const what = kind === "net" ? "net" : "cell";
  return label === null
    ? `${name}\ndouble-click to label this ${what}`
    : `${name}\nlabelled "${label}" — double-click to rename`;
}

/** Applies a chip's current text and tooltip from the glossary. */
function paint(chip: HTMLElement): void {
  const kind = chip.dataset.labelKind as LabelKind | undefined;
  const name = chip.dataset.labelName;
  if (!kind || name === undefined) return;
  const label = labels.get(kind, name);
  chip.textContent = (label ?? name) + (chip.dataset.labelSuffix ?? "");
  chip.classList.toggle("net-chip-labelled", label !== null);
  chip.title = labelHint(kind, name);
}

/** Re-derives every chip on the page. Cheap enough to do on any label edit:
 *  it touches text, not layout, and only runs when the player renames
 *  something. */
export function refreshChips(root: ParentNode = document): void {
  for (const chip of Array.from(root.querySelectorAll<HTMLElement>(".net-chip[data-label-name]"))) {
    paint(chip);
  }
}

labels.subscribe(() => refreshChips());

/**
 * A name, rendered as a chip: labelled if the player has named it, hoverable
 * to highlight it on the die, clickable to open it, and double-clickable to
 * (re)name it.
 */
export function chip(kind: LabelKind, name: string, opts: ChipOptions = {}): HTMLElement {
  const element = document.createElement("span");
  element.className = opts.className ? `net-chip ${opts.className}` : "net-chip";
  element.dataset.labelKind = kind;
  element.dataset.labelName = name;
  if (opts.suffix) element.dataset.labelSuffix = opts.suffix;
  paint(element);

  const target = opts.target === undefined ? name : opts.target;
  if (target) {
    element.addEventListener("pointerenter", () => highlightBus.set({ name: target }));
    element.addEventListener("pointerleave", () => highlightBus.set(null));
    if (opts.onClick !== false) {
      const onClick = opts.onClick ?? (() => coneRootBus.open(target));
      element.addEventListener("click", (e) => {
        e.stopPropagation();
        onClick();
      });
    }
  } else if (opts.onClick) {
    element.addEventListener("click", (e) => {
      e.stopPropagation();
      (opts.onClick as () => void)();
    });
  }

  element.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    e.preventDefault();
    openLabelEditor(kind, name, element);
  });

  return element;
}

/** `chip("net", …)`, for the common case. */
export function netChip(name: string, opts?: ChipOptions): HTMLElement {
  return chip("net", name, opts);
}

/** `chip("instance", …)`. Flops are instances: a chip for `dfrtp_2_50` is a
 *  cell, not a net, and labelling it must not collide with a net of the same
 *  name. */
export function instanceChip(name: string, opts?: ChipOptions): HTMLElement {
  return chip("instance", name, opts);
}

let openEditor: (() => void) | null = null;

/**
 * The rename affordance: a small popover anchored to whatever was
 * double-clicked, showing the raw name so it is never in doubt what is being
 * labelled. Deliberately not `window.prompt` -- the raw name has to stay
 * visible while you type the alias for it.
 */
export function openLabelEditor(kind: LabelKind, name: string, anchor: HTMLElement): void {
  openEditor?.();

  const pop = document.createElement("div");
  pop.className = "label-editor";
  pop.innerHTML = `
    <div class="label-editor-head">
      <span class="label-editor-kind"></span>
      <span class="label-editor-name"></span>
    </div>
    <input class="label-editor-input" type="text" maxlength="48" placeholder="a name you'll recognise…" />
    <div class="label-editor-actions">
      <button class="label-editor-save" type="button">save</button>
      <button class="label-editor-clear" type="button">clear</button>
      <span class="label-editor-hint">enter · esc</span>
    </div>`;

  (pop.querySelector(".label-editor-kind") as HTMLElement).textContent =
    kind === "net" ? "label net" : "label cell";
  (pop.querySelector(".label-editor-name") as HTMLElement).textContent = name;
  const input = pop.querySelector(".label-editor-input") as HTMLInputElement;
  const saveBtn = pop.querySelector(".label-editor-save") as HTMLButtonElement;
  const clearBtn = pop.querySelector(".label-editor-clear") as HTMLButtonElement;
  input.value = labels.get(kind, name) ?? "";
  clearBtn.disabled = !labels.has(kind, name);

  document.body.append(pop);

  // Anchored below the chip, nudged back on screen if that would overflow.
  // The popover is in `body`, not the panel, so a panel's `overflow: hidden`
  // cannot clip it.
  const box = anchor.getBoundingClientRect();
  const width = pop.offsetWidth;
  const height = pop.offsetHeight;
  pop.style.left = `${Math.max(6, Math.min(box.left, window.innerWidth - width - 6))}px`;
  pop.style.top =
    box.bottom + height + 6 < window.innerHeight
      ? `${box.bottom + 4}px`
      : `${Math.max(6, box.top - height - 4)}px`;

  function close(): void {
    document.removeEventListener("pointerdown", onOutside, true);
    pop.remove();
    openEditor = null;
  }
  function onOutside(e: PointerEvent): void {
    if (!pop.contains(e.target as Node)) close();
  }
  function commit(): void {
    labels.set(kind, name, input.value);
    close();
  }

  saveBtn.addEventListener("click", commit);
  clearBtn.addEventListener("click", () => {
    labels.clear(kind, name);
    close();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    else if (e.key === "Escape") close();
    e.stopPropagation();
  });
  document.addEventListener("pointerdown", onOutside, true);

  openEditor = close;
  input.focus();
  input.select();
}