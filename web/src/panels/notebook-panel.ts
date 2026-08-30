// The Notebook (game-plan.md §5): the panel that makes justified belief the
// scored object, and the game's central mechanic.
//
// Three things it does that a list of notes would not:
//
// 1. **A claim is structured, so it is checkable.** Eight forms, one per claim
//    type, with the fields validated against the real design -- a claim about a
//    net that does not exist is caught while you are typing it, not reported as
//    a verdict about a variable the design never had.
// 2. **The verdict's strength is as prominent as the verdict.** PROVEN is green
//    with its case count; LIKELY is amber and says "no counterexample in N
//    vectors" and never contains the word "proven". Every colour and every
//    label on this page comes from `verdictStyle`, which is the only place
//    either is chosen; this file never branches on a verdict itself.
// 3. **A disproof stays.** Struck through, timestamped, with its counterexample
//    one click from the waveform. Disproving your own earlier conclusion is the
//    best thing that happens in a real session and the notebook treats it that
//    way.
//
// The cost estimate above the verify button is deliberate: the player is told
// "22 leaves · sampled · 10K cases" and that the answer can only be LIKELY
// BEFORE they spend attention on it, rather than being surprised by an amber
// verdict afterwards.

import type { DesignClient, ClaimVocabulary } from "../design/client";
import type { SimStore } from "../sim/store";
import { instanceChip, netChip } from "./chips";
import { attachPythonCallButton } from "./python-call";
import type { PanelDef } from "../workspace/workspace";
import {
  type Claim,
  type ClaimKind,
  type ClaimRecord,
  type Stimulus,
  claimText,
  latest,
  verdictStyle,
} from "../notebook/model";
import { notebookFor } from "../notebook/store";
import { evidenceLog, type EvidenceRecord } from "../notebook/evidence";
import { VerifyEngine, estimate } from "../notebook/engine";
import { asPercent, coverage, pointsFor, type Coverage } from "../notebook/scoring";
import { generateWriteup, type WriteupSession } from "../notebook/writeup";
import { ModelStore } from "../model/store";

const KIND_LABELS: Record<ClaimKind, string> = {
  structural: "Structural — a net's driver",
  support: "Support — what a flop's D depends on",
  function: "Function — what a flop's D computes",
  role: "Role — what a register group is",
  invariant: "Invariant — when a net is 1",
  requirement: "Requirement — what an output needs",
  timing: "Timing — which cycles an event lands on",
  constraint: "Constraint — what every key satisfies",
};

function el(tag: string, className?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function when(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** A text input backed by a `<datalist>` of real names from the design. */
function suggest(placeholder: string, listId: string): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "nb-input";
  input.placeholder = placeholder;
  input.setAttribute("list", listId);
  return input;
}

function datalist(id: string, values: readonly string[]): HTMLDataListElement {
  const list = document.createElement("datalist");
  list.id = id;
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    list.append(option);
  }
  return list;
}

function select(values: readonly string[], labels?: Record<string, string>): HTMLSelectElement {
  const box = document.createElement("select");
  box.className = "nb-select";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels?.[value] ?? value;
    box.append(option);
  }
  return box;
}

function field(label: string, ...controls: HTMLElement[]): HTMLElement {
  const row = el("div", "nb-field");
  row.append(el("label", "nb-label", label), ...controls);
  return row;
}

function numbers(text: string): number[] {
  return text
    .split(/[\s,]+/)
    .map((part) => Number.parseInt(part, 10))
    .filter((n) => Number.isFinite(n));
}

