// The shared "which cycle am I looking at" cursor. Clicking a cycle in the
// Waveform or the Sequence Editor moves it; both panels render a marker at
// its position, so scrubbing one keeps the other oriented.

type Listener = (cycle: number) => void;

class CursorBus {
  private current = 0;

  get(): number {
    return this.current;
  }

  set(cycle: number): void {
    const clamped = Math.max(0, Math.floor(cycle));
    if (clamped === this.current) return;
    this.current = clamped;
    for (const l of this.listeners) l(clamped);
  }

  private readonly listeners = new Set<Listener>();

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

export const cursorBus = new CursorBus();