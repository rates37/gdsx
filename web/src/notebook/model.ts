// The notebook's vocabulary: what a claim is, what a verdict is, and the one
// function that decides how a verdict looks.
//
// There is one rule this file exists to enforce, and it is the whole
// intellectual point of the game:
//
//     LIKELY must never be displayed as PROVEN.
//
// "I proved this over all 1,048,576 cases" and "I sampled 10,000 vectors and
// nothing broke" are different claims about the world. Two things keep them
// apart here, and neither is discipline:
//
//   1. There is no `verified` boolean anywhere in this model. No `ok`, no
//      `passed`, no `isProven`. The only way to learn anything about a verdict
//      is to switch on `verdict.kind`, so there is no field for a hurried edit
//      to collapse the two into.
//   2. `verdictStyle` is the ONLY place a colour or a label is chosen, and it
//      is an exhaustive switch with no `default` branch. Adding a fifth verdict
//      is then a compile error rather than a silent fall-through into green.
//
// tests: web/scripts/test-notebook-verify.mjs asserts PROVEN and LIKELY differ
// in both tone and wording, so collapsing them fails CI rather than shipping.

/** A witness. `null` on a structural disproof, which needs no witness --
 *  "you said n96 is driven by a NAND2; it is driven by an AOI21". */
export type Vector =
  /** One assignment of a cone's leaves: what a combinational counterexample is. */
  | { kind: "assignment"; leaves: Record<string, number> }
  /** A stimulus, loadable straight into the sequence editor and the waveform. */
  | { kind: "trace"; cycles: number; tracks: Record<string, string> }
  /** A starting state of a register group: what a role counterexample is. */
  | { kind: "state"; flops: Record<string, number> };

export type Verdict =
  | { kind: "PROVEN"; method: "exhaustive" | "structural"; cases: number }
  /** A deterministic replay of ONE stimulus -- see sequential.ts's header. It
   *  is not quantified over inputs, so it carries the sequence it is about and
   *  is never labelled the way an exhaustive sweep is. `cases` counts cycles. */
  | { kind: "PROVEN"; method: "replay"; cases: number; sequence: string }
  | {
      kind: "DISPROVEN";
      counterexample: Vector | null;
      observed: string[];
      expected: string[];
    }
  /** No counterexample found -- which is not a proof, and never rendered as one. */
  | { kind: "LIKELY"; method: "sampled"; cases: number }
  | { kind: "UNKNOWN"; reason: string };

/** How a PROVEN verdict was reached. Named so scoring.ts can price every one
 *  of them exhaustively, and adding a fourth is a compile error there. */
export type ProvenMethod = Extract<Verdict, { kind: "PROVEN" }>["method"];

export type Tone = "green" | "amber" | "red" | "grey";

export interface VerdictStyle {
  tone: Tone;
  /** What the player reads. An exhaustive `PROVEN` says how many cases; a
   *  replay says which sequence and how many cycles, and claims nothing beyond
   *  it; `LIKELY` says how many vectors found nothing. Only the first says
   *  "proven" -- the other two are not quantified over a space of inputs. */
  label: string;
  /** Disproven claims stay in the notebook struck through, with a timestamp.
   *  A disproof is a result, not a mistake to be swept up. */
  strike: boolean;
}

const COUNT = new Intl.NumberFormat("en-US");

export function verdictStyle(verdict: Verdict): VerdictStyle {
  switch (verdict.kind) {
    case "PROVEN":
      // Three different things, and the middle one is why this is not a
      // two-way ternary any more. A replay settled ONE stimulus: it is real
      // evidence, so it is green, but calling it "proven · 121 cases" would
      // read as a sweep over 121 inputs, which is the confusion this whole
      // module exists to prevent. It says what actually happened instead.
      if (verdict.method === "replay") {
        return {
          tone: "green",
          label:
            `replayed under sequence ⟨${verdict.sequence}⟩ · ` +
            `${COUNT.format(verdict.cases)} cycles`,
          strike: false,
        };
      }
      return {
        tone: "green",
        label:
          verdict.method === "structural"
            ? "proven · structural"
            : `proven · ${COUNT.format(verdict.cases)} cases`,
        strike: false,
      };
    case "LIKELY":
      return {
        tone: "amber",
        label: `no counterexample in ${COUNT.format(verdict.cases)} vectors`,
        strike: false,
      };
    case "DISPROVEN":
      return { tone: "red", label: "disproven", strike: true };
    case "UNKNOWN":
      return { tone: "grey", label: `unknown · ${verdict.reason}`, strike: false };
  }
}

// ---- claims -------------------------------------------------------------

export type Role = "counter" | "lfsr" | "shift-reg" | "saturating-counter";
export type TimingEvent = "high" | "low" | "rises" | "falls" | "latches-high";

