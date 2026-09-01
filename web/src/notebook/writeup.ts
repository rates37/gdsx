// The write-up: on completion the game generates a
// Markdown document from the notebook: claims in the order proven, the
// evidence attached to each, the experiments run, the model source, and the
// final key. The player can edit and export it.
//
// This module only assembles Markdown from data that already lives in the
// notebook, evidence log, model store and sim store -- it invents nothing new
// to say about a claim. In particular it never re-derives what a verdict
// means: `claimText` and `verdictStyle` from `./model.ts` are the only place
// that happens anywhere in this codebase, and this file calls them rather
// than switching on `verdict.kind` itself. A DISPROVEN claim comes out
// struck through and a LIKELY one never has the word "proven" near it because
// those functions already guarantee that, not because this file re-checks it.
//
// No DOM here, on purpose -- this is the same split as `./scoring.ts` and
// `./model.ts` versus `../panels/notebook-panel.ts`: pure logic that a script
// or a unit test can call without a browser.

import type { ClaimRecord, VerdictRecord } from "./model.ts";
import { claimText, verdictStyle } from "./model.ts";
import type { Notebook } from "./store.ts";
import type { EvidenceLog, EvidenceRecord } from "./evidence.ts";
import type { ModelStore } from "../model/store.ts";
import { VALIDATION_VECTORS } from "../model/store.ts";
import type { SimStore } from "../sim/store.ts";
import { asDuration, type ScoreCard } from "./scoring.ts";
import type { HintTier } from "../puzzles/catalog.ts";

export interface WriteupOptions {
  /** The net the notebook's coverage is measured against, and whose latch
   *  state decides "solved" -- the same constant every other panel that
   *  needs it takes as a parameter (see `stickyFlopsPanel` in main.ts). */
  /** Null for a puzzle with no lock, whose write-up reports no verdict --
   *  there is nothing to latch. */
  successNet: string | null;
  /** The sequence-editor port whose contents are printed as the final key.
   *  Null for a puzzle with no data input at all -- an autonomous design's
   *  answer is a value it computes, not a stimulus, and the write-up then
   *  has no key section to print rather than an empty one. */
  keyPort: string | null;
  puzzleId: string;
}

/**
 * The half of the write-up that is not about the design: the score, and how
 * the session went.
 *
 * Optional, and passed in rather than derived, for two reasons. The score
 * comes from the progress store and the puzzle descriptor, neither of which
 * this module or the Notebook panel has any other business knowing about. And
 * a caller with no session -- a test, or a player exporting a write-up before
 * solving -- gets the document without these sections rather than a document
 * full of zeroes.
 */
export interface WriteupSession {
  score: ScoreCard;
  /** Every submit attempt, accepted or not. */
  attempts: number;
  /** Engaged milliseconds at the first accepted verdict, or null if unsolved
   *  or never recorded. */
  solveMs: number | null;
  parMinutes: number | null;
  /** The tiers actually revealed, in order, with their text -- quoted in full
   *  rather than counted. The write-up is the account the player keeps, and
   *  what a hint told them is part of how the solve went. */
  hintsTaken: readonly HintTier[];
}

function when(at: number): string {
  return new Date(at).toISOString();
}

function bitString(bits: Uint8Array): string {
  return Array.from(bits, (b) => (b ? "1" : "0")).join("");
}

/** One line per verdict entry, in the shape the notebook panel already uses. */
function verdictLine(entry: VerdictRecord): string {
  const style = verdictStyle(entry.verdict);
  return `${style.label} · ${when(entry.at)}`;
}

function renderClaim(record: ClaimRecord): string {
  const current = record.history[record.history.length - 1] ?? null;
  const text = claimText(record.claim);
  const lines: string[] = [];

  if (current) {
    const style = verdictStyle(current.verdict);
    const heading = style.strike ? `~~${text}~~` : `**${text}**`;
    lines.push(`- ${heading} — ${style.label}`);
    if (current.notes.length) lines.push(`  - assumptions: ${current.notes.join("; ")}`);
    if (current.call) lines.push(`  - \`${current.call}\``);
    if (record.history.length > 1) {
      lines.push(`  - earlier: ${record.history.slice(0, -1).map(verdictLine).join(", ")}`);
    }
  } else {
    lines.push(`- **${text}** — not yet verified`);
  }
  return lines.join("\n");
}

