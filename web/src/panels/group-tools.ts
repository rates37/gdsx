// The tools you can point at a group of flops: bit weights, orbit, and the
// control values that make the group react.
//
// Extracted from the former Register Decoder panel so that the Registers panel
// can offer the same three sections against a register it *discovered*, rather
// than only against a group the player typed out by hand. That panel and this
// one were already calling the same `decodeOrbit` with the same hand-rolled
// stimulus row and the same state-strip rendering; this is the one copy.

import type { DesignClient } from "../design/client";
import type { WeightView } from "../gdsx-types";
import type { Claim, Role } from "../notebook/model";
import type { Notebook } from "../notebook/store";
import { instanceChip } from "./chips";
import { stimulusRow } from "./stimulus-row";
import type { Mounted } from "./mounts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function names(raw: string): string[] {
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

/** `analysis.decode.OrbitKind` -> `notebook/model.ts`'s `Role`, only where
 *  `analysis.claims.ROLES` actually accepts the result. */
const CLAIMABLE_ROLE: Partial<Record<string, Role>> = {
  counter: "counter",
  saturating: "saturating-counter",
  shift: "shift-reg",
};

export interface GroupToolsOptions {
  design: DesignClient;
  notebook: Notebook;
  /** Primary input names, for the stimulus rows. */
  inputs: string[];
  /** Excluded from the stimulus rows built from `inputs`: driving the clock
   *  or reset as a decode stimulus is meaningless. */
  clockPort: string;
  resetPort: string | null;
  /** The flops to decode. */
  group: string[];
  /** Reports the Python call behind the most recent action, for the `{ }`
   *  button of whichever panel is hosting this. */
  onCall?: (call: string) => void;
}

/**
 * Mounts the three decoding sections against `group`.
 *
 * Subscription-free on purpose: everything here hangs off a button handler, so
 * a host that re-mounts this on every selection change (which the Registers
 * panel does) cannot leak listeners.
 */
