// What the Model Builder remembers: the player's source, and every run of it
// against the design.
//
// The badge (game-plan.md §6: "awards a badge at 100% over N vectors — model
// validated — which is worth more points than solving") is the highest-scoring
// thing in the game, so what it says has to be exact:
//
//   * it is awarded for **agreement over N vectors**, and its label always
//     carries the N. It is the same kind of statement as `LIKELY`, not the same
//     kind as `PROVEN`: 500 vectors that agreed is not every stimulus, and the
//     word "proven" appears nowhere near it.
//   * it is **never silently kept**. Runs are append-only, and if a later run
//     finds a divergence the panel says the model no longer agrees while
//     keeping the earlier record — the same rule the notebook applies to a
//     claim that was proven once and fell over later.
//
// Persistence is best-effort, as everywhere else: losing a model is bad,
// refusing to start is worse.

import type { DiffReport } from "./diff.ts";
import type { Language } from "./host.ts";

const VERSION = 1;

/** How many vectors a run must agree on, in full, to earn the badge. Tunable
 *  here and nowhere else; every label quotes the number rather than implying a
 *  proof. */
export const VALIDATION_VECTORS = 200;

export interface RunRecord {
  at: number;
  language: Language;
  vectors: number;
  agreed: number;
  seed: number;
  /** True when this run agreed on every vector, ran at least
   *  `VALIDATION_VECTORS` of them, AND actually asked the model something.
   *
   *  All three matter. 100% of 5 vectors is not a validated model. Neither is
   *  100% over an observable the design held at 0 the whole time: a model that
   *  answers 0 unconditionally agrees perfectly with a `success` that never
   *  latched, and awarding the game's highest-scoring badge for that would make
   *  the badge worthless. See `DiffReport.constant`. */
  validated: boolean;
  /** The observables that were compared, so a run over `success` alone is not
   *  mistaken later for a run over the whole design. */
  watch: string[];
  /** Observables the design held at one value for the whole run -- the part of
   *  the comparison that tested nothing. */
  constant: string[];
}

interface Saved {
  version: number;
  language: Language;
  source: string;
  runs: RunRecord[];
}

type Listener = () => void;

export class ModelStore {
  private readonly key: string;
  private data: Saved;
  private readonly listeners = new Set<Listener>();

  constructor(puzzleId: string, starter: string, language: Language = "javascript") {
    this.key = `gdsx.model.${puzzleId}.v${VERSION}`;
    this.data = { version: VERSION, language, source: starter, runs: [] };
    this.restore();
  }

  get source(): string {
    return this.data.source;
  }

  get language(): Language {
    return this.data.language;
  }

  runs(): readonly RunRecord[] {
    return this.data.runs;
  }

  setSource(source: string): void {
    this.data.source = source;
    this.save();
  }

  setLanguage(language: Language, starter: string): void {
    // Switching language replaces the source only when nothing has been written
    // over the starter: silently discarding a model someone typed would be the
    // worst bug in this panel.
    if (this.data.source.trim() === "") this.data.source = starter;
    this.data.language = language;
    this.save();
  }

  record(report: DiffReport, language: Language, seed: number): RunRecord {
    const run: RunRecord = {
      at: report.at,
      language,
      vectors: report.vectors,
      agreed: report.agreed,
      seed,
      validated:
        report.agreed === report.vectors &&
        report.vectors >= VALIDATION_VECTORS &&
        report.constant.length < report.watch.length,
      watch: [...report.watch],
      constant: [...report.constant],
    };
    this.data.runs = [...this.data.runs, run];
    this.save();
    return run;
  }

  /** The run that earned the badge, if any -- the first validated one. */
  badge(): RunRecord | null {
    return this.data.runs.find((r) => r.validated) ?? null;
  }

  latest(): RunRecord | null {
    return this.data.runs.length ? this.data.runs[this.data.runs.length - 1] : null;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private save(): void {
    try {
      localStorage.setItem(this.key, JSON.stringify(this.data));
    } catch (err) {
      console.warn("gdsx: could not save the model", err);
    }
    for (const listener of this.listeners) listener();
  }

  private restore(): void {
    const raw = localStorage.getItem(this.key);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as Saved;
      if (saved.version !== VERSION || typeof saved.source !== "string") return;
      this.data = { ...saved, runs: Array.isArray(saved.runs) ? saved.runs : [] };
    } catch (err) {
      console.warn("gdsx: discarding an unreadable saved model", err);
      localStorage.removeItem(this.key);
    }
  }
}

export interface BadgeView {
  tone: "green" | "amber" | "red" | "grey";
  label: string;
  /** The second line: what the current state of play actually is. */
  detail: string;
}

/**
 * How the badge reads right now.
 *
 * The four states are separate on purpose, and the amber one is the one that
 * would be easiest to fudge: a model that WAS validated and has since diverged
 * is not a validated model, and it is not an unvalidated one either. It gets
 * its own line saying both things.
 */
export function badgeView(store: ModelStore): BadgeView {
  const badge = store.badge();
  const latest = store.latest();

  if (!latest) {
    return {
      tone: "grey",
      label: "not run",
      detail: `agree with the design on ${VALIDATION_VECTORS} vectors to validate the model`,
    };
  }
  if (badge && latest.validated) {
    return {
      tone: "green",
      label: `model validated · ${badge.vectors} vectors`,
      detail:
        `agreed on every one of ${latest.vectors} generated vectors over ` +
        `${latest.watch.join(", ")}. That is agreement, not a proof: the key ` +
        `space is larger than any vector set.`,
    };
  }
  if (badge) {
    return {
      tone: "amber",
      label: `badge earned, model since diverged`,
      detail:
        `validated at ${new Date(badge.at).toLocaleTimeString()} over ${badge.vectors} ` +
        `vectors; the latest run agreed on ${latest.agreed} of ${latest.vectors}`,
    };
  }
  if (latest.agreed !== latest.vectors) {
    return {
      tone: "red",
      label: `${latest.agreed} / ${latest.vectors} vectors agree`,
      detail: "the first divergence is below; load it into the waveform and look",
    };
  }
  // Agreement about a signal that never moved is agreement about nothing, and
  // it is reported as such rather than as progress towards the badge.
  const vacuous = (latest.constant ?? []).length >= latest.watch.length;
  if (vacuous) {
    return {
      tone: "amber",
      label: "nothing was tested",
      detail:
        `the design held ${latest.watch.join(", ")} at one value for all ` +
        `${latest.vectors} vectors, so agreeing with it proves nothing — ` +
        `generate keys that actually move it, or watch something that moves`,
    };
  }
  return {
    tone: "amber",
    label: `agreed on all ${latest.vectors} — ${VALIDATION_VECTORS} needed for the badge`,
    detail: "run more vectors to earn the badge",
  };
}