function renderEvidence(record: EvidenceRecord): string {
  const lines: string[] = [];
  lines.push(`### ${record.recipe} — ${when(record.at)}`);
  lines.push("");
  lines.push(record.summary);
  lines.push("");
  lines.push(`Baseline: ${record.baseline}`);
  if (record.lines.length) {
    lines.push("");
    for (const line of record.lines) lines.push(`- ${line}`);
  }
  if (record.warnings.length) {
    lines.push("");
    for (const warning of record.warnings) lines.push(`> ⚠ ${warning}`);
  }
  if (record.call) {
    lines.push("");
    lines.push(`\`${record.call}\``);
  }
  return lines.join("\n");
}

/**
 * The score, as a table of what each part of the game was worth.
 *
 * The breakdown rather than the number, on purpose: a bare "63/100" is a
 * grade, and this document is meant to be an account of a session. Every row
 * carries the note `scoreCard` wrote for it.
 */
function renderScore(score: ScoreCard): string[] {
  const out: string[] = [];
  out.push("## Score");
  out.push("");
  out.push(`**${score.total} of ${score.available}.**`);
  out.push("");
  out.push("| | earned | of | |");
  out.push("|---|---:|---:|---|");
  for (const line of score.lines) {
    const of = line.available === 0 ? "" : String(line.available);
    out.push(`| ${line.name} | ${line.earned} | ${of} | ${line.note} |`);
  }
  if (score.bare) {
    // A solve with an empty notebook is a real solve. This says what the other
    // points are FOR rather than leaving a low number to read as a failure --
    // the weights are an opinion about what is worth doing, not a judgement
    // of how this player did it.
    out.push("");
    out.push(
      `Solved by driving the design. The other ${score.available - score.total} points are for ` +
        "explaining it: what the registers do, what the success cone depends on, and a model " +
        "that agrees with the gate tape.",
    );
  }
  out.push("");
  return out;
}

/** How the session went: attempts, time against par, and every hint taken. */
function renderSession(session: WriteupSession): string[] {
  const out: string[] = [];
  out.push("## Session");
  out.push("");
  out.push(`- ${session.attempts} submitted answer(s)`);
  if (session.solveMs !== null) {
    const spent = asDuration(session.solveMs);
    out.push(
      session.parMinutes === null
        ? `- ${spent} at the design (this puzzle declares no par)`
        : `- ${spent} at the design, against a par of ${session.parMinutes} min`,
    );
  }
  if (session.hintsTaken.length === 0) {
    out.push("- no hints taken");
  } else {
    out.push(`- ${session.hintsTaken.length} hint(s) taken:`);
    for (const hint of session.hintsTaken) out.push(`  - ${hint.text}`);
  }
  out.push("");
  return out;
}

/** Assemble the write-up fresh from the current state of every store. Pure:
 *  called again it returns a new string, nothing here is persisted -- the
 *  notebook, evidence log and model store already are the persistence layer. */