export function mountGroupTools(container: HTMLElement, opts: GroupToolsOptions): Mounted {
  const { design, notebook, group } = opts;
  const excluded = new Set([opts.clockPort, opts.resetPort].filter((p): p is string => p !== null));
  const inputs = opts.inputs.filter((p) => !excluded.has(p));
  const setCall = (call: string): void => opts.onCall?.(call);

      container.append(el("div", "rd-group-title", `group: ${group.join(", ")} (width ${group.length})`));

      // ---- weights --------------------------------------------------
      const weightsBox = el("div", "rd-section");
      weightsBox.append(el("div", "rd-section-title", "bit weights — drive this stimulus and watch for one-hot states"));
      const wStim = stimulusRow(inputs);
      const wCyclesLabel = el("label", "rd-inline", "cycles ");
      const wCycles = document.createElement("input");
      wCycles.type = "number";
      wCycles.value = String(Math.max(8, 2 ** group.length));
      wCyclesLabel.append(wCycles);
      const wRunBtn = document.createElement("button");
      wRunBtn.type = "button";
      wRunBtn.textContent = "▶ infer weights";
      const wOut = el("div", "rd-weights-out");
      weightsBox.append(wStim.row, wCyclesLabel, wRunBtn, wOut);
      container.append(weightsBox);

      wRunBtn.addEventListener("click", async () => {
        if (!design) return;
        wOut.replaceChildren(el("div", "rd-hint", "inferring…"));
        try {
          const { data, call } = await design.weights(group, wStim.read(), Math.max(1, Math.round(Number(wCycles.value) || 1)));
          setCall(call);
          renderWeights(wOut, group, data.weights);
        } catch (err) {
          wOut.replaceChildren(el("div", "rd-hint bad", err instanceof Error ? err.message : String(err)));
        }
      });

      // ---- orbit ------------------------------------------------------
      const orbitBox = el("div", "rd-section");
      orbitBox.append(el("div", "rd-section-title", "orbit — apply a stimulus repeatedly from reset"));
      const oStim = stimulusRow(inputs);
      const oRunBtn = document.createElement("button");
      oRunBtn.type = "button";
      oRunBtn.textContent = "▶ walk orbit";
      const oOut = el("div", "rd-orbit-out");
      orbitBox.append(oStim.row, oRunBtn, oOut);
      container.append(orbitBox);

      oRunBtn.addEventListener("click", async () => {
        if (!design) return;
        oOut.replaceChildren(el("div", "rd-hint", "walking…"));
        try {
          const { data, call } = await design.decodeOrbit(group, oStim.read(), null);
          setCall(call);
          oOut.replaceChildren();
          oOut.append(el("div", "rd-orbit-kind", `classification: ${data.kind}`));
          const claimable = CLAIMABLE_ROLE[data.kind];
          if (claimable) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = `pin as role: ${claimable}`;
            btn.addEventListener("click", () => {
              const claim: Claim = {
                kind: "role",
                group,
                role: claimable,
                width: group.length,
                stimulus: oStim.read(),
              };
              notebook.add(claim);
              btn.textContent = "pinned";
              btn.disabled = true;
            });
            oOut.append(btn);
          } else {
            oOut.append(
              el(
                "div",
                "rd-hint",
                data.kind === "unknown"
                  ? "a real cycle was found, but it fits none of the named shapes — not claimable"
                  : `"${data.kind}" is not one of the notebook's role claim kinds yet — described here, not claimable`,
              ),
            );
          }
          const states = el("div", "rd-orbit-states");
          for (const [i, state] of data.states.entries()) {
            states.append(el("span", "rd-orbit-state", `${i}:[${state.join("")}]`));
          }
          oOut.append(states);
        } catch (err) {
          oOut.replaceChildren(el("div", "rd-hint bad", err instanceof Error ? err.message : String(err)));
        }
      });

      // ---- select values ------------------------------------------------
      const selBox = el("div", "rd-section");
      selBox.append(el("div", "rd-section-title", "select values — which control-flop settings make this group react"));
      const controlInput = document.createElement("input");
      controlInput.type = "text";
      controlInput.placeholder = "control flop names, comma separated";
      const bStim = stimulusRow(inputs);
      const pulseLabel = el("label", "rd-inline", "then pulse ");
      const pulseSelect = document.createElement("select");
      for (const port of inputs) {
        const o = document.createElement("option");
        o.value = port;
        o.textContent = port;
        pulseSelect.append(o);
      }
      pulseLabel.append(pulseSelect);
      const selRunBtn = document.createElement("button");
      selRunBtn.type = "button";
      selRunBtn.textContent = "▶ sweep control";
      const selOut = el("div", "rd-select-out");
      selBox.append(controlInput, bStim.row, pulseLabel, selRunBtn, selOut);
      container.append(selBox);

      selRunBtn.addEventListener("click", async () => {
        if (!design) return;
        const control = names(controlInput.value);
        if (!control.length) {
          selOut.replaceChildren(el("div", "rd-hint bad", "name at least one control flop"));
          return;
        }
        const baseline = bStim.read();
        const perturb = { ...baseline, [pulseSelect.value]: baseline[pulseSelect.value] === 1 ? 0 : 1 };
        selOut.replaceChildren(el("div", "rd-hint", "sweeping…"));
        try {
          const { data, call } = await design.decodeSelects(group, control, baseline, perturb);
          setCall(call);
          selOut.replaceChildren();
          if (!data.hits.length) {
            selOut.append(el("div", "rd-hint", "no control combination made this group react"));
          } else {
            for (const hit of data.hits) {
              selOut.append(el("div", "rd-select-row", Object.entries(hit).map(([k, v]) => `${k}=${v}`).join(" ")));
            }
          }
        } catch (err) {
          selOut.replaceChildren(el("div", "rd-hint bad", err instanceof Error ? err.message : String(err)));
        }
      });

  function renderWeights(out: HTMLElement, group: string[], weights: Record<string, WeightView>): void {
    out.replaceChildren();
    for (const flop of group) {
      const w = weights[flop];
      const line = el("div", "rd-weight-line");
      line.append(instanceChip(flop, { onClick: false }));
      if (!w || w.confidence === "unknown") {
        const v = el("span", "rd-weight-unknown", " — (unknown: neither observed nor uniquely determined by elimination)");
        line.append(v);
      } else if (w.confidence === "observed") {
        line.append(el("span", "rd-weight-value", ` = ${w.value}`), el("span", "rd-weight-tag", " observed"));
      } else {
        const v = el("span", "rd-weight-value rd-by-elimination", ` = ${w.value}`);
        v.title = "inferred by elimination, not sighted directly one-hot in this window — not the same claim as an observed weight";
        const tag = el("span", "rd-weight-tag rd-by-elimination", " by elimination");
        tag.title = v.title;
        line.append(v, tag);
      }
      out.append(line);
    }
  }

  return {};
}
