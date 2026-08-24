// Rendering a `RequirementsView`: what a net being 0 or 1 *forces*, and what
// it merely *offers a choice about*.
//
// There used to be two renderers of this payload -- the Cone Walker's flatten
// drawer and the Requirements panel -- reached by the same
// `DesignClient.requirements` call, and in fact driven by the same event:
// `coneRootBus.open(net)` re-rooted the walk *and* re-derived the panel, so
// clicking any net chip fired the analysis twice and drew it two different
// ways. This is the one renderer.
//
// The distinction it exists to protect: **forced and choice are drawn apart,
// always**. A leaf under "forced" must hold that value in every way of
// reaching the target; an option under "choices" is one of several that would
// do. Collapsing the two would turn "must" into "might", which is the single
// most expensive misreading available in this kind of work.

import type { LeafValue, RequirementsView } from "../gdsx-types";
import { instanceChip, netChip } from "./chips";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

/** A justification leaf is written `dfrtp_2_50.Q`, a bare net name, or a
 *  constant. The labellable thing is the flop or the net, never the composite
 *  string. */
export type LeafKind =
  | { kind: "const"; value: 0 | 1 }
  | { kind: "flop"; instance: string }
  | { kind: "input"; net: string };

export function leafKind(label: string): LeafKind {
  if (label === "0" || label === "1") return { kind: "const", value: label === "1" ? 1 : 0 };
  if (label.endsWith(".Q")) return { kind: "flop", instance: label.slice(0, -2) };
  return { kind: "input", net: label };
}

export interface RequirementsViewOptions {
  /**
   * Whether a leaf currently holds the value it needs, at whatever cycle the
   * shared cursor is on. `null` means "cannot say" -- no simulation loaded, or
   * a leaf the tape does not carry. Omit entirely to draw no ✓/✗ column.
   */
  live?: (label: string, wanted: number) => boolean | null;
  /** Offer a `pin` button on flop leaves, turning one into a notebook claim.
   *  Omit for a read-only rendering. */
  onPin?: (leaf: LeafValue, decoded: Extract<LeafKind, { kind: "flop" }>) => void;
}

/** Draws `view` into `host`, replacing whatever was there. */
export function renderRequirements(
  host: HTMLElement,
  view: RequirementsView,
  opts: RequirementsViewOptions = {},
): void {
  host.replaceChildren();

  host.append(
    el(
      "div",
      "rq-summary",
      `${view.net} == ${view.value}: ` +
        (view.consistent ? "consistent" : "INCONSISTENT — conflicting forced values"),
    ),
  );
  if (!view.consistent && view.conflicts.length) {
    host.append(el("div", "rq-conflicts", `conflicting: ${view.conflicts.join(", ")}`));
  }

  const leavesBox = el("div", "rq-section");
  leavesBox.append(
    el(
      "div",
      "rq-section-title",
      `forced (${view.leaves.length}) — every way of reaching ${view.value} needs these`,
    ),
  );
  for (const leaf of view.leaves) leavesBox.append(leafRow(leaf, opts));
  if (view.leaves.length === 0) {
    leavesBox.append(el("div", "rq-hint", "nothing is forced — every path is a choice"));
  }
  host.append(leavesBox);

  const choicesBox = el("div", "rq-section");
  choicesBox.append(
    el(
      "div",
      "rq-section-title",
      `choices (${view.choices.length}) — an OR: any ONE option below suffices, none is forced`,
    ),
  );
  if (view.choices.length === 0) {
    choicesBox.append(el("div", "rq-hint", "none — the tree is pure AND/OR and fully resolved"));
  }
  for (const choice of view.choices) {
    const cbox = el("div", "rq-choice");
    cbox.append(el("div", "rq-choice-head", `${choice.net} == ${choice.value}, satisfied by any of:`));
    for (const option of choice.options) {
      const line = el("div", "rq-choice-option");
      line.append(el("span", "rq-choice-bullet", "— "));
      if (option.literals.length === 0) {
        line.append(el("span", "rq-hint", "(always true)"));
      }
      for (const lit of option.literals) {
        line.append(netChip(lit.net, { className: "rq-choice-chip", suffix: `=${lit.value}` }));
      }
      cbox.append(line);
    }
    choicesBox.append(cbox);
  }
  host.append(choicesBox);
}

function leafRow(leaf: LeafValue, opts: RequirementsViewOptions): HTMLElement {
  const row = el("div", "rq-leaf");
  const decoded = leafKind(leaf.net);

  if (opts.live) {
    const ok = opts.live(leaf.net, leaf.value);
    // Three states, three colours. A ✗ used to render in the same green as a
    // ✓ -- an unmet requirement that looks met is worse than no column at all.
    const mark = ok === null ? "·" : ok ? "✓" : "✗";
    const tone = ok === null ? "rq-check-unknown" : ok ? "rq-check-yes" : "rq-check-no";
    row.append(el("span", `rq-check ${tone}`, mark));
  }

  const chip =
    decoded.kind === "flop"
      ? instanceChip(decoded.instance, { suffix: ".Q" })
      : decoded.kind === "input"
        ? netChip(decoded.net)
        : el("span", "net-chip", leaf.net);
  row.append(chip, el("span", "rq-eq", ` == ${leaf.value}`));

  if (decoded.kind === "flop" && opts.onPin) {
    const pin = document.createElement("button");
    pin.type = "button";
    pin.className = "rq-claim-btn";
    pin.textContent = "pin";
    pin.title = "add as a notebook requirement claim";
    pin.addEventListener("click", () => {
      opts.onPin!(leaf, decoded);
      pin.textContent = "pinned";
      pin.disabled = true;
    });
    row.append(pin);
  }
  return row;
}