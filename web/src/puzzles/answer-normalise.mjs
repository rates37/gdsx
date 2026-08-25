// Canonical form of a submitted or authored answer value, and the exact
// string that gets hashed for a `digest` check.
//
// Two hosts share this: the Node-side baker (web/scripts/puzzle-index.mjs,
// which hashes the authored answer at sync time) and the browser (which
// hashes what the player typed). They MUST agree byte-for-byte or a correct
// answer is rejected, so the normalisation lives in one file rather than
// being written twice.
//
// **This file is .mjs and not .ts on purpose.** `scripts/sync-assets.mjs`
// runs as bare `node scripts/sync-assets.mjs` (package.json `predev` and
// `prebuild`), with no `--experimental-strip-types`, so everything it
// reaches transitively has to be real JavaScript. The sibling
// answer-normalise.d.mts is what gives the browser side types under
// `tsc -b`. Converting this to TypeScript breaks `npm run build`.
//
// No crypto here: these are pure string functions, and each host hashes with
// whatever it has (node:crypto's createHash, crypto.subtle.digest).

/** Optional sign, then a radix prefix or plain decimal digits. Anchored, so
 *  "0x1 fish" is text rather than a badly-punctuated 1. */
const INTEGER = /^-?(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|[0-9]+)$/;

/**
 * The canonical form of an answer value.
 *
 * An answer that reads as an integer normalises to its base-10 string, which
 * is what makes `0xA3000000`, `"0xa3000000"` and `2734686208` a single
 * answer rather than three. Anything else normalises as text: trimmed,
 * lowercased, internal whitespace runs collapsed to one space.
 *
 * Deliberately width-independent -- no masking to a declared bit width. A
 * check's `width` is a hint for the submission widget and is not always
 * present, and making the canonical form depend on it would mean the same
 * value normalised two ways depending on which side was asking.
 */
export function normaliseAnswer(value) {
  const text = String(value ?? "").trim().replace(/_/g, "").replace(/^\+/, "");
  if (INTEGER.test(text)) return BigInt(text).toString(10);
  return text.toLowerCase().replace(/\s+/g, " ");
}

/**
 * The string a `digest` check's hash is taken over.
 *
 * The `<puzzle>:<field>:` prefix is domain separation, so two puzzles whose
 * answers happen to coincide do not ship the same hash and advertise the
 * fact. It is not a security measure -- game-plan.md §1 rules anti-cheat out
 * as a non-goal, and a SHA-256 of a short number is trivially reversed by
 * anyone who wants to. The point is only to keep the answer out of plain
 * sight in a file the player can open.
 */
export function answerDigestInput(puzzleId, fieldName, value) {
  return `${puzzleId}:${fieldName}:${normaliseAnswer(value)}`;
}