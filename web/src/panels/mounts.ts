// Hosting several former panels inside one panel.
//
// The pattern is the one `die-view-panel.ts` already uses for 2D/3D: a strip
// of buttons at the top, exactly one child mounted at a time, and the other
// disposed rather than hidden. Disposal is the important half -- these
// children hold WebGL contexts, animation loops, worker subscriptions and
// bus listeners, and "hidden but still running" is how a tab you are not
// looking at ends up costing you a core.
//
// `subTabHost` nests: a section's `mount` can itself call `subTabHost` on the
// element it is given, as long as it passes a `panelId` distinct from its
// parent's (storage keys and route-bus addressing are both keyed on that
// string). The Netlist panel's Instances/Nets strip is the example -- it
// lives inside the "browser" section of the Browser/Labels strip.
//
// A child is a `Mount`: the body of what used to be `PanelDef.render`, taking
// the element it should fill and returning something disposable. Converting a
// panel into a section is therefore a signature change and an indentation
// change, not a rewrite.

import { panelRouteBus } from "../store/panel-route";

export interface Mounted {
  dispose?(): void;
}

export type Mount = (host: HTMLElement) => Mounted;

export interface SubTab {
  /** Stable id: what the guide and the menu bar route to, and what the
   *  remembered-tab preference is stored as. */
  id: string;
  title: string;
  mount: Mount;
  /**
   * Keep this section mounted when the player switches away, hiding it
   * instead of tearing it down.
   *
   * The default is to dispose, because that is what makes a section that owns
   * a GL context or an animation loop cost nothing while it is off screen.
   * But disposal also throws away everything the section had *fetched* and
   * everything the player had *done* in it -- the netlist browser re-runs two
   * Pyodide round-trips over 728 instances and loses your selection, scroll
   * and filter -- so a section whose state is expensive to rebuild and cheap
   * to hold should opt in here.
   */
  keepAlive?: boolean;
  /**
   * Called every time this tab becomes the visible one -- on first
   * activation (right after `mount`) and on every later flip back to it,
   * including when `keepAlive` meant there was nothing to (re)mount.
   *
   * For a section that owns real, independent content, `mount` is enough.
   * This exists for the host's *shared* chrome -- a filter box, a count, a
   * "last call" readout -- that lives outside any one section's element but
   * still needs to know which section is now current.
   */
  onShow?: () => void;
}

export interface Drawer extends Mounted {
  /** Text beside the title, e.g. a pending count. `null` clears it. */
  setBadge(text: string | null): void;
}

/**
 * A collapsible region at the foot of a panel, for a secondary tool that
 * belongs *next to* the primary one rather than in a tab of its own.
 *
 * The child is mounted on first expand, not on creation, and that is the
 * point rather than an optimisation: it lets a panel that is deliberately
 * free of some dependency host a section that needs it, without taking the
 * dependency itself. The Experiments panel runs on the gate tape and is live
 * before Pyodide has booted; the Constraints drawer inside it needs Python,
 * and because it does not mount until the player opens it, the sweep never
 * waits on the analysis engine.
 */
export function drawer(
  container: HTMLElement,
  opts: { id: string; title: string; mount: Mount },
): Drawer {
  const storageKey = `gdsx.drawer.${opts.id}`;
  const root = document.createElement("div");
  root.className = "pd-drawer";
  root.innerHTML = `
    <button class="pd-drawer-head" type="button" aria-expanded="false">
      <span class="pd-drawer-caret">▸</span>
      <span class="pd-drawer-title"></span>
      <span class="pd-drawer-badge"></span>
    </button>
    <div class="pd-drawer-body" hidden></div>`;
  (root.querySelector(".pd-drawer-title") as HTMLElement).textContent = opts.title;
  const head = root.querySelector(".pd-drawer-head") as HTMLButtonElement;
  const caret = root.querySelector(".pd-drawer-caret") as HTMLElement;
  const badge = root.querySelector(".pd-drawer-badge") as HTMLElement;
  const body = root.querySelector(".pd-drawer-body") as HTMLElement;
  container.append(root);

  let open = false;
  let mounted: Mounted | null = null;

  function setOpen(next: boolean, remember = false): void {
    open = next;
    body.hidden = !open;
    caret.textContent = open ? "▾" : "▸";
    head.setAttribute("aria-expanded", String(open));
    root.classList.toggle("on", open);
    if (open && !mounted) mounted = opts.mount(body);
    if (remember) {
      try {
        localStorage.setItem(storageKey, open ? "1" : "0");
      } catch {
        // Storage disabled: the drawer works, it just opens closed next time.
      }
    }
  }

  head.addEventListener("click", () => setOpen(!open, true));

  let initial = false;
  try {
    initial = localStorage.getItem(storageKey) === "1";
  } catch {
    initial = false;
  }
  if (initial) setOpen(true);

  return {
    setBadge(text) {
      badge.textContent = text ?? "";
      badge.hidden = text === null;
    },
    dispose() {
      mounted?.dispose?.();
      mounted = null;
    },
  };
}

