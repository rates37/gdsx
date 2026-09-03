// The Sticky Flops panel: every one-way latch
// (`sequential.sticky`), with two columns the player fills in themselves.
//
// The rule this panel exists to keep, stated directly in the design brief:
// stickiness alone does not say whether a flop is a **checkpoint** (must
// reach its latched value) or a **trap** (must avoid it) -- the
// honest-attempt document records exactly that mistake being made once
// already. So `checkpoint`/`trap` are never computed here; they are two
// buttons the player presses, persisted locally, and nothing pre-selects
// either one.
//
// The panel cross-references the puzzle's win condition two ways:
//
// - a **required** column shows the value the win condition needs this
//   flop's Q to hold, for every sticky flop that is *on* the win condition --
//   blank for the rest. This is the raw fact, read straight out of the
//   accessor: it saves the cross-reference against the Cone Walker's flatten
//   drawer, nothing more.
// - if a sticky flop's own polarity is that required value, that is evidence
//   pointing toward checkpoint or trap, and it is shown as a **marked
//   suggestion** chip next to the two buttons -- never written into the
//   buttons themselves.
//
// The distinction matters: the required column is a fact about the design,
// the same fact the flatten drawer already shows. The suggestion is this
// panel's own inference from that fact (required value vs. latched
// polarity), and inference is the player's job -- stickiness alone does not
// say whether a flop is a checkpoint or a trap, and the honest-attempt
// document records that mistake being made once already. So checkpoint/trap
// are never computed here; they are two buttons the player presses,
// persisted locally, and nothing pre-selects either one.
//
// Where the win condition comes from is `design/win-condition.ts`, shared
// with the Experiments watch selector. It used to be a `requirements(success)`
// call made here, which on a design whose lock is a latched flop says only
// "the lock flop must be high" -- true, and one step short of the flop values
// a player can act on.

import { instanceChip } from "./chips.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { Mounted } from "./mounts.ts";
import type { DesignClient } from "../design/client.ts";
import type { WinCondition, WinConditionSource } from "../design/win-condition.ts";
import type { StickyView } from "../gdsx-types.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

type Classification = "checkpoint" | "trap" | null;

class StickyClassifications {
  private readonly key: string;
  private map: Record<string, Classification>;

  constructor(puzzleId: string) {
    this.key = `gdsx.sticky-classification.${puzzleId}.v1`;
    this.map = this.restore();
  }

  get(flop: string): Classification {
    return this.map[flop] ?? null;
  }

  set(flop: string, value: Classification): void {
    if (value === null) delete this.map[flop];
    else this.map[flop] = value;
    try {
      localStorage.setItem(this.key, JSON.stringify(this.map));
    } catch (err) {
      console.warn("gdsx: could not save sticky-flop classifications", err);
    }
  }

  private restore(): Record<string, Classification> {
    try {
      const raw = localStorage.getItem(this.key);
      return raw ? (JSON.parse(raw) as Record<string, Classification>) : {};
    } catch {
      return {};
    }
  }
}

export interface StickyFlopsPanelOptions {
  designReady: Promise<DesignClient>;
  puzzleId: string;
  /** Null for a puzzle with no lock: there is then no win condition to
   *  suggest from, and the button that would do it is not drawn. */
  successNet: string | null;
  /** The flops the win condition names, shared with the Experiments panel. */
  winCondition: WinConditionSource;
}

/**
 * The sticky-flop list, as a section of the Registers panel.
 *
 * A flat list about every flop in the design, so it is a sibling of the
 * register list rather than something inside a selected register. Body
 * unchanged from the former `render`, only de-indented.
 */
