// Evidence: what the Experiment Runner puts in the notebook -- results are
// tabular and exportable to the notebook as evidence.
//
// Evidence is not a claim, and the distinction is the same one `LIKELY` and
// `PROVEN` draw. A claim is an assertion the game checks and scores; a piece of
// evidence is a **measurement**, and measuring something is not asserting
// anything about it. So:
//
//   * evidence scores nothing. Reading a sweep off the design is work, but the
//     points are in what you conclude from it, which is a claim;
//   * evidence has no verdict. It has a result and the conditions it was taken
//     under, and the panel prints both;
//   * evidence is append-only, like verdicts, because the write-up at the end
//     is an account of an investigation and an experiment you ran and then
//     thought better of is part of that account.
//
// What it carries is deliberately a summary rather than the whole matrix: the
// matrix of a 121-cycle sweep over 92 flops is 11k cells, it belongs in the
// panel that produced it, and a notebook that stored every one of them would be
// unreadable and would blow the localStorage quota by mid-session.

const VERSION = 1;

type Listener = () => void;

/** One measurement, as the notebook keeps it. */
export interface EvidenceRecord {
  id: string;
  /** The recipe's own name: "single-pulse sweep", "gap sweep", … */
  recipe: string;
  /** One line: what was run. "121 runs · 92 watched · baseline: the current sequence". */
  summary: string;
  /** The conditions. A sweep's numbers mean nothing without the baseline it
   *  was measured against, so this is never optional. */
  baseline: string;
  /** The findings worth keeping: the marked rows, per element, already
   *  summarised by the panel. Rendered as a short list. */
  lines: string[];
  /** The self-checks that fired, kept verbatim -- a sweep with a warning is
   *  still evidence, and hiding the warning would make it dishonest evidence. */
  warnings: string[];
  /** The `gdsx` call that produced it, so the write-up is reproducible. */
  call: string;
  at: number;
}

interface Saved {
  version: number;
  evidence: EvidenceRecord[];
}

export class EvidenceLog {
  private readonly key: string;
  private records: EvidenceRecord[] = [];
  private readonly listeners = new Set<Listener>();
  private counter = 0;

  /** One log per puzzle, for the same reason the notebook has one per puzzle. */
  constructor(puzzleId: string) {
    this.key = `gdsx.evidence.${puzzleId}.v${VERSION}`;
    this.restore();
  }

  all(): readonly EvidenceRecord[] {
    return this.records;
  }

  add(entry: Omit<EvidenceRecord, "id" | "at">): EvidenceRecord {
    const record: EvidenceRecord = {
      ...entry,
      id: `e${Date.now().toString(36)}-${this.counter++}`,
      at: Date.now(),
    };
    this.records = [...this.records, record];
    this.save();
    return record;
  }

  /** The player's explicit choice, as with a claim they never meant to make. */
  remove(id: string): void {
    this.records = this.records.filter((r) => r.id !== id);
    this.save();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private save(): void {
    try {
      localStorage.setItem(
        this.key,
        JSON.stringify({ version: VERSION, evidence: this.records } satisfies Saved),
      );
    } catch (err) {
      console.warn("gdsx: could not save the evidence log", err);
    }
    for (const listener of this.listeners) listener();
  }

  private restore(): void {
    const raw = localStorage.getItem(this.key);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as Saved;
      if (saved.version !== VERSION || !Array.isArray(saved.evidence)) return;
      this.records = saved.evidence;
      this.counter = saved.evidence.length;
    } catch (err) {
      console.warn("gdsx: discarding an unreadable evidence log", err);
      localStorage.removeItem(this.key);
    }
  }
}

/** One log per puzzle, shared by the panel that writes it and the one that
 *  shows it -- the Experiment Runner and the Notebook are different panels
 *  looking at the same log, not two copies of it. */
const logs = new Map<string, EvidenceLog>();

export function evidenceLog(puzzleId: string): EvidenceLog {
  let log = logs.get(puzzleId);
  if (!log) {
    log = new EvidenceLog(puzzleId);
    logs.set(puzzleId, log);
  }
  return log;
}