function names(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

export interface NotebookPanelOptions {
  designReady: Promise<DesignClient>;
  storeReady: Promise<SimStore>;
  puzzleId: string;
  /** The net whose fan-in cone coverage is measured against, per §5. */
  /** Null for a puzzle with no lock. Coverage is then measured over the
   *  claims alone, there being no success cone to measure against. */
  successNet: string | null;
  /** The sequence-editor port the write-up prints as the final key (§8).
   *  Null for a puzzle with no data input, where there is no key to print. */
  keyPort: string | null;
  /** The solve's score, timings and hints for the write-up's summary
   *  sections, or null while the puzzle is unsolved. A function because it is
   *  read at the moment the write-up is generated, and supplied by boot.ts
   *  because the par time and the hint text live on the puzzle descriptor,
   *  which this panel deliberately does not import. */
  session?: () => WriteupSession | null;
  /** Called with each freshly computed coverage, so the post-solve score can
   *  use it. Deliberately not a display hook any more: the cone denominator
   *  needs a design call and a step through the success flop, which is this
   *  panel's job, and boot.ts only caches the result. Nothing shows coverage
   *  live outside this panel (game-plan.md §8). */
  onCoverage?: (found: Coverage) => void;
}

export function notebookPanel(options: NotebookPanelOptions): PanelDef {
  return {
    id: "notebook",
    title: "Notebook",
    render(container: HTMLElement) {
      container.classList.add("nb-panel");
      container.innerHTML = `
        <div class="nb-toolbar">
          <span class="nb-coverage">coverage —</span>
          <span class="nb-spacer"></span>
          <button class="nb-new" type="button" disabled>+ new claim</button>
          <button class="nb-export" type="button">export write-up</button>
          <span class="py-call-slot"></span>
        </div>
        <div class="nb-form" hidden></div>
        <div class="nb-writeup" hidden>
          <div class="nb-writeup-toolbar">
            <span class="nb-writeup-hint">generated from the notebook, evidence, model and current key -- edit freely, this is your copy</span>
            <button class="nb-writeup-download" type="button">download .md</button>
            <button class="nb-writeup-close" type="button">close</button>
          </div>
          <textarea class="nb-writeup-text" spellcheck="false"></textarea>
        </div>
        <div class="nb-loading">waiting on the analysis engine…</div>
        <div class="nb-list" hidden></div>
        <div class="nb-evidence"></div>`;

      const coverageEl = container.querySelector(".nb-coverage") as HTMLSpanElement;
      const newBtn = container.querySelector(".nb-new") as HTMLButtonElement;
      const exportBtn = container.querySelector(".nb-export") as HTMLButtonElement;
      const formEl = container.querySelector(".nb-form") as HTMLDivElement;
      const writeupEl = container.querySelector(".nb-writeup") as HTMLDivElement;
      const writeupText = container.querySelector(".nb-writeup-text") as HTMLTextAreaElement;
      const writeupDownload = container.querySelector(".nb-writeup-download") as HTMLButtonElement;
      const writeupClose = container.querySelector(".nb-writeup-close") as HTMLButtonElement;
      const loadingEl = container.querySelector(".nb-loading") as HTMLDivElement;
      const listEl = container.querySelector(".nb-list") as HTMLDivElement;
      const evidenceEl = container.querySelector(".nb-evidence") as HTMLDivElement;
      const callSlot = container.querySelector(".py-call-slot") as HTMLSpanElement;

      const notebook = notebookFor(options.puzzleId);
      // The same log the Experiment Runner writes to: two panels looking at one
      // notebook, not two notebooks. Evidence sits below the claims and scores
      // nothing -- a measurement is not an assertion, and the points are in
      // what you conclude from it.
      const evidence = evidenceLog(options.puzzleId);
      let lastCall: string | null = null;
      let engine: VerifyEngine | null = null;
      let vocabulary: ClaimVocabulary | null = null;
      let store: SimStore | null = null;
      let coneNets: string[] = [];
      let disposed = false;
      const busy = new Set<string>();
      const progress = new Map<string, number>();

      attachPythonCallButton(callSlot, () => lastCall);

      // ---- stimulus snapshots ------------------------------------------

      function snapshot(): Stimulus {
        if (!store) return { name: "empty", cycles: 0, tracks: {} };
        const tracks: Record<string, string> = {};
        for (const port of store.inputPorts) {
          tracks[port] = Array.from(store.bitsOf(port), (b) => (b ? "1" : "0")).join("");
        }
        return { name: "the current sequence", cycles: store.cycles, tracks };
      }

      /** Load a counterexample trace into the sequence editor and waveform. */
      function loadTrace(tracks: Record<string, string>): void {
        if (!store) return;
        for (const [port, bits] of Object.entries(tracks)) store.setPattern(port, bits);
      }

      // ---- the list ----------------------------------------------------

      function renderVerdict(record: ClaimRecord): HTMLElement {
        const current = latest(record);
        if (!current) {
          if (busy.has(record.id)) {
            const percent = progress.get(record.id);
            return el(
              "span",
              "nb-verdict nb-tone-grey",
              percent === undefined ? "verifying…" : `verifying… ${percent}%`,
            );
          }
          return el("span", "nb-verdict nb-tone-grey", "not verified");
        }
        // Every colour and every word about strength comes from here. This file
        // does not get to decide what a verdict looks like.
        const style = verdictStyle(current.verdict);
        const box = el("span", `nb-verdict nb-tone-${style.tone}`, style.label);
        if (busy.has(record.id)) {
          const percent = progress.get(record.id);
          box.textContent = percent === undefined ? "verifying…" : `verifying… ${percent}%`;
          box.className = "nb-verdict nb-tone-grey";
        }
        box.title = `${when(current.at)}${current.notes.length ? ` · ${current.notes.join(" · ")}` : ""}`;
        return box;
      }

      function renderRecord(record: ClaimRecord): HTMLElement {
        const current = latest(record);
        const style = current ? verdictStyle(current.verdict) : null;
        const row = el("div", "nb-row");

        const head = el("div", "nb-row-head");
        const text = el("div", "nb-claim-text", claimText(record.claim));
        if (style?.strike) text.classList.add("nb-struck");
        head.append(text, renderVerdict(record));
        row.append(head);

        if (current) {
          const meta = el("div", "nb-row-meta");
          meta.append(el("span", "nb-time", when(current.at)));
          meta.append(el("span", "nb-points", `+${pointsFor(record)}`));
          row.append(meta);

          for (const note of current.notes) {
            row.append(el("div", "nb-note", note));
          }

          if (current.verdict.kind === "DISPROVEN") {
            const detail = el("div", "nb-detail");
            if (current.verdict.observed.length) {
              detail.append(el("div", undefined, `observed: ${current.verdict.observed.join(", ")}`));
            }
            if (current.verdict.expected.length) {
              detail.append(el("div", undefined, `expected: ${current.verdict.expected.join(", ")}`));
            }
            const witness = current.verdict.counterexample;
            if (witness?.kind === "assignment") {
              const chips = el("div", "nb-witness");
              chips.append(el("span", "nb-witness-label", "counterexample:"));
              for (const [net, value] of Object.entries(witness.leaves)) {
                chips.append(netChip(net, { suffix: `=${value}` }));
              }
              detail.append(chips);
            } else if (witness?.kind === "state") {
              const chips = el("div", "nb-witness");
              chips.append(el("span", "nb-witness-label", "from state:"));
              for (const [net, value] of Object.entries(witness.flops)) {
                chips.append(instanceChip(net, { suffix: `=${value}` }));
              }
              detail.append(chips);
            } else if (witness?.kind === "trace") {
              const load = el("button", "nb-load", "load into the waveform") as HTMLButtonElement;
              load.addEventListener("click", () => loadTrace(witness.tracks));
              detail.append(load);
            }
            row.append(detail);
          }
        }

        const actions = el("div", "nb-actions");
        const verifyBtn = el("button", "nb-verify", current ? "re-verify" : "verify") as HTMLButtonElement;
        verifyBtn.disabled = busy.has(record.id) || !engine;
        verifyBtn.addEventListener("click", () => void run(record));
        const removeBtn = el("button", "nb-remove", "remove") as HTMLButtonElement;
        removeBtn.addEventListener("click", () => {
          notebook.remove(record.id);
          refresh();
        });
        actions.append(verifyBtn, removeBtn);

        // Earlier verdicts, kept. A claim that has been disproven and then
        // re-verified shows both, in order -- that history IS the write-up.
        if (record.history.length > 1) {
          const older = el("details", "nb-history");
          older.append(el("summary", undefined, `${record.history.length - 1} earlier`));
          for (const entry of record.history.slice(0, -1)) {
            const past = verdictStyle(entry.verdict);
            older.append(el("div", "nb-history-row", `${when(entry.at)} · ${past.label}`));
          }
          row.append(older);
        }
        row.append(actions);
        return row;
      }

      function renderEvidence(record: EvidenceRecord): HTMLElement {
        const row = el("div", "nb-row nb-evidence-row");
        const head = el("div", "nb-row-head");
        head.append(
          el("div", "nb-claim-text", record.recipe),
          el("span", "nb-verdict nb-tone-grey", "evidence"),
        );
        row.append(head);
        row.append(el("div", "nb-row-meta", `${when(record.at)} · ${record.summary}`));
        // The conditions, never dropped: a sweep's numbers are about the
        // baseline it was measured against.
        row.append(el("div", "nb-note", `measured against ${record.baseline}`));
        for (const warning of record.warnings) {
          row.append(el("div", "nb-note nb-warning", `⚠ ${warning}`));
        }
        const detail = el("details", "nb-history");
        detail.append(el("summary", undefined, `${record.lines.length} lines`));
        for (const line of record.lines) detail.append(el("div", "nb-history-row", line));
        row.append(detail);

        const actions = el("div", "nb-actions");
        const show = el("button", "nb-verify", "show the call") as HTMLButtonElement;
        show.addEventListener("click", () => {
          lastCall = record.call;
          callSlot.querySelector("button")?.click();
        });
        const removeBtn = el("button", "nb-remove", "remove") as HTMLButtonElement;
        removeBtn.addEventListener("click", () => evidence.remove(record.id));
        actions.append(show, removeBtn);
        row.append(actions);
        return row;
      }

      function refreshEvidence(): void {
        if (disposed) return;
        evidenceEl.replaceChildren();
        const records = evidence.all();
        if (records.length === 0) return;
        evidenceEl.append(
          el("div", "nb-section", `evidence · ${records.length} experiment(s), unscored`),
        );
        for (const record of [...records].reverse()) evidenceEl.append(renderEvidence(record));
      }

      function refresh(): void {
        if (disposed) return;
        listEl.replaceChildren();
        const records = notebook.all();
        if (records.length === 0) {
          listEl.append(
            el("div", "nb-empty", "no claims yet — the notebook is what gets scored"),
          );
        }
        for (const record of [...records].reverse()) listEl.append(renderRecord(record));

        if (vocabulary) {
          const found = coverage(notebook, vocabulary.flops, coneNets);
          const text =
            `coverage ${asPercent(found.fraction)} · roles ${found.roles.done}/${found.roles.total}` +
            ` · cone ${found.cone.done}/${found.cone.total}`;
          coverageEl.textContent = text;
          // Coverage, yes; score, no. §8 is explicit that points are shown
          // after solving and never as live pressure. It is shown HERE and
          // nowhere else: the same number in the title bar was read as a
          // score, which is why the toolbar's status slot no longer exists.
          coverageEl.title =
            "flops with a proven role claim, and the part of the success cone " +
            "a settled claim names";
          options.onCoverage?.(found);
        }
      }

      async function run(record: ClaimRecord): Promise<void> {
        if (!engine || busy.has(record.id)) return;
        busy.add(record.id);
        progress.delete(record.id);
        refresh();
        try {
          const verdict = await engine.verify(record.claim, (percent) => {
            progress.set(record.id, percent);
            refresh();
          });
          lastCall = verdict.call;
          notebook.record(record.id, verdict);
        } catch (err) {
          notebook.record(record.id, {
            verdict: {
              kind: "UNKNOWN",
              reason: err instanceof Error ? err.message : String(err),
            },
            at: Date.now(),
            notes: [],
            call: "",
          });
        } finally {
          busy.delete(record.id);
          progress.delete(record.id);
          refresh();
        }
      }

      // ---- the form ----------------------------------------------------

      function buildForm(): void {
        if (!vocabulary) return;
        formEl.replaceChildren();
        formEl.hidden = false;

        const kindBox = select(Object.keys(KIND_LABELS), KIND_LABELS);
        formEl.append(field("claim", kindBox));

        const nets = datalist("nb-nets", coneNets.length ? coneNets : []);
        const flops = datalist("nb-flops", vocabulary.flops);
        const ports = datalist("nb-ports", vocabulary.inputs);
        formEl.append(nets, flops, ports);

        const body = el("div", "nb-form-body");
        const footer = el("div", "nb-form-footer");
        const cost = el("span", "nb-cost", "");
        const addBtn = el("button", "nb-add", "add to the notebook") as HTMLButtonElement;
        const cancelBtn = el("button", "nb-cancel", "cancel") as HTMLButtonElement;
        footer.append(cost, addBtn, cancelBtn);
        formEl.append(body, footer);

        let build: () => Claim | string = () => "";

        function rebuild(): void {
          body.replaceChildren();
          const kind = kindBox.value as ClaimKind;
          build = FORMS[kind](body, vocabulary!, snapshot);
          void estimateCost();
        }

        async function estimateCost(): Promise<void> {
          const claim = build();
          if (typeof claim === "string") {
            cost.textContent = claim;
            cost.className = "nb-cost nb-cost-idle";
            return;
          }
          if (!engine) return;
          try {
            const { data } = await engine.plan(claim);
            const found = estimate(data);
            cost.textContent = found.text;
            // The honesty that has to arrive BEFORE the verdict, not after.
            cost.className = `nb-cost ${found.sampledOnly ? "nb-cost-sampled" : "nb-cost-ok"}`;
            if (found.sampledOnly) cost.textContent += " · can only be LIKELY";
          } catch (err) {
            cost.textContent = err instanceof Error ? err.message : String(err);
            cost.className = "nb-cost nb-cost-bad";
          }
        }

        kindBox.addEventListener("change", rebuild);
        body.addEventListener("input", () => void estimateCost());
        body.addEventListener("change", () => void estimateCost());

        addBtn.addEventListener("click", () => {
          const claim = build();
          if (typeof claim === "string") {
            cost.textContent = claim || "fill the fields in first";
            cost.className = "nb-cost nb-cost-bad";
            return;
          }
          const record = notebook.add(claim);
          formEl.hidden = true;
          refresh();
          void run(record);
        });
        cancelBtn.addEventListener("click", () => {
          formEl.hidden = true;
        });

        rebuild();
      }

      newBtn.addEventListener("click", () => {
        if (formEl.hidden) buildForm();
        else formEl.hidden = true;
      });

      // ---- the write-up (game-plan.md §8) -------------------------------
      //
      // Generated fresh from the current state of every store each time the
      // button is pressed -- nothing about it is persisted separately, because
      // the notebook, evidence log and model store already are the
      // persistence layer. `generateWriteup` is pure; this closure only wires
      // it to the DOM (editable textarea, one-click download).

      exportBtn.addEventListener("click", () => {
        if (!store) return;
        // A fresh instance reads whatever is currently saved under this
        // puzzle's key -- the same localStorage record the Model Builder
        // panel writes, not a second copy of it.
        const modelStore = new ModelStore(options.puzzleId, "");
        const markdown = generateWriteup(
          notebook,
          evidence,
          modelStore,
          store,
          {
            successNet: options.successNet,
            keyPort: options.keyPort,
            puzzleId: options.puzzleId,
          },
          options.session?.() ?? null,
        );
        writeupText.value = markdown;
        writeupEl.hidden = false;
        formEl.hidden = true;
      });

      writeupClose.addEventListener("click", () => {
        writeupEl.hidden = true;
      });

      writeupDownload.addEventListener("click", () => {
        const blob = new Blob([writeupText.value], { type: "text/markdown" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${options.puzzleId}-writeup.md`;
        a.click();
        URL.revokeObjectURL(url);
      });

      const unsub = notebook.subscribe(refresh);
      const unsubEvidence = evidence.subscribe(refreshEvidence);
      refresh();
      refreshEvidence();

      void (async () => {
        try {
          const [design, simStore] = await Promise.all([
            options.designReady,
            options.storeReady,
          ]);
          if (disposed) return;
          store = simStore;
          const vocab = await design.claimVocabulary();
          vocabulary = vocab.data;

          // One cone call at load, for the coverage denominator. Not recomputed
          // per claim -- coverage is a progress bar, not an analysis.
          //
          // The step through the flop is not optional. `success` is a port
          // driven by a flop, so its own fan-in cone is a single `flop_q` leaf
          // and a denominator of 1 would read 100% explained off one claim.
          // What "the success cone" means is the cone of the flop's D, one cycle
          // earlier -- the same step the cone walker makes explicit.
          try {
            const lock = options.successNet;
            // No lock, no success cone: coverage falls back to the claims
            // alone rather than being measured against an invented net.
            if (lock === null) throw new Error("this puzzle declares no lock");
            let root = lock;
            const head = await design.cone(root, { depth: 1 });
            if (head.data.leaf === "flop_q") {
              const stepped = await design.flopDNet(root);
              if (stepped.data.net) root = stepped.data.net;
            }
            const cone = await design.cone(root, { depth: 40 });
            // The port itself counts as part of its own cone: a claim about
            // `success` is a claim about the success cone in any sense a player
            // means it, even though the D-cone walk starts below it.
            coneNets = [...new Set([lock, ...collect(cone.data)])];
          } catch {
            coneNets = [];
          }

          engine = new VerifyEngine({
            design,
            sim: () => ({
              tape: simStore.tape,
              resetVector: simStore.resetVector,
              inputPorts: simStore.inputPorts,
            }),
            baseline: snapshot,
          });
          loadingEl.hidden = true;
          listEl.hidden = false;
          newBtn.disabled = false;
          refresh();
        } catch (err) {
          loadingEl.textContent = `ERROR: ${err instanceof Error ? err.message : String(err)}`;
        }
      })();

      return {
        dispose() {
          disposed = true;
          unsub();
          unsubEvidence();
          engine?.dispose();
        },
      };
    },
  };
}

/** Every net named anywhere in a cone tree. */
function collect(node: { net: string; children: { net: string; children: unknown[] }[] }): string[] {
  const found: string[] = [];
  const stack = [node];
  while (stack.length) {
    const current = stack.pop() as typeof node;
    found.push(current.net);
    for (const child of current.children) stack.push(child as typeof node);
  }
  return [...new Set(found)];
}

// ---- one builder per claim type ----------------------------------------
//
// Each returns a function producing either a `Claim` or a string explaining
// what is still missing. Nothing unparseable can leave a form: the fields are
// typed, the dropdowns are built from the design's own vocabulary, and the
// names autocomplete against real nets and flops.

type Builder = (
  body: HTMLElement,
  vocabulary: ClaimVocabulary,
  stimulus: () => Stimulus,
) => () => Claim | string;

const FORMS: Record<ClaimKind, Builder> = {
  structural(body) {
    const net = suggest("net", "nb-nets");
    const cell = suggest("cell, e.g. nand2", "nb-cells");
    body.append(field("net", net), field("is driven by", cell));
    return () =>
      net.value.trim() && cell.value.trim()
        ? { kind: "structural", net: net.value.trim(), cell: cell.value.trim() }
        : "";
  },

  support(body) {
    const flop = suggest("flop", "nb-flops");
    const leaves = suggest("net, net, net…", "nb-nets");
    const sense = select(["structural", "functional"]);
    body.append(
      field("D of", flop),
      field("depends exactly on", leaves),
      field("in the sense", sense),
    );
    // Two senses, side by side, because they are not the same claim: a net can
    // sit in the structural cone and never change the output.
    body.append(
      el(
        "div",
        "nb-hint",
        "structural = it is in the cone (a graph fact). " +
          "functional = it can actually change D (checked over assignments).",
      ),
    );
    return () =>
      flop.value.trim()
        ? {
            kind: "support",
            flop: flop.value.trim(),
            leaves: names(leaves.value),
            sense: sense.value as "structural" | "functional",
          }
        : "";
  },

  function(body) {
    const flop = suggest("flop", "nb-flops");
    const expression = suggest("n96 & ~n136", "nb-nets");
    expression.classList.add("nb-wide");
    body.append(field("D of", flop), field("==", expression));
    return () =>
      flop.value.trim() && expression.value.trim()
        ? { kind: "function", flop: flop.value.trim(), expression: expression.value.trim() }
        : "";
  },

  role(body, vocabulary) {
    const group = suggest("flop, flop, flop…", "nb-flops");
    group.classList.add("nb-wide");
    const role = select(vocabulary.roles);
    body.append(field("the group", group), field("is a", role));
    body.append(
      el(
        "div",
        "nb-hint",
        "bit 0 is the first flop you name. Everything outside the group is " +
          "held at 0 for the walk; the verdict's notes say which nets those are.",
      ),
    );
    return () => {
      const flops = names(group.value);
      return flops.length
        ? { kind: "role", group: flops, role: role.value as never, width: flops.length, stimulus: {} }
        : "";
    };
  },

  invariant(body) {
    const net = suggest("net", "nb-nets");
    const value = select(["1", "0"]);
    const condition = suggest("I & enable", "nb-nets");
    condition.classList.add("nb-wide");
    const regime = select(["frame", "sequential"]);
    body.append(
      field("net", net),
      field("is", value),
      field("whenever", condition),
      field("over", regime),
    );
    body.append(
      el(
        "div",
        "nb-hint",
        "frame = one clock cycle, and can be proven. " +
          "sequential = over time, and cannot: it is LIKELY at best.",
      ),
    );
    return () =>
      net.value.trim() && condition.value.trim()
        ? {
            kind: "invariant",
            net: net.value.trim(),
            value: Number(value.value),
            condition: condition.value.trim(),
            regime: regime.value as "frame" | "sequential",
          }
        : "";
  },

  requirement(body, vocabulary) {
    const output = suggest("output net", "nb-nets");
    output.value = vocabulary.outputs[0] ?? "";
    const value = select(["1", "0"]);
    const flop = suggest("flop", "nb-flops");
    const flopValue = select(["1", "0"]);
    body.append(
      field("for", output),
      field("to be", value),
      field("this flop", flop),
      field("must be", flopValue),
    );
    return () =>
      output.value.trim() && flop.value.trim()
        ? {
            kind: "requirement",
            output: output.value.trim(),
            value: Number(value.value),
            flop: flop.value.trim(),
            flop_value: Number(flopValue.value),
          }
        : "";
  },

  timing(body, vocabulary, stimulus) {
    const net = suggest("net", "nb-nets");
    const event = select(vocabulary.events);
    const cycles = suggest("121, 122", "nb-none");
    body.append(field("net", net), field("", event), field("on cycles", cycles));
    body.append(
      el(
        "div",
        "nb-hint",
        "snapshots the sequence editor as it is now. The verdict is about that " +
          "sequence and is always shown that way; editing it later does not " +
          "change what was proven.",
      ),
    );
    return () =>
      net.value.trim()
        ? {
            kind: "timing",
            net: net.value.trim(),
            event: event.value as never,
            cycles: numbers(cycles.value),
            stimulus: stimulus(),
          }
        : "";
  },

  constraint(body, vocabulary, stimulus) {
    const output = suggest("output net", "nb-nets");
    output.value = vocabulary.outputs[0] ?? "";
    const predicate = suggest("count(I) == 22 & mingap(I) >= 2", "nb-none");
    predicate.classList.add("nb-wide");
    body.append(field("any key that latches", output), field("satisfies", predicate));
    const measures = Object.entries(vocabulary.measures)
      .map(([name, what]) => `${name}(port) — ${what}`)
      .join("\n");
    const hint = el("div", "nb-hint", measures);
    hint.style.whiteSpace = "pre-line";
    body.append(hint);
    return () =>
      output.value.trim() && predicate.value.trim()
        ? {
            kind: "constraint",
            output: output.value.trim(),
            predicate: predicate.value.trim(),
            stimulus: stimulus(),
          }
        : "";
  },
};