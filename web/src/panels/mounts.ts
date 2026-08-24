// Hosting several former panels inside one panel.
//
// The pattern is the one `die-view-panel.ts` already uses for 2D/3D: a strip
// of buttons at the top, exactly one child mounted at a time, and the other
// disposed rather than hidden. Disposal is the important half -- these
// children hold WebGL contexts, animation loops, worker subscriptions and
// bus listeners, and "hidden but still running" is how a tab you are not
// looking at ends up costing you a core.
//
// Deliberately *not* modelled on `netlist-panel.ts`'s `.tab-btn`s: those
// switch a filter mode over one shared toolbar and one shared data set, which
// is a different thing from mounting and tearing down independent views.
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

    // Each section gets its own element, so a kept-alive one can be hidden
    // without the next section having to clean up after it.
    let entry = live.get(id);
    if (!entry) {
      const element = document.createElement("div");
      element.className = "pt-section";
      host.append(element);
      entry = { element, mounted: tabs.find((t) => t.id === id)!.mount(element) };
      live.set(id, entry);
    }
    entry.element.hidden = false;

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