// Where a net (or instance) gets *opened*, as opposed to merely hovered.
// `highlightBus` (src/store/highlight.ts) is transient -- it glows and
// forgets on pointerleave. This is the click-driven counterpart: "inspect
// this in the Cone Walker", which should survive the pointer moving away.

type Listener = (net: string) => void;

class ConeRootBus {
  private readonly listeners = new Set<Listener>();

  open(net: string): void {
    for (const l of this.listeners) l(net);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

export const coneRootBus = new ConeRootBus();