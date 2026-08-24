// The Sticky Flops panel (game-plan.md §4.12, needs L37): every one-way latch
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
// Once `requirements()` (L32) has been run against `success`, this panel
// cross-references: if a sticky flop's own polarity shows up as a forced
// leaf of `success == 1`, that is evidence pointing one way or the other, and
// it is shown as a **marked suggestion** chip next to the two buttons --
// never written into the buttons themselves.

import { instanceChip } from "./chips.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { Mounted } from "./mounts.ts";
import type { DesignClient } from "../design/client.ts";
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
  successNet: string;
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
        <button type="button" class="sf-suggest" disabled>suggest from requirements(${options.successNet})</button>
        <span class="sf-status"></span>
        <span class="py-call-slot"></span>
      </div>
      <div class="sf-loading">waiting on the analysis engine…</div>
      <div class="sf-body" hidden></div>`;

    const countEl = container.querySelector(".sf-count") as HTMLSpanElement;
    const suggestBtn = container.querySelector(".sf-suggest") as HTMLButtonElement;
    const statusEl = container.querySelector(".sf-status") as HTMLSpanElement;
    const loadingEl = container.querySelector(".sf-loading") as HTMLDivElement;
    const bodyEl = container.querySelector(".sf-body") as HTMLDivElement;
    const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

    let design: DesignClient | null = null;
    let disposed = false;
    let sticky: StickyView[] = [];
    let lastCall: string | null = null;
    let suggestions: Map<string, Classification> = new Map();
    const classifications = new StickyClassifications(options.puzzleId);

    attachPythonCallButton(callSlot, () => lastCall);

    function render(): void {
      bodyEl.replaceChildren();
      for (const s of sticky) {
        const row = el("div", "sf-row");
        row.append(instanceChip(s.flop, { className: "sf-flop" }));
        row.append(el("span", "sf-polarity", `latches ${s.polarity ? "high" : "low"}`));
        row.append(el("span", "sf-condition", `D = ${s.condition} | Q`));

        const current = classifications.get(s.flop);
        const suggestion = suggestions.get(s.flop) ?? null;
        if (suggestion) {
          const tag = el("span", "sf-suggestion-tag", `suggests: ${suggestion}`);
          tag.title = "from requirements(success, 1): this flop's polarity appears among the forced leaves — a suggestion, not a decision";
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
        const { data, call } = await design.requirements(options.successNet, 1);
        lastCall = call;
        const byInstance = new Map<string, number>();
        for (const leaf of data.leaves) {
          if (leaf.net.endsWith(".Q")) byInstance.set(leaf.net.slice(0, -2), leaf.value);
        }
        suggestions = new Map();
        for (const s of sticky) {
          const forced = byInstance.get(s.flop);
          if (forced === undefined) continue;
          // Forced to its own latched value: success needs it reached ->
          // a checkpoint. Forced to the opposite: success needs it never
          // latched -> a trap. Either way this is read polarity, not a
          // guess -- but it is still only a suggestion.
          suggestions.set(s.flop, forced === s.polarity ? "checkpoint" : "trap");
        }
        statusEl.textContent = `${suggestions.size} of ${sticky.length} sticky flops appear in the forced leaves`;
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
        suggestBtn.disabled = false;
        render();
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
