// Types for answer-normalise.mjs, hand-written because that file is
// JavaScript for a reason -- see its header.

/** The canonical form of an answer value: base-10 for anything that reads as
 *  an integer in any radix, lowercased collapsed text otherwise. */
export function normaliseAnswer(value: unknown): string;

/** The string a `digest` check's hash is taken over: `<puzzle>:<field>:<canonical>`. */
export function answerDigestInput(puzzleId: string, fieldName: string, value: unknown): string;