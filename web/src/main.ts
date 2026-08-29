// Entry point: which of the app's two screens a URL asks for, and nothing
// else.
//
//   ?puzzle=<id>   the workspace, on that puzzle
//   anything else  the level menu
//
// Both screens are behind a dynamic import, and that is load-bearing rather
// than stylistic. Everything the workspace needs is expensive -- Pyodide in a
// worker, a render bundle, a gate tape, dockview, three.js -- and a player who
// asked for the menu should wait for none of it. Importing boot.ts statically
// here would put all of it in the entry chunk and hand the menu the
// workspace's ~30 s boot for a screen that needs one 12 KB JSON file.
//
// The stylesheet is the one thing both screens share, so it is imported here:
// styles/index.css is the single entry to every stylesheet the app has.

import "./styles/index.css";

import { chooseRoute, lastPlayedId, loadCatalog, requestedId } from "./puzzles/catalog";

async function main(): Promise<void> {
  const catalog = await loadCatalog();
  const route = chooseRoute(catalog, { requested: requestedId() });

  if (route.kind === "puzzle") {
    const { bootWorkspace } = await import("./boot");
    await bootWorkspace(catalog, route.puzzle);
    return;
  }

  const { mountMenu } = await import("./menu/menu");
  mountMenu(document.body, { catalog, lastPlayed: lastPlayedId() });
}

main().catch((err) => {
  const message = `ERROR: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
  const workspaceEl = document.getElementById("workspace");
  if (workspaceEl) workspaceEl.textContent = message;
  console.error(err);
});