// A slim strip above the dockview grid: reset the layout back to its
// default arrangement, and reopen a tab that was closed. dockview's own tab
// close button (×) has no undo, and the default layout is otherwise only
// reachable by clearing localStorage by hand -- both are one click here.

import type { Workspace } from "./workspace.ts";

export function attachToolbar(host: HTMLElement, workspace: Workspace): void {
  const bar = document.createElement("div");
  bar.className = "gdsx-toolbar";
  bar.innerHTML = `
    <span class="gdsx-toolbar-title">DIESHARK</span>
    <div class="gdsx-toolbar-spacer"></div>
    <div class="gdsx-reopen-wrap">
      <button class="gdsx-reopen-btn" type="button">reopen tab ▾</button>
      <div class="gdsx-reopen-menu" hidden></div>
    </div>
    <button class="gdsx-reset-btn" type="button" title="restore the default panel arrangement">
      reset layout
    </button>
  `;
  host.prepend(bar);

  const reopenWrap = bar.querySelector(".gdsx-reopen-wrap") as HTMLDivElement;
  const reopenBtn = bar.querySelector(".gdsx-reopen-btn") as HTMLButtonElement;
  const reopenMenu = bar.querySelector(".gdsx-reopen-menu") as HTMLDivElement;
  const resetBtn = bar.querySelector(".gdsx-reset-btn") as HTMLButtonElement;

  function closeMenu(): void {
    reopenMenu.hidden = true;
  }

  function renderMenu(): void {
    const closed = workspace.closedPanels();
    reopenMenu.replaceChildren();
    for (const p of closed) {
      const item = document.createElement("div");
      item.className = "gdsx-reopen-item";
      item.textContent = p.title;
      item.addEventListener("click", () => {
        workspace.reopen(p.id);
        closeMenu();
      });
      reopenMenu.append(item);
    }
  }

  function refresh(): void {
    const count = workspace.closedPanels().length;
    reopenBtn.disabled = count === 0;
    reopenBtn.textContent = count > 0 ? `reopen tab (${count}) ▾` : "reopen tab ▾";
    if (!reopenMenu.hidden) renderMenu();
  }

  reopenBtn.addEventListener("click", () => {
    if (reopenBtn.disabled) return;
    if (reopenMenu.hidden) renderMenu();
    reopenMenu.hidden = !reopenMenu.hidden;
  });
  document.addEventListener("click", (e) => {
    if (!reopenWrap.contains(e.target as Node)) closeMenu();
  });
  resetBtn.addEventListener("click", () => {
    closeMenu();
    workspace.resetLayout();
  });

  workspace.onChange(refresh);
  refresh();
}