// The Register Decoder (game-plan.md §4.11, needs L34-L36).
//
// The one rule this panel exists to keep: `weights.infer`'s `confidence`
// field is load-bearing, not decoration (analysis/weights.py's own docstring
// says so). `observed` renders in plain text; `by_elimination` renders in
// amber with a tooltip explaining why it could not be observed directly;
// `unknown` renders as a dash, not a zero -- zero is not itself a valid
// weight (weights are powers of two) and printing it would be a wrong
// answer dressed as a right one. The three are never styled the same, and
// never merged into one column without the distinction visible.
//
// Orbit classification (L36) is shown for all six `OrbitKind`s, but only
// promoted to a notebook `role` claim when the kind maps onto one the
// backend's claim verifier actually knows how to check (`counter`,
// `saturating` -> `saturating-counter`, `shift` -> `shift-reg`) --
// `analysis.claims.ROLES` does not include `wrapping` or `fixed-point`, and
// wiring a claim button to a role the verifier would reject on submission is
// worse than not offering it: `wrapping`/`fixed-point` results are described
// in the panel and are not claimable today.

import { instanceChip } from "./chips.ts";
import { attachPythonCallButton } from "./python-call.ts";
import type { PanelDef } from "../workspace/workspace.ts";
import type { DesignClient } from "../design/client.ts";
import type { WeightView } from "../gdsx-types.ts";
import { Notebook } from "../notebook/store.ts";
import type { Claim, Role } from "../notebook/model.ts";

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function names(text: string): string[] {
  return text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
}

/** `analysis.decode.OrbitKind` -> `notebook/model.ts`'s `Role`, only where
 *  `analysis.claims.ROLES` actually accepts the result. */
const CLAIMABLE_ROLE: Partial<Record<string, Role>> = {
  counter: "counter",
  saturating: "saturating-counter",
  shift: "shift-reg",
};

function toggleRow(labels: string[]): { row: HTMLElement; read: () => Record<string, number> } {
  const row = el("div", "rd-toggle-row");
  const buttons = new Map<string, HTMLButtonElement>();
  for (const name of labels) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = `${name}=0`;
    btn.dataset.value = "0";
    btn.addEventListener("click", () => {
      const next = btn.dataset.value === "0" ? "1" : "0";
      btn.dataset.value = next;
      btn.textContent = `${name}=${next}`;
      btn.classList.toggle("active", next === "1");
    });
    buttons.set(name, btn);
    row.append(btn);
  }
  return {
    row,
    read: () => {
      const out: Record<string, number> = {};
      for (const [name, btn] of buttons) out[name] = Number(btn.dataset.value);
      return out;
    },
  };
}

export interface RegisterDecoderPanelOptions {
  designReady: Promise<DesignClient>;
  puzzleId: string;
}

export function registerDecoderPanel(options: RegisterDecoderPanelOptions): PanelDef {
  return {
    id: "register-decoder",
    title: "Register Decoder",
    render(container: HTMLElement) {
      container.classList.add("rd-panel");
      container.innerHTML = `
        <div class="rd-loading">waiting on the analysis engine…</div>
        <div class="rd-body" hidden>
          <div class="rd-toolbar">
            <input type="text" class="rd-flops" placeholder="flop names, comma separated" />
            <button type="button" class="rd-find">find groups</button>
            <span class="rd-status"></span>
            <span class="py-call-slot"></span>
          </div>
          <div class="rd-groups"></div>
          <div class="rd-detail"></div>
        </div>`;

      const loadingEl = container.querySelector(".rd-loading") as HTMLDivElement;
      const bodyEl = container.querySelector(".rd-body") as HTMLDivElement;
      const flopsInput = container.querySelector(".rd-flops") as HTMLInputElement;
      const findBtn = container.querySelector(".rd-find") as HTMLButtonElement;
      const statusEl = container.querySelector(".rd-status") as HTMLSpanElement;
      const groupsEl = container.querySelector(".rd-groups") as HTMLDivElement;
      const detailEl = container.querySelector(".rd-detail") as HTMLDivElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      let design: DesignClient | null = null;
      let disposed = false;
      let inputs: string[] = [];
      let groups: string[][] = [];
      let lastCall: string | null = null;
      const notebook = new Notebook(options.puzzleId);

      attachPythonCallButton(callSlot, () => lastCall);

      async function findGroups(): Promise<void> {
        if (!design) return;
        const flops = names(flopsInput.value);
        if (!flops.length) return;
        statusEl.textContent = "grouping…";
        statusEl.className = "rd-status";
        try {
          const { data, call } = await design.grouping(flops);
          lastCall = call;
          groups = data.groups;
          statusEl.textContent = `${groups.length} group${groups.length === 1 ? "" : "s"}`;
          renderGroups();
        } catch (err) {
          statusEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
          statusEl.className = "rd-status bad";
        }
      }

      function renderGroups(): void {
        groupsEl.replaceChildren();
        for (const group of groups) {
          const chip = el("div", "rd-group-chip", `[${group.join(", ")}]`);
          chip.addEventListener("click", () => selectGroup(group));
          groupsEl.append(chip);
        }
      }

      function selectGroup(group: string[]): void {
        detailEl.replaceChildren();
        detailEl.append(el("div", "rd-group-title", `group: ${group.join(", ")} (width ${group.length})`));

        // ---- weights --------------------------------------------------
        const weightsBox = el("div", "rd-section");
        weightsBox.append(el("div", "rd-section-title", "bit weights — drive this stimulus and watch for one-hot states"));
        const wStim = toggleRow(inputs);
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
        detailEl.append(weightsBox);

        wRunBtn.addEventListener("click", async () => {
          if (!design) return;
          wOut.replaceChildren(el("div", "rd-hint", "inferring…"));
          try {
            const { data, call } = await design.weights(group, wStim.read(), Math.max(1, Math.round(Number(wCycles.value) || 1)));
            lastCall = call;
            renderWeights(wOut, group, data.weights);
          } catch (err) {
            wOut.replaceChildren(el("div", "rd-hint bad", err instanceof Error ? err.message : String(err)));
          }
        });

        // ---- orbit ------------------------------------------------------
        const orbitBox = el("div", "rd-section");
        orbitBox.append(el("div", "rd-section-title", "orbit — apply a stimulus repeatedly from reset"));
        const oStim = toggleRow(inputs);
        const oRunBtn = document.createElement("button");
        oRunBtn.type = "button";
        oRunBtn.textContent = "▶ walk orbit";
        const oOut = el("div", "rd-orbit-out");
        orbitBox.append(oStim.row, oRunBtn, oOut);
        detailEl.append(orbitBox);

        oRunBtn.addEventListener("click", async () => {
          if (!design) return;
          oOut.replaceChildren(el("div", "rd-hint", "walking…"));
          try {
            const { data, call } = await design.decodeOrbit(group, oStim.read(), null);
            lastCall = call;
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
        const bStim = toggleRow(inputs);
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
        detailEl.append(selBox);

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
            lastCall = call;
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
      }

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

      findBtn.addEventListener("click", () => void findGroups());
      flopsInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") void findGroups();
      });

      options.designReady
        .then(async (d) => {
          if (disposed) return;
          design = d;
          const { data } = await d.claimVocabulary();
          if (disposed) return;
          inputs = data.inputs;
          loadingEl.hidden = true;
          bodyEl.hidden = false;
        })
        .catch((err) => {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        });

      return {
        dispose() {
          disposed = true;
        },
      };
    },
  };
}