/** A snapshot of the sequence editor at the moment a claim was made. A timing
 *  or constraint claim without its stimulus is not a claim, so it carries one
 *  and the verdict is always rendered "under sequence ⟨name⟩". */
export interface Stimulus {
  name: string;
  cycles: number;
  /** port -> "0101…", one character per cycle. */
  tracks: Record<string, string>;
}

export type Claim =
  | { kind: "structural"; net: string; cell: string }
  | {
      kind: "support";
      flop: string;
      leaves: string[];
      /** Structural support is a graph fact. Functional support is stronger and
       *  different: a net can sit in the cone and never change the output. */
      sense: "structural" | "functional";
    }
  | { kind: "function"; flop: string; expression: string }
  | {
      kind: "role";
      group: string[];
      role: Role;
      width: number;
      /** Everything outside the group is held here for the whole walk, so the
       *  claim is conditional on it and the plan's notes say so. */
      stimulus: Record<string, number>;
    }
  | {
      kind: "invariant";
      net: string;
      value: number;
      condition: string;
      /** "frame" is one clock cycle and can be proven. "sequential" is over
       *  time and cannot: it is LIKELY at best, said before you press verify. */
      regime: "frame" | "sequential";
    }
  | {
      kind: "requirement";
      output: string;
      value: number;
      flop: string;
      flop_value: number;
    }
  | {
      kind: "timing";
      net: string;
      event: TimingEvent;
      cycles: number[];
      stimulus: Stimulus;
    }
  | { kind: "constraint"; output: string; predicate: string; stimulus: Stimulus };

export type ClaimKind = Claim["kind"];

export interface VerdictRecord {
  verdict: Verdict;
  /** Epoch milliseconds. Shown next to a disproof. */
  at: number;
  /** The assumptions the plan was built under, from the library. */
  notes: string[];
  /** The `gdsx` call behind it, for the `{ }` button. */
  call: string;
}

export interface ClaimRecord {
  id: string;
  claim: Claim;
  created: number;
  /** Append-only. Nothing is ever removed: a claim disproven at 12:04 stays
   *  disproven at 12:04 even after it is re-verified, which is what makes the
   *  write-up at the end honest. */
  history: VerdictRecord[];
}

/** The current verdict: the last one recorded, or none yet. */
export function latest(record: ClaimRecord): VerdictRecord | null {
  return record.history.length ? record.history[record.history.length - 1] : null;
}

/** Every net a claim is *about*, for the coverage metric and for highlighting. */
export function netsOf(claim: Claim): string[] {
  switch (claim.kind) {
    case "structural":
      return [claim.net];
    case "support":
      return [...claim.leaves];
    case "function":
      return [];
    case "role":
      return [];
    case "invariant":
      return [claim.net];
    case "requirement":
      return [claim.output];
    case "timing":
      return [claim.net];
    case "constraint":
      return [claim.output];
  }
}

/** Every flop a claim is *about*. */
export function flopsOf(claim: Claim): string[] {
  switch (claim.kind) {
    case "support":
    case "function":
      return [claim.flop];
    case "role":
      return [...claim.group];
    case "requirement":
      return [claim.flop];
    case "structural":
    case "invariant":
    case "timing":
    case "constraint":
      return [];
  }
}

/** The claim in one line, as the notebook lists it and the write-up prints it. */
export function claimText(claim: Claim): string {
  switch (claim.kind) {
    case "structural":
      return `${claim.net} is driven by ${claim.cell}`;
    case "support": {
      const set = claim.leaves.length ? `{${claim.leaves.join(", ")}}` : "{}";
      const how = claim.sense === "functional" ? " (functionally)" : "";
      return `D(${claim.flop}) depends exactly on ${set}${how}`;
    }
    case "function":
      return `D(${claim.flop}) == ${claim.expression}`;
    case "role":
      return `${claim.group.join(", ")} is a ${claim.role} of width ${claim.width}`;
    case "invariant":
      return (
        `${claim.net} is ${claim.value} whenever ${claim.condition}` +
        (claim.regime === "sequential" ? " (over time)" : "")
      );
    case "requirement":
      return (
        `${claim.output} == ${claim.value} requires ` +
        `${claim.flop} == ${claim.flop_value}`
      );
    case "timing":
      return (
        `${claim.net} ${EVENT_TEXT[claim.event]} on cycles ` +
        `{${claim.cycles.join(", ")}} under ${claim.stimulus.name}`
      );
    case "constraint":
      return `any key that latches ${claim.output} satisfies ${claim.predicate}`;
  }
}

const EVENT_TEXT: Record<TimingEvent, string> = {
  high: "is high",
  low: "is low",
  rises: "rises",
  falls: "falls",
  "latches-high": "latches high",
};