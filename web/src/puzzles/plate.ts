// The routing mark: a level's generated ornament, from its id alone.
//
// The app paints two screens that want to show "this level" before any of its
// geometry has loaded -- the menu card and the briefing card -- and neither
// has a render bundle to draw from. A hash of the puzzle id sets three track
// pitches, so a level's mark is stable, distinct, and needs nothing added to
// the catalog to author.
//
// It lives here rather than in either screen because it is a fact about a
// puzzle, not about a menu; the gradients it drives are `.gdsx-plate` in
// shell.css for the same reason.

/** A `<span class="gdsx-plate">` whose track pitches are derived from `id`.
 *  Decorative, so it is `aria-hidden`. Callers add their own class for how
 *  the mark is sized and masked in their layout. */
export function plateElement(id: string, extraClass = ""): HTMLElement {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  const node = document.createElement("span");
  node.className = extraClass ? `gdsx-plate ${extraClass}` : "gdsx-plate";
  node.setAttribute("aria-hidden", "true");
  node.style.setProperty("--pitch-a", `${5 + (hash % 4)}px`);
  node.style.setProperty("--pitch-b", `${16 + ((hash >>> 4) % 9)}px`);
  node.style.setProperty("--pitch-c", `${41 + ((hash >>> 9) % 33)}px`);
  return node;
}