// Player-authored names for nets and instances.
//
// An extracted netlist names things after the geometry it found them in:
// `n2951`, `dfrtp_2_50`. That is the honest name and the game never throws it
// away -- it is what the Python API, the gate tape and the write-up all speak.
// But "n2951" is not what the net *is*, and the moment a player works out that
// it is the compare result they want to call it `match`. Recording that is a
// real part of reverse engineering: the glossary you build as you go is half
// the deliverable.
//
// So a label is an *alias*, never a rename. Nothing downstream is renamed:
// lookups, claims, cone walks and the tape all still key on the raw name, and
// every labelled thing keeps its raw name one hover away. This store is the
// single place that mapping lives.
//
// Nets and instances are separate namespaces -- a design may legitimately have
// an instance and a net of the same name, and labelling one must not label the
// other.

export type LabelKind = "net" | "instance";

export interface LabelEntry {
  kind: LabelKind;
  /** The raw, extracted name. Never changes. */
  name: string;
  label: string;
}

const VERSION = 1;

type Listener = () => void;

function keyOf(kind: LabelKind, name: string): string {
  return `${kind}:${name}`;
}

interface Saved {
  version: number;
  labels: LabelEntry[];
}

export class LabelStore {
  private storageKey: string | null = null;
  private entries = new Map<string, LabelEntry>();
  private readonly listeners = new Set<Listener>();

  /**
   * Binds the store to a puzzle and loads that puzzle's glossary. Called once
   * at startup; a label for `n96` in Two Stars means nothing in another
   * design, so the key is per puzzle, as the notebook's is.
   *
   * Until this is called the store works in memory and persists nothing,
   * which is what keeps importing a panel outside the app harmless.
   */
  open(puzzleId: string): void {
    this.storageKey = `gdsx.labels.${puzzleId}.v${VERSION}`;
    this.entries = new Map();
    this.restore();
    this.emit();
  }

  /** The label for a thing, or null if the player has not named it. */
  get(kind: LabelKind, name: string): string | null {
    return this.entries.get(keyOf(kind, name))?.label ?? null;
  }

  /** What to show: the label if there is one, otherwise the raw name. Every
   *  caller of this must keep the raw name reachable -- a tooltip, an adjacent
   *  dim span -- so a label never hides what the thing actually is. */
  display(kind: LabelKind, name: string): string {
    return this.get(kind, name) ?? name;
  }

  /** True if `name` carries a label, i.e. `display` is showing an alias. */
  has(kind: LabelKind, name: string): boolean {
    return this.entries.has(keyOf(kind, name));
  }

  /** Sets or, given blank/whitespace, clears a label. Returns the stored
   *  label, or null if it was cleared. */
  set(kind: LabelKind, name: string, label: string): string | null {
    const trimmed = label.trim();
    if (!trimmed) {
      this.clear(kind, name);
      return null;
    }
    this.entries.set(keyOf(kind, name), { kind, name, label: trimmed });
    this.save();
    return trimmed;
  }

  clear(kind: LabelKind, name: string): void {
    if (this.entries.delete(keyOf(kind, name))) this.save();
  }

  /** Every label, sorted by the label itself -- a glossary reads
   *  alphabetically by the name the player chose, not by `n2951`. */
  all(): LabelEntry[] {
    return [...this.entries.values()].sort(
      (a, b) => a.label.localeCompare(b.label) || a.name.localeCompare(b.name),
    );
  }

  get size(): number {
    return this.entries.size;
  }

  /**
   * The reverse lookup: a raw name for something the player typed. Tries the
   * text as a raw name first, so a design that happens to contain a net
   * literally called `match` is never shadowed by a label of the same text,
   * then as a label (case-insensitively, since the player typed it).
   *
   * Returns null when the text matches nothing -- the caller decides whether
   * that is an error or just an unrecognised net.
   */
  resolve(kind: LabelKind, text: string, isRawName?: (name: string) => boolean): string | null {
    const query = text.trim();
    if (!query) return null;
    if (isRawName?.(query)) return query;
    const lower = query.toLowerCase();
    for (const entry of this.entries.values()) {
      if (entry.kind === kind && entry.label.toLowerCase() === lower) return entry.name;
    }
    return isRawName ? null : query;
  }

  /** True if `query` (already lower-cased) appears in this thing's label --
   *  what lets a filter box find `n2951` by typing "match". */
  matches(kind: LabelKind, name: string, lowerQuery: string): boolean {
    const label = this.get(kind, name);
    return label !== null && label.toLowerCase().includes(lowerQuery);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  private save(): void {
    if (this.storageKey) {
      const payload: Saved = { version: VERSION, labels: [...this.entries.values()] };
      try {
        localStorage.setItem(this.storageKey, JSON.stringify(payload));
      } catch (err) {
        // Storage full or disabled: the glossary still works this session.
        // Same call as the notebook makes -- a warning, not a modal.
        console.warn("gdsx: could not save net labels", err);
      }
    }
    this.emit();
  }

  private restore(): void {
    if (!this.storageKey) return;
    const raw = localStorage.getItem(this.storageKey);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as Saved;
      if (saved.version !== VERSION || !Array.isArray(saved.labels)) return;
      for (const entry of saved.labels) {
        if (!entry?.name || !entry?.label) continue;
        const kind: LabelKind = entry.kind === "instance" ? "instance" : "net";
        this.entries.set(keyOf(kind, entry.name), { kind, name: entry.name, label: entry.label });
      }
    } catch (err) {
      console.warn("gdsx: discarding unreadable net labels", err);
      localStorage.removeItem(this.storageKey);
    }
  }
}

/** One glossary per page, like `highlightBus` and `coneRootBus`: labels are
 *  cross-panel by nature, and switching puzzles is a navigation, so there is
 *  never more than one design in play at a time. */
export const labels = new LabelStore();