export function mountStickyFlops(
  container: HTMLElement,
  options: StickyFlopsPanelOptions,
): Mounted {
    container.classList.add("sf-panel");
    container.innerHTML = `
      <div class="sf-toolbar">
        <span class="sf-count"></span>
        <button type="button" class="sf-suggest" disabled hidden>suggest from the win condition${options.successNet ? ` (${options.successNet})` : ""}</button>
        <span class="sf-status"></span>
        <span class="py-call-slot"></span>
      </div>
      <div class="sf-loading">waiting on the analysis engine…</div>
      <div class="sf-caption" hidden></div>
      <div class="sf-body" hidden></div>`;

    const countEl = container.querySelector(".sf-count") as HTMLSpanElement;
    const suggestBtn = container.querySelector(".sf-suggest") as HTMLButtonElement;
    const statusEl = container.querySelector(".sf-status") as HTMLSpanElement;
    const loadingEl = container.querySelector(".sf-loading") as HTMLDivElement;
    const captionEl = container.querySelector(".sf-caption") as HTMLDivElement;
    const bodyEl = container.querySelector(".sf-body") as HTMLDivElement;
    const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

    let design: DesignClient | null = null;
    let disposed = false;
    let sticky: StickyView[] = [];
    let lastCall: string | null = null;
    let suggestions: Map<string, Classification> = new Map();
    // The required column's source: the win condition itself, not the
    // suggestion this panel derives from it. Fetched once on mount (the
    // accessor memoises), independent of whether the player ever presses
    // `suggest`.
    let win: WinCondition | null = null;
    const classifications = new StickyClassifications(options.puzzleId);

    attachPythonCallButton(callSlot, () => lastCall);

    function render(): void {
      bodyEl.replaceChildren();
      for (const s of sticky) {
        const row = el("div", "sf-row");
        row.append(instanceChip(s.flop, { className: "sf-flop" }));
        row.append(el("span", "sf-polarity", `latches ${s.polarity ? "high" : "low"}`));
        row.append(el("span", "sf-condition", `D = ${s.condition} | Q`));

        // Blank, not a placeholder dash, for a sticky flop the win condition
        // does not name -- most of them: the original puzzle's win condition
        // touches 36 of its 62 sticky flops.
        const required = win?.flops.get(s.flop);
        const requiredEl = el(
          "span",
          "sf-required",
          required === undefined ? "" : String(required),
        );
        if (required !== undefined) {
          requiredEl.title = `the win condition needs ${s.flop}.Q == ${required}`;
        }
        row.append(requiredEl);

        const current = classifications.get(s.flop);
        const suggestion = suggestions.get(s.flop) ?? null;
        if (suggestion) {
          const tag = el("span", "sf-suggestion-tag", `suggests: ${suggestion}`);
          tag.title = `the win condition needs this flop at ${suggestion === "checkpoint" ? "" : "the opposite of "}its latched value — a suggestion, not a decision`;
          row.append(tag);
        }

        const checkBtn = document.createElement("button");
        checkBtn.type = "button";
        checkBtn.textContent = "checkpoint";
        checkBtn.className = "sf-classify-btn" + (current === "checkpoint" ? " active" : "");
        checkBtn.addEventListener("click", () => {
          classifications.set(s.flop, current === "checkpoint" ? null : "checkpoint");
          render();
        });

        const trapBtn = document.createElement("button");
        trapBtn.type = "button";
        trapBtn.textContent = "trap";
        trapBtn.className = "sf-classify-btn" + (current === "trap" ? " active" : "");
        trapBtn.addEventListener("click", () => {
          classifications.set(s.flop, current === "trap" ? null : "trap");
          render();
        });

        row.append(checkBtn, trapBtn);
        bodyEl.append(row);
      }
    }

    async function suggest(): Promise<void> {
      if (!design) return;
      suggestBtn.disabled = true;
      statusEl.textContent = "deriving requirements…";
      statusEl.className = "sf-status";
      try {
        // Already resolved by the time the button exists (mount hides it
        // until `win` is non-null), so this is the memoised value, not a
        // fresh round trip.
        win = await options.winCondition.get();
        if (win === null) {
          // Only reachable if the derivation stopped being possible between
          // the button appearing and the click; it is offered on the strength
          // of a resolved win condition.
          statusEl.textContent = "no win condition to suggest from";
          return;
        }
        lastCall = win.call;
        suggestions = new Map();
        for (const s of sticky) {
          const forced = win.flops.get(s.flop);
          if (forced === undefined) continue;
          // Forced to its own latched value: success needs it reached ->
          // a checkpoint. Forced to the opposite: success needs it never
          // latched -> a trap. Either way this is read polarity, not a
          // guess -- but it is still only a suggestion.
          suggestions.set(s.flop, forced === s.polarity ? "checkpoint" : "trap");
        }
        statusEl.textContent = `${suggestions.size} of ${sticky.length} sticky flops are named by the win condition`;
        render();
      } catch (err) {
        statusEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        statusEl.className = "sf-status bad";
      } finally {
        suggestBtn.disabled = false;
      }
    }

    suggestBtn.addEventListener("click", () => void suggest());

    options.designReady
      .then(async (d) => {
        if (disposed) return;
        design = d;
        const { data, call } = await d.sticky();
        if (disposed) return;
        sticky = data.sticky;
        lastCall = call;
        countEl.textContent = `${sticky.length} sticky flops`;
        loadingEl.hidden = true;
        bodyEl.hidden = false;
        render();
        // The required column and the `suggest` button both depend on the
        // win condition; a puzzle whose lock does not reduce to a set of flop
        // values gets neither -- no column full of blanks, no button that
        // would report nothing.
        //
        // And neither is offered when there is no sticky flop to put them
        // against. A design can have a derivable win condition and no one-way
        // latch at all (First Light is both), which would otherwise caption an
        // empty list with "named for 0 of 0 sticky flops" and offer a button
        // whose only possible answer is 0.
        win = await options.winCondition.get();
        if (disposed || win === null || sticky.length === 0) return;
        const named = sticky.filter((s) => win!.flops.has(s.flop)).length;
        captionEl.textContent =
          `required = what the win condition (${win.net}) needs this flop's Q to hold — ` +
          `named for ${named} of ${sticky.length} sticky flops, blank for the rest`;
        captionEl.hidden = false;
        render();
        suggestBtn.hidden = false;
        suggestBtn.disabled = false;
      })
      .catch((err) => {
        loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
      });

    return {
      dispose() {
        disposed = true;
      },
    };
}
