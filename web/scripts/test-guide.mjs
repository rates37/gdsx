// Checks that the guided walkthrough still points at things that exist.
//
// The guide is the one part of the app that drives the UI on the player's
// behalf: each step names a panel to bring to the front, and a DOM check that
// lights its tick. Both fail *silently* when they go stale -- a step naming a
// panel that has been renamed or merged away focuses nothing and strands the
// player on whatever tab was already open, and a check pointing at a class
// the panel never emits simply never ticks.
//
// That is not hypothetical: the "decoder" step tested `.rd-groups .rd-group`
// while the panel emitted `.rd-group-chip`, so only its fallback clause ever
// fired, and no test in the suite could see it.
//
// What is checkable without a browser is the routing -- panel ids and section
// ids -- so that is what this asserts, plus a few structural invariants that
// keep the walkthrough honest. The DOM selectors themselves are covered by
// driving the real app (see the plan's verification section).
//
// Usage: node --experimental-strip-types web/scripts/test-guide.mjs

import { STEPS, GUIDE_PUZZLE_ID } from "../src/guide/steps.ts";
import { PANEL_IDS, PANEL_SECTIONS, isPanelId } from "../src/panels/ids.ts";

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

// ---- 1. every step routes somewhere real -------------------------------

for (const step of STEPS) {
  if (step.panel !== undefined) {
    check(
      isPanelId(step.panel),
      `step "${step.id}" names panel "${step.panel}", which is not in PANEL_IDS ` +
        `(src/panels/ids.ts). A step pointing at a panel that does not exist ` +
        `focuses nothing and silently strands the player.`,
    );
  }
  if (step.section !== undefined) {
    const sections = PANEL_SECTIONS[step.panel] ?? [];
    check(
      sections.includes(step.section),
      `step "${step.id}" names section "${step.section}" of panel "${step.panel}", ` +
        `which declares sections [${sections.join(", ")}].`,
    );
  }
}

// ---- 2. structural invariants the walkthrough relies on ----------------

const ids = STEPS.map((s) => s.id);
check(new Set(ids).size === ids.length, "step ids must be unique -- progress is stored per id");
check(STEPS.length > 0, "there is at least one step");
check(
  typeof GUIDE_PUZZLE_ID === "string" && GUIDE_PUZZLE_ID.length > 0,
  "the guide names the puzzle it walks",
);

for (const step of STEPS) {
  check(
    typeof step.title === "string" && step.title.trim().length > 0,
    `step "${step.id}" has a title`,
  );
  check(
    Array.isArray(step.body) && step.body.length > 0,
    `step "${step.id}" has at least one paragraph`,
  );
  // A goal is the line under the body that the tick reports against. One
  // without a check can never tick, which reads to the player as "I did what
  // it asked and it did not notice".
  check(
    step.goal === undefined || typeof step.done === "function",
    `step "${step.id}" states a goal but has no done() check, so its tick can never light`,
  );
  check(
    step.done === undefined || step.goal !== undefined,
    `step "${step.id}" has a done() check but no goal line, so nothing tells the player what it wants`,
  );
}

// ---- 3. every panel the guide can reach is covered ---------------------
// Not a hard requirement -- some panels are reachable without being taught --
// but the walkthrough claims to show every tool, so a gap is worth surfacing.

const taught = new Set(STEPS.map((s) => s.panel).filter(Boolean));
const untaught = PANEL_IDS.filter((id) => !taught.has(id));
if (untaught.length) {
  console.warn(
    `note: the walkthrough never opens ${untaught.join(", ")} — fine if a step ` +
      `teaches it from elsewhere, worth a look if not.`,
  );
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`ok: ${checks} guide-routing checks`);