export function generateWriteup(
  notebook: Notebook,
  evidenceLog: EvidenceLog,
  modelStore: ModelStore,
  simStore: SimStore,
  opts: WriteupOptions,
  session: WriteupSession | null = null,
): string {
  const claims = notebook.all();
  // "Settled" = has at least one recorded verdict, of whatever kind. Ordered
  // by the timestamp of that FIRST verdict, which is when the claim actually
  // entered the record -- not by when it was written down, and not by its
  // current (possibly later, possibly overturned) verdict.
  const settled = claims
    .filter((r) => r.history.length > 0)
    .slice()
    .sort((a, b) => a.history[0].at - b.history[0].at);
  const unsettled = claims.filter((r) => r.history.length === 0);

  // A puzzle with no lock has nothing to latch, so it has no verdict to
  // report -- not "not solved", which would be a claim about a net that does
  // not exist.
  const latchedAt =
    opts.successNet === null ? null : simStore.firstLatchedHigh(opts.successNet);
  const solved = latchedAt !== null;
  const keyBits = opts.keyPort === null ? null : bitString(simStore.bitsOf(opts.keyPort));

  const out: string[] = [];

  // ---- 1. result banner --------------------------------------------------
  out.push(`# ${opts.puzzleId} — write-up`);
  out.push("");
  if (keyBits === null) {
    out.push(
      solved
        ? `**Result: solved.** \`${opts.successNet}\` latches high at cycle ${latchedAt}.`
        : `**Result: not solved.** \`${opts.successNet}\` has not latched high.`,
    );
  } else {
    out.push(
      solved
        ? `**Result: solved.** \`${opts.successNet}\` latches high at cycle ${latchedAt} ` +
            `under the key on \`${opts.keyPort}\`:`
        : `**Result: not solved.** \`${opts.successNet}\` has not latched high under the current ` +
            `sequence on \`${opts.keyPort}\`.`,
    );
    out.push("");
    out.push("```");
    out.push(keyBits);
    out.push("```");
  }
  out.push("");
  out.push("---");
  out.push("");

  // ---- 1b. score and session ------------------------------------------
  //
  // First, because this is the summary a player re-reads. Both are omitted
  // without a session, and the score is omitted for an unsolved puzzle --
  // a score is shown only after solving, and a write-up exported mid-session
  // is a working document, not a result.
  if (session) {
    if (session.score.solved) out.push(...renderScore(session.score));
    out.push(...renderSession(session));
    out.push("---");
    out.push("");
  }

  // ---- 2/3. claims, in the order settled ---------------------------------
  out.push("## Claims, in the order settled");
  out.push("");
  if (settled.length === 0) {
    out.push("_No claim has been verified yet._");
  } else {
    for (const record of settled) out.push(renderClaim(record));
  }
  out.push("");

  if (unsettled.length) {
    out.push("## Claims not yet verified");
    out.push("");
    out.push("_Listed separately -- these were never settled, so they are not part of the timeline above._");
    out.push("");
    for (const record of unsettled) out.push(renderClaim(record));
    out.push("");
  }
  out.push("---");
  out.push("");

  // ---- 3. experiments run -------------------------------------------------
  const evidence = [...evidenceLog.all()].sort((a, b) => a.at - b.at);
  out.push("## Experiments run");
  out.push("");
  if (evidence.length === 0) {
    out.push("_No experiments recorded._");
  } else {
    for (const record of evidence) {
      out.push(renderEvidence(record));
      out.push("");
    }
  }
  out.push("---");
  out.push("");

  // ---- 4. model source -----------------------------------------------------
  out.push("## Model");
  out.push("");
  const run = modelStore.latest();
  if (!run) {
    out.push(`_No run yet. Validation needs agreement on ${VALIDATION_VECTORS} vectors._`);
  } else if (run.validated) {
    out.push(
      `Agreement over ${run.vectors} vectors on ${run.watch.join(", ")} ` +
        `(as of ${when(run.at)}). This is agreement, not a proof: the key space is larger ` +
        `than any vector set.`,
    );
  } else {
    out.push(
      `Latest run: ${run.agreed} / ${run.vectors} vectors agreed on ${run.watch.join(", ")} ` +
        `(as of ${when(run.at)}) -- not validated.`,
    );
  }
  out.push("");
  out.push(`\`\`\`${modelStore.language}`);
  out.push(modelStore.source);
  out.push("```");
  out.push("");
  out.push("---");
  out.push("");

  // ---- 5. final key ---------------------------------------------------------
  if (keyBits !== null) {
    out.push("## Final key");
    out.push("");
    out.push(`Sequence on \`${opts.keyPort}\` (${simStore.cycles} cycles):`);
    out.push("");
    out.push("```");
    out.push(keyBits);
    out.push("```");
  }

  return out.join("\n");
}