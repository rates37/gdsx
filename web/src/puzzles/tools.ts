// Which panels a puzzle opens by DEFAULT, derived from its manifest's
// `tools_enabled` (`PuzzleDescriptor.toolsEnabled`).
//
// This lives here, not in workspace.ts: the shell must not know puzzle
// vocabulary, only panel ids. It does not live in main.ts either -- putting
// the mapping table in the entry point would put it out of reach of
// scripts/test-puzzles.mjs, which already imports from src/puzzles/ under
// --experimental-strip-types and is where a bad key should be caught.
//
// THE POLICY IS ADDITIVE, NOT RESTRICTIVE. `tools_enabled` predates most of
// the current panels -- puzzles/original-puzzle/manifest.json declares
// `["cone","guards","registers","fsm","sensitivity"]`, which names no die
// view, no netlist, no waveform and no notebook, and "guards"/"fsm" do not
// name a panel at all (both merged into the register inspector). Treating
// that list as the *complete* set of allowed panels would open the flagship
// puzzle on three tabs with no die view. Instead every puzzle's default
// layout is CORE_PANELS unioned with whatever `tools_enabled` maps to.
//
// Gating only ever changes what opens by default -- see `panelsFor`'s use in
// main.ts, which feeds `Workspace`'s default-layout list. Every panel stays
// registered and stays in the menu bar regardless of `tools_enabled`, so the
// worst case for an ungated tool is "open it from a menu", never "it is
// gone". A saved layout (workspace.ts's `restore`) still wins over this --
// gating only applies to a fresh player or an explicit "reset layout".

import type { PuzzleDescriptor } from "./catalog.ts";
import { PANEL_IDS, type PanelId } from "../panels/ids.ts";

/** `checks.kind === "latch"` is the `sequence` answer kind (catalog.ts's
 *  `LatchCheck`, describeChecks in puzzle-index.mjs) -- the player drives an
 *  input track and the app watches a lock net latch. A puzzle checked this
 *  way has nothing to submit without the Sequence Editor, so it belongs in
 *  the default layout regardless of what `tools_enabled` says. */
function needsSequenceEditor(descriptor: Pick<PuzzleDescriptor, "checks">): boolean {
  return descriptor.checks?.kind === "latch";
}

/** Always open, whatever the manifest says. `notebook` is here even though
 *  most manifests predate it and never name it -- it is the game's only
 *  scored surface, so a puzzle that forgot to list it is an authoring
 *  oversight, not a puzzle that means to hide it. */
export const CORE_PANELS: readonly PanelId[] = ["die-view", "netlist", "waveform", "notebook"];

/**
 * `tools_enabled` key -> panel id. Mostly aliasing: the panel consolidation
 * (docs/game/web-ui-architecture.md §2) merged six former panels into three,
 * and no manifest was rewritten to match, so several keys collapse onto the
 * same panel on purpose.
 */
export const TOOL_PANELS: Record<string, PanelId> = {
  die: "die-view",
  netlist: "netlist",
  labels: "netlist",
  cone: "cone-walker",
  requirements: "cone-walker",
  registers: "register-inspector",
  "register-decoder": "register-inspector",
  sticky: "register-inspector",
  guards: "register-inspector",
  fsm: "register-inspector",
  waveform: "waveform",
  sequence: "sequence-editor",
  experiments: "experiments",
  sensitivity: "experiments",
  constraints: "experiments",
  notebook: "notebook",
  model: "model-builder",
  repl: "repl",
};

const warnedKeys = new Set<string>();

/**
 * The panels a puzzle's default layout should open: `CORE_PANELS` unioned
 * with `descriptor.toolsEnabled` mapped through `TOOL_PANELS`. An empty
 * `toolsEnabled` means "everything" (every registered panel id), not
 * "nothing" -- it is what a puzzle with no `tools_enabled` field at all
 * decodes to (catalog.ts's `FALLBACK`), and a missing field authoring an
 * empty toolset would be a strange way to gate a puzzle down to CORE_PANELS.
 * A key `TOOL_PANELS` does not recognise is ignored, with a console.warn the
 * first time that key is seen.
 *
 * `checks.kind === "latch"` (the `sequence` answer kind) additionally forces
 * `sequence-editor` into the set, after the `tools_enabled` union so the
 * result never depends on where in that list a "sequence" key would have
 * sorted -- see `needsSequenceEditor`.
 */
export function panelsFor(
  descriptor: Pick<PuzzleDescriptor, "toolsEnabled" | "checks">,
): PanelId[] {
  if (descriptor.toolsEnabled.length === 0) return [...PANEL_IDS];

  const panels = new Set<PanelId>(CORE_PANELS);
  for (const key of descriptor.toolsEnabled) {
    const id = TOOL_PANELS[key];
    if (id) {
      panels.add(id);
    } else if (!warnedKeys.has(key)) {
      warnedKeys.add(key);
      console.warn(`gdsx: tools_enabled key "${key}" names no panel -- ignored`);
    }
  }
  if (needsSequenceEditor(descriptor)) panels.add("sequence-editor");
  return [...panels];
}