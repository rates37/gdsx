// Minimal cross-panel highlight bus. Net highlighting has to work from
// wherever a net is named -- the die view itself, the netlist browser, the
// cone walker -- so the selection lives here rather than inside any one
// panel. Any panel can `set` it; any panel can `subscribe` to react.
//
// Carries only the net NAME, deliberately. The die view's shader keys
// highlighting on a numeric id from `render.bin`'s own net numbering, which
// is a different id space from anything the Python API hands out -- only
// `DieView` (via `netIdOf`) knows how to resolve a name into that space, so
// resolution happens there, not here. A panel that doesn't have a bundle id
// handy (the netlist browser, the cone walker) must not have to fake one.

export interface NetSelection {
  name: string;
}

type Listener = (sel: NetSelection | null) => void;

class HighlightBus {
  private current: NetSelection | null = null;
  private readonly listeners = new Set<Listener>();

  get(): NetSelection | null {
    return this.current;
  }

  set(sel: NetSelection | null): void {
    if (sel?.name === this.current?.name) return;
    this.current = sel;
    for (const l of this.listeners) l(sel);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}

export const highlightBus = new HighlightBus();