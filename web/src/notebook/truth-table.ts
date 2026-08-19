// A flop's D-cone, enumerated: game-plan.md §4.5's "truth table for any
// flop's D-cone over its leaves -- the `evalnet` enumeration, exposed."
//
// This is NOT a new evaluator. It is `verify.ts`'s `SliceRunner` -- the same
// bit-parallel machine the notebook's `function` claim runs, held to
// `web/src/sim/execute.py`'s equivalent by the golden slice tests -- pointed
// at a plain `SliceView` from `gdsx.api.cone_slice` instead of a claim job.
// Writing a second cone-enumeration loop here would be exactly the mistake
// docs/game/agent-guide.md Rule 4 warns about, just one level up from gate
// traversal instead of at it.
//
// Small cones (<= EXHAUSTIVE_CEILING free leaves) enumerate every case, exact.
// Larger ones sample, honestly: `method` says which, and the UI must never
// present a sampled table with the authority of an exhaustive one -- the same
// PROVEN/LIKELY line the notebook draws (model.ts's header comment) applies
// here even though this is not itself a claim.

import type { SliceView } from "../gdsx-types.ts";
import {
  EXHAUSTIVE_CEILING,
  LANES,
  SliceRunner,
  exhaustiveColumns,
  randomColumns,
  seeded,
} from "./verify.ts";

/** `verify.ts`'s `decode()` returns a `Vector` union (it also names states and
 *  traces); a truth-table row is always an assignment, so this is that one
 *  arm's body, not a second decoder. */
function decodeLeaves(free: readonly string[], columns: Int32Array, lane: number): Record<string, number> {
  const leaves: Record<string, number> = {};
  for (let i = 0; i < free.length; i++) leaves[free[i]] = (columns[i] >>> lane) & 1;
  return leaves;
}

export interface TruthRow {
  leaves: Record<string, number>;
  value: number;
}

export interface TruthTable {
  free: string[];
  method: "exhaustive" | "sampled";
  /** 2**free.length: the true size of the space, whether or not it was walked in full. */
  cases: number;
  rows: TruthRow[];
  /** true when `rows` is not every case in `cases` -- sampling, or the display cap below. */
  truncated: boolean;
}

/** Rendering more rows than this is a DOM problem, not an honesty one --
 *  `truncated` is what tells the player the table is partial, not this cap. */
const DISPLAY_ROW_CAP = 4096;
const SAMPLE_ROWS = 512;

export function computeTruthTable(slice: SliceView): TruthTable {
  const n = slice.free.length;
  const runner = new SliceRunner(slice);
  const cases = 2 ** n;

  if (n === 0) {
    runner.run(new Int32Array(0));
    return {
      free: [],
      method: "exhaustive",
      cases: 1,
      rows: [{ leaves: {}, value: runner.target() & 1 }],
      truncated: false,
    };
  }

  if (n <= EXHAUSTIVE_CEILING) {
    const rows: TruthRow[] = [];
    const columns = new Int32Array(n);
    const blocks = Math.ceil(cases / LANES);
    outer: for (let b = 0; b < blocks; b++) {
      exhaustiveColumns(n, b, columns);
      runner.run(columns);
      const out = runner.target();
      for (let lane = 0; lane < LANES; lane++) {
        const row = b * LANES + lane;
        if (row >= cases) break;
        rows.push({ leaves: decodeLeaves(slice.free, columns, lane), value: (out >>> lane) & 1 });
        if (rows.length >= DISPLAY_ROW_CAP) break outer;
      }
    }
    return { free: [...slice.free], method: "exhaustive", cases, rows, truncated: rows.length < cases };
  }

  const rng = seeded(1);
  const rows: TruthRow[] = [];
  const columns = new Int32Array(n);
  while (rows.length < SAMPLE_ROWS) {
    randomColumns(n, columns, rng);
    runner.run(columns);
    const out = runner.target();
    for (let lane = 0; lane < LANES && rows.length < SAMPLE_ROWS; lane++) {
      rows.push({ leaves: decodeLeaves(slice.free, columns, lane), value: (out >>> lane) & 1 });
    }
  }
  return { free: [...slice.free], method: "sampled", cases, rows, truncated: true };
}