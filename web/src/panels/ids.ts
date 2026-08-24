// The panel id vocabulary, in one place.
//
// Panel ids are load-bearing in three places that cannot see each other: the
// dockview layout blob persisted to localStorage, the guided walkthrough's
// `panel:` field (src/guide/steps.ts), and the puzzle manifests'
// `tools_enabled` mapping. A typo or a stale id in any of them fails
// *silently* -- the guide focuses nothing and strands the player on whatever
// tab happened to be open.
//
// That has already happened once: a guide check pointed at a class the panel
// never emitted, so the tick could not light and nothing said so. This module
// exists so the failure becomes a test failure instead
// (scripts/test-guide.mjs).
//
// Sub-tab ids live here too, for the same reason: once a panel hosts more
// than one former panel, "which section" is as much a routing target as
// "which panel".

export const PANEL_IDS = [
  "die-view",
  "netlist",
  "cone-walker",
  "notebook",
  "waveform",
  "sequence-editor",
  "experiments",
  "model-builder",
  "register-inspector",
  "requirements",
  "sensitivity",
  "register-decoder",
  "sticky-flops",
  "constraints",
  "labels",
  "repl",
] as const;

export type PanelId = (typeof PANEL_IDS)[number];

/**
 * Sub-tabs a panel hosts, by panel id. Empty until the merges land; the guide
 * test reads it so that a step naming a section of a panel that has none is
 * caught the same way a bad panel id is.
 */
export const PANEL_SECTIONS: Partial<Record<PanelId, readonly string[]>> = {};

export function isPanelId(value: string): value is PanelId {
  return (PANEL_IDS as readonly string[]).includes(value);
}