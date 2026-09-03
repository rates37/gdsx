// A modal confirmation for the two destructive actions on the menu.
//
// Not `window.confirm`: the requirement on both of them is that the dialog
// SAYS WHAT WILL BE DELETED, and a native confirm gives one unstyled line
// with no room to name the puzzle, list what a clear takes with it, or show
// how much of it there is. It is also suppressible per-origin in some
// browsers, which is the wrong failure mode for the last question asked
// before hours of a player's notebook go away.
//
// The safe answer is the default: the cancel button takes focus, Escape and a
// click outside both cancel, and the destructive button is the one that has to
// be aimed at.

export interface ConfirmOptions {
  title: string;
  /** Paragraphs, in order. Say what goes, not "are you sure?". */
  body: string[];
  /** The destructive button's label -- name the action, not "OK". */
  confirmLabel: string;
}

export function confirmDestructive(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "menu-modal";

    const box = document.createElement("div");
    box.className = "menu-modal-box";
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-modal", "true");

    const title = document.createElement("h2");
    title.className = "menu-modal-title";
    title.textContent = opts.title;
    box.append(title);

    for (const paragraph of opts.body) {
      const p = document.createElement("p");
      p.className = "menu-modal-text";
      p.textContent = paragraph;
      box.append(p);
    }

    const actions = document.createElement("div");
    actions.className = "menu-modal-actions";

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "menu-modal-cancel";
    cancel.textContent = "cancel";

    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "menu-modal-confirm";
    confirm.textContent = opts.confirmLabel;

    actions.append(cancel, confirm);
    box.append(actions);
    overlay.append(box);
    document.body.append(overlay);

    const close = (answer: boolean): void => {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(answer);
    };
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") close(false);
    }

    cancel.addEventListener("click", () => close(false));
    confirm.addEventListener("click", () => close(true));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
    });
    document.addEventListener("keydown", onKey);
    cancel.focus();
  });
}