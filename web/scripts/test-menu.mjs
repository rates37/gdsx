// Checks the level menu's card model (web/src/menu/entries.ts): what each
// card says, and which puzzle Continue offers.
//
// The properties worth protecting, in the order they are checked below:
//
//   1. every catalog puzzle gets a card, in catalog order, and none of them
//      is locked, hidden or gated on another puzzle's progress -- the catalog
//      is ordered and every level has always been reachable by URL, so the
//      menu must not invent a progression;
//   2. a card carries what the descriptor already knows -- title, blurb,
//      difficulty, par, the answer kind's goal phrase -- and a link that IS
//      the routing rule, `?puzzle=<id>`;
//   3. solved state comes from the progress store: the tick, the first-solve
//      day and the attempt count, with an unattempted puzzle marked in no way
//      at all;
//   4. Continue offers the last-played puzzle, and offers nothing rather than
//      a dead link when there is no last-played id or its puzzle has left the
//      catalog. That entry is the whole reason a bare URL is allowed to stop
//      opening the last-played puzzle, so it is the one that must not rot.
//
// No browser is available under plain Node, and none is needed: entries.ts
// takes progress records as an argument rather than reading localStorage, and
// returns data rather than DOM.
//
// Usage: node --experimental-strip-types web/scripts/test-menu.mjs

import { continueEntry, menuEntries } from "../src/menu/entries.ts";

let checks = 0;
const failures = [];

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

function eq(got, want, message) {
  check(
    JSON.stringify(got) === JSON.stringify(want),
    `${message}\n      got:  ${JSON.stringify(got)}\n      want: ${JSON.stringify(want)}`,
  );
}

/** A descriptor with only the fields the menu reads -- the rest of a real
 *  PuzzleDescriptor (assets, driver, checks) is the workspace's business. */
function puzzle(id, extra = {}) {
  return {
    id,
    dir: id,
    title: id.toUpperCase(),
    blurb: `${id} blurb`,
    difficulty: null,
    parMinutes: null,
    answerKind: null,
    toolsEnabled: [],
    assets: { netlist: "", render: "", tape: "" },
    driver: {},
    checks: null,
    ...extra,
  };
}

const catalog = [
  puzzle("0-first-light", { difficulty: 0, parMinutes: 15, answerKind: "sequence" }),
  puzzle("1-warm-start", { difficulty: 1, parMinutes: 20, answerKind: "constant" }),
  puzzle("original-puzzle", { difficulty: "hard", parMinutes: 120, answerKind: "sequence" }),
];

// ---- 1. one card per puzzle, in catalog order, ungated -------------------

const none = menuEntries(catalog, []);
eq(
  none.map((entry) => entry.id),
  ["0-first-light", "1-warm-start", "original-puzzle"],
  "one card per catalog entry, in catalog order",
);
check(
  none.every((entry) => !("locked" in entry) && !("available" in entry)),
  "no card carries a lock/unlock flag -- the catalog is not a progression",
);

// ---- 2. what a card says -------------------------------------------------

eq(none[0].href, "?puzzle=0-first-light", "the card's link is the routing rule itself");
eq(none[0].title, "0-FIRST-LIGHT", "the title comes from the descriptor");
eq(none[0].blurb, "0-first-light blurb", "so does the blurb");
eq(none[0].difficulty, "difficulty 0", "a numeric difficulty is labelled");
eq(none[2].difficulty, "hard", "a named one is shown as it stands");
eq(none[0].par, "par 15m", "par time is shown in minutes");
eq(none[0].goal, "find the input sequence", "the goal phrase is the toolbar's, for `sequence`");
eq(none[1].goal, "recover a value", "…and for `constant`");
eq(
  menuEntries([puzzle("x")], [])[0],
  {
    id: "x",
    title: "X",
    blurb: "x blurb",
    href: "?puzzle=x",
    difficulty: null,
    par: null,
    goal: null,
    solved: false,
    solvedOn: null,
    attempts: 0,
  },
  "a descriptor that declares nothing optional produces a card that claims nothing",
);

// ---- 3. solved state -----------------------------------------------------

const progress = [
  { puzzleId: "0-first-light", solvedAt: "2026-08-29T10:11:12.000Z", attempts: 3 },
  { puzzleId: "1-warm-start", attempts: 11 },
];
const entries = menuEntries(catalog, progress);

check(entries[0].solved, "an accepted submission marks the card solved");
eq(entries[0].solvedOn, "2026-08-29", "the first-solve day is shown");
eq(entries[0].attempts, 3, "so is the attempt count");
check(!entries[1].solved, "attempts without an accepted submission do not mark it solved");
eq(entries[1].attempts, 11, "…but the eleven tries are still counted");
eq(entries[1].solvedOn, null, "…and there is no solve day to show");
check(!entries[2].solved, "an unattempted puzzle is marked in no way at all");
eq(entries[2].attempts, 0, "…and reports no attempts");
eq(
  menuEntries(catalog, [{ puzzleId: "0-first-light", solvedAt: "not a date", attempts: 1 }])[0]
    .solvedOn,
  null,
  "an unreadable saved timestamp shows no day rather than `Invalid Date`",
);

// ---- 4. Continue ---------------------------------------------------------

eq(
  continueEntry(entries, "1-warm-start")?.id,
  "1-warm-start",
  "Continue offers the last-played puzzle",
);
eq(continueEntry(entries, null), null, "…nothing at all when there is no last-played id");
eq(
  continueEntry(entries, "a-puzzle-that-was-deleted"),
  null,
  "…and nothing when the last-played puzzle has left the catalog, rather than a dead link",
);

// ---- report --------------------------------------------------------------

if (failures.length) {
  console.error(`FAIL ${failures.length}/${checks}`);
  for (const failure of failures) console.error(`  ✗ ${failure}`);
  process.exit(1);
}
console.log(`ok — ${checks} checks (menu cards, solved state, Continue)`);