/**
 * Renders a sub-tab strip into `container` and mounts one child beneath it.
 *
 * @param panelId The *panel's* id, not the section's -- this is what
 *   `panelRouteBus` addresses and what the remembered preference is keyed on.
 */
export function subTabHost(
  container: HTMLElement,
  panelId: string,
  tabs: SubTab[],
): Mounted {
  if (tabs.length === 0) return {};

  const storageKey = `gdsx.subtab.${panelId}`;
  container.classList.add("pt-panel");
  container.innerHTML = `<div class="pt-tabs"></div><div class="pt-host"></div>`;
  const strip = container.querySelector(".pt-tabs") as HTMLDivElement;
  const host = container.querySelector(".pt-host") as HTMLDivElement;

  const buttons = new Map<string, HTMLButtonElement>();
  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pt-tab";
    button.dataset.subtab = tab.id;
    button.textContent = tab.title;
    button.addEventListener("click", () => show(tab.id, true));
    buttons.set(tab.id, button);
    strip.append(button);
  }

  interface Live {
    element: HTMLElement;
    mounted: Mounted;
  }
  const live = new Map<string, Live>();
  let current: string | null = null;

  function remembered(): string {
    // An explicit route wins over the remembered preference: something asked
    // for this section by name, and that is a stronger signal than where the
    // player happened to be last time.
    const routed = panelRouteBus.latest(panelId)?.section;
    if (routed && buttons.has(routed)) return routed;
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved && buttons.has(saved)) return saved;
    } catch {
      // Storage disabled: fall through to the first tab. The host still works,
      // it just does not remember.
    }
    return tabs[0].id;
  }

  function show(id: string, remember = false): void {
    if (id === current || !buttons.has(id)) return;

    // Retire whatever was showing: hidden if it asked to be kept, torn down
    // and removed otherwise.
    if (current !== null) {
      const previous = tabs.find((t) => t.id === current)!;
      const held = live.get(current);
      if (held) {
        if (previous.keepAlive) {
          held.element.hidden = true;
        } else {
          held.mounted.dispose?.();
          held.element.remove();
          live.delete(current);
        }
      }
    }

    current = id;
    for (const [tabId, button] of buttons) button.classList.toggle("on", tabId === id);

    const tabDef = tabs.find((t) => t.id === id)!;

    // Each section gets its own element, so a kept-alive one can be hidden
    // without the next section having to clean up after it.
    let entry = live.get(id);
    if (!entry) {
      const element = document.createElement("div");
      element.className = "pt-section";
      host.append(element);
      entry = { element, mounted: tabDef.mount(element) };
      live.set(id, entry);
    }
    entry.element.hidden = false;
    tabDef.onShow?.();

    if (remember) {
      try {
        localStorage.setItem(storageKey, id);
      } catch {
        // As above.
      }
    }
  }

  const unsubscribe = panelRouteBus.subscribe((route) => {
    if (route.panel === panelId && route.section) show(route.section);
  });

  show(remembered());

  return {
    dispose() {
      unsubscribe();
      // Every section that is still around, not just the visible one: a
      // kept-alive section is still holding its subscriptions.
      for (const entry of live.values()) entry.mounted.dispose?.();
      live.clear();
      current = null;
    },
  };
}