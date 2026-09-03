// The notebook itself: the claims a player has made and what became of them.
//
// One rule shapes the whole file. **Nothing is ever removed.** A claim disproven
// at 12:04 stays disproven at 12:04 after it is re-verified, after the sequence
// changes, after the player decides they were right all along. Verdicts are an
// append-only list per claim, and the notebook shows the latest while keeping
// the rest -- which is what makes the write-up generated from this at the end
// an account of an investigation rather than a tidied-up summary of one.
//
// Persistence follows the pattern in web/src/workspace/workspace.ts: best
// effort, and unreadable saved state is discarded with a console warning rather
// than breaking the app. Losing a notebook is bad; refusing to start is worse.

import type { Claim, ClaimRecord, VerdictRecord } from "./model.ts";
import { latest } from "./model.ts";

const VERSION = 1;

type Listener = () => void;

interface Saved {
  version: number;
  claims: ClaimRecord[];
}

export class Notebook {
  private readonly key: string;
  private records: ClaimRecord[] = [];
  private readonly listeners = new Set<Listener>();
  private counter = 0;

  /** One notebook per puzzle: claims about Two Stars are not claims about
   *  anything else, and a shared key would silently merge two investigations. */
  constructor(puzzleId: string) {
    this.key = `gdsx.notebook.${puzzleId}.v${VERSION}`;
    this.restore();
  }

  all(): readonly ClaimRecord[] {
    return this.records;
  }

  get(id: string): ClaimRecord | undefined {
    return this.records.find((r) => r.id === id);
  }

  /** Record a claim, unverified. Verifying it is a separate, later step, so a
   *  claim you have not settled yet is still a claim you wrote down. */
  add(claim: Claim): ClaimRecord {
    const record: ClaimRecord = {
      id: `c${Date.now().toString(36)}-${this.counter++}`,
      claim,
      created: Date.now(),
      history: [],
    };
    this.records = [...this.records, record];
    this.save();
    return record;
  }

  /** Append a verdict. Never replaces the previous one -- see the header. */
  record(id: string, verdict: VerdictRecord): void {
    const record = this.get(id);
    if (!record) return;
    record.history = [...record.history, verdict];
    this.save();
  }

  /** The only destructive operation, and it is the player's explicit choice:
   *  a claim they decide they never meant to make. A claim that turned out to
   *  be false is removed with this at the cost of the points it scored. */
  remove(id: string): void {
    this.records = this.records.filter((r) => r.id !== id);
    this.save();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private save(): void {
    const payload: Saved = { version: VERSION, claims: this.records };
    try {
      localStorage.setItem(this.key, JSON.stringify(payload));
    } catch (err) {
      // Storage full or disabled. The notebook still works for this session;
      // it just will not be there next time, which is not worth a modal.
      console.warn("gdsx: could not save the notebook", err);
    }
    for (const listener of this.listeners) listener();
  }

  private restore(): void {
    const raw = localStorage.getItem(this.key);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as Saved;
      if (saved.version !== VERSION || !Array.isArray(saved.claims)) return;
      this.records = saved.claims;
      this.counter = saved.claims.length;
    } catch (err) {
      console.warn("gdsx: discarding an unreadable notebook", err);
      localStorage.removeItem(this.key);
    }
  }
}

/**
 * The one notebook for a puzzle.
 *
 * Three panels write claims -- the Notebook itself, the Cone Walker's
 * requirement pins, the Registers panel's `pin as role` -- and each used to
 * construct its own `Notebook`. Every instance reads and writes the same
 * localStorage key, so nothing was *lost*, but each held its own in-memory
 * `records` array and its own listener set: pinning a claim from one panel
 * wrote it to storage and the Notebook panel, holding a different instance,
 * never heard about it. The claim appeared to vanish until a reload.
 *
 * Keyed by puzzle rather than a bare singleton because `Notebook` is
 * constructed with a puzzle id and the class stays independently usable (the
 * write-up and verification tests build their own).
 */
const shared = new Map<string, Notebook>();

export function notebookFor(puzzleId: string): Notebook {
  let notebook = shared.get(puzzleId);
  if (!notebook) {
    notebook = new Notebook(puzzleId);
    shared.set(puzzleId, notebook);
  }
  return notebook;
}

/** Every claim whose current verdict is a proof. */
export function proven(notebook: Notebook): ClaimRecord[] {
  return notebook.all().filter((r) => latest(r)?.verdict.kind === "PROVEN");
}