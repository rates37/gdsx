// Minimal cross-panel highlight bus. Net highlighting has to work from
// wherever a net is named -- the die view itself, the netlist browser, the
// cone walker -- so the selection lives here rather than inside any one
// panel. Any panel can `set` it; any panel can `subscribe` to react.
//
// Two layers, not one. `set` is the *hover* layer: transient, cleared on
// pointerleave, and the only thing most panels write. `pin` is the *click*
// layer underneath it: it survives the pointer moving away, which is what
// lets you fix on a net in the die view and then go look at it somewhere
// else. Subscribers are handed the effective selection -- hover if there is
// one, the pinned net otherwise -- so a panel that only wants "what should
// glow right now" needs to know nothing about the split.
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
  private hovered: NetSelection | null = null;
  private pinnedNet: NetSelection | null = null;
  private readonly listeners = new Set<Listener>();

  /** What should be glowing: the hovered net, or the pinned one if nothing
   *  is hovered. */
  get(): NetSelection | null {
    return this.hovered ?? this.pinnedNet;
  }

  /** The net the player clicked, if any. For panels that draw a pinned row
   *  differently from a merely hovered one. */
  pinned(): NetSelection | null {
    return this.pinnedNet;
  }

  /** Transient hover. `null` falls back to the pinned net rather than
   *  clearing the highlight outright. */
  set(sel: NetSelection | null): void {
    this.hovered = sel;
    this.publish();
  }

  /** Sticky selection. Survives the pointer leaving the panel it came from. */
  pin(sel: NetSelection | null): void {
    this.pinnedNet = sel;
    this.publish();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Notify only when the *effective* selection moved: a hover that lands on
   *  the already-pinned net, or clearing a hover back to it, changes nothing
   *  anyone can see and must not repaint every panel. */
  private publish(): void {
    const next = this.get();
    if (next?.name === this.lastPublished?.name) return;
    this.lastPublished = next;
    for (const l of this.listeners) l(next);
  }

  private lastPublished: NetSelection | null = null;
}

export const highlightBus = new HighlightBus();