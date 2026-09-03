// What a puzzle's `answerKind` means, said in the imperative.
//
// The point is to tell the player what *shape* of answer ends the puzzle:
// "find the input sequence" and "recover a value" are very different sessions.
// Both screens say it -- the menu on every card, the workspace toolbar for the
// puzzle that is open -- and they must say it the same way, which is why the
// table is here rather than in either of them.

/** The answer kinds. */
export const ANSWER_GOAL: Record<string, string> = {
  sequence: "find the input sequence",
  constant: "recover a value",
  parameter: "recover the parameters",
  model: "build a working model",
  function: "recover the function",
  location: "find the cells",
  patch: "repair the design",
  state: "find the register state",
};

/** The goal phrase for an answer kind, or null for a puzzle that declares
 *  none (or one this table has not caught up with). Callers offer nothing
 *  rather than inventing a phrase. */
export function goalFor(answerKind: string | null | undefined): string | null {
  return answerKind ? (ANSWER_GOAL[answerKind] ?? null) : null;
}