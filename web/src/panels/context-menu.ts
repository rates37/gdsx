// A right-click menu, anchored to a cursor position.
//
// Modelled on `openLabelEditor` (chips.ts), which already solved every hard
// part of a floating popover here: it lives in `document.body` so a panel's
// `overflow: hidden` cannot clip it, it is nudged back on screen when it
// would overflow, and a module-level singleton means only one is ever open.
//
// The one difference is what it anchors to. The label editor hangs off an
// element it can measure; a menu opened over a canvas has nothing to hang
// off but the pointer, so it takes coordinates. It also closes on wheel and
// scroll, which the label editor does not need: the die view keeps moving
// underneath, and a menu still pointing at where a wire *used to be* is
// worse than no menu.

export interface MenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
}

export type MenuEntry = MenuItem | "separator";

let closeOpen: (() => void) | null = null;

/** Closes whatever menu is open, if any. Safe to call when none is. */
export function closeContextMenu(): void {
  closeOpen?.();
}

/**
 * Opens a menu at viewport coordinates `(x, y)`. Entries with no enabled
 * items are still drawn -- a greyed "Open in Cone Walker" says the action
 * exists and does not apply here, which a missing row does not.
 */
export function openContextMenu(x: number, y: number, entries: MenuEntry[]): void {
  closeContextMenu();
  if (entries.length === 0) return;

  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.setAttribute("role", "menu");

  for (const entry of entries) {
    if (entry === "separator") {
      menu.append(document.createElement("hr"));
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ctx-menu-item";
    button.textContent = entry.label;
    button.disabled = entry.disabled === true;
    button.addEventListener("click", () => {
      // Close first: an item that opens another popover (the label editor)
      // must not have this menu's outside-click handler tear that one down.
      close();
      entry.onSelect();
    });
    menu.append(button);
  }

  document.body.append(menu);

  // Prefer down-and-right of the cursor, the direction a menu is expected to
  // grow; flip back over the pointer rather than off the edge when there is
  // no room.
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const left = x + width + 6 < window.innerWidth ? x : Math.max(6, x - width);
  const top = y + height + 6 < window.innerHeight ? y : Math.max(6, y - height);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;

  function close(): void {
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("contextmenu", onNativeMenu, true);
    window.removeEventListener("wheel", close, true);
    window.removeEventListener("scroll", close, true);
    menu.remove();
    closeOpen = null;
  }
  // Suppress the browser's own menu for as long as ours is up.
  //
  // Windows fires `contextmenu` on pointer *up*; macOS and Linux fire it on
  // pointer *down*. The 3D die view raises this menu from its own pointerup
  // handler (it cannot use `contextmenu` -- on a mousedown-firing platform
  // that arrives before a right-drag can be told from a right-click, and
  // right-drag pans the camera). So on Windows the order is: pointerup ->
  // our menu is created under the cursor -> `contextmenu` fires, and by then
  // the topmost element at those coordinates is *this menu*, not the canvas.
  // The canvas's own preventDefault never sees the event, and the native menu
  // opens on top of ours. Cancelling at the document, in capture, does not
  // care which element the event lands on or which platform's ordering got us
  // here.
  function onNativeMenu(e: Event): void {
    e.preventDefault();
  }
  function onOutside(e: PointerEvent): void {
    if (!menu.contains(e.target as Node)) close();
  }
  function onKey(e: KeyboardEvent): void {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    close();
  }

  document.addEventListener("pointerdown", onOutside, true);
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("contextmenu", onNativeMenu, true);
  window.addEventListener("wheel", close, true);
  window.addEventListener("scroll", close, true);
  closeOpen = close;
}