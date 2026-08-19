// A queue of constraint-row candidates a player has measured elsewhere in the
// app -- today, just the Sensitivity panel's "this element reacts to these
// cycles" rows -- for the Constraints panel (§4.13) to turn into rows of a
// `System`. Modelled on `highlightBus`/`coneRootBus`: a plain pub/sub
// singleton, no framework, so the two panels do not need to know about each
// other directly.

export interface ConstraintCandidate {
  /** What this candidate is called, pre-filled from where it came from --
   *  the player can rename it before it becomes a constraint row. */
  name: string;
  /** Candidate cycles this element was seen to react to. */
  elements: number[];
  source: string;
}

type Listener = (items: readonly ConstraintCandidate[]) => void;

class ConstraintsInbox {
  private items: ConstraintCandidate[] = [];
  private readonly listeners = new Set<Listener>();

  push(item: ConstraintCandidate): void {
    this.items = [...this.items, item];
    for (const l of this.listeners) l(this.items);
  }

  all(): readonly ConstraintCandidate[] {
    return this.items;
  }

  clear(): void {
    this.items = [];
    for (const l of this.listeners) l(this.items);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

export const constraintsInbox = new ConstraintsInbox();