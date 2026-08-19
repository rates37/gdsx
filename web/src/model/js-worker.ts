// Runs a player's JavaScript model. One `evaluate(pulses)` per vector.
//
// In a worker for two reasons, and neither of them is anti-cheat (§1: there is
// none, and the solution ships with the puzzle):
//
//   * a model with an accidental infinite loop is a normal thing to write while
//     you are working one out, and it must cost you a click rather than the
//     session. The host terminates this worker on a timeout and starts another.
//   * a few hundred model calls should not compete with the die view for the
//     main thread.
//
// `pulses` arrives as a `Set<number>`, matching the Python signature in
// game-plan.md §6 rather than quietly handing JS an array and Python a set.

export interface ModelRequest {
  id: number;
  source: string;
  vectors: number[][];
}

export type ModelReply =
  | { id: number; ok: true; results: Record<string, number>[] }
  | { id: number; ok: false; error: string };

type Evaluate = (pulses: Set<number>) => unknown;

/** 0 or 1, or a clear failure. A model that answers `undefined` is a bug worth naming. */
function bit(value: unknown, key: string, index: number): number {
  if (value === true) return 1;
  if (value === false) return 0;
  const n = Number(value);
  if (n === 0 || n === 1) return n;
  throw new Error(
    `evaluate() returned ${JSON.stringify(value)} for ${JSON.stringify(key)} on ` +
      `vector ${index}; observables must be 0 or 1`,
  );
}

self.onmessage = (event: MessageEvent<ModelRequest>) => {
  const { id, source, vectors } = event.data;
  try {
    const factory = new Function(
      `${source}\n;return typeof evaluate === "function" ? evaluate : null;`,
    ) as () => Evaluate | null;
    const evaluate = factory();
    if (!evaluate) {
      throw new Error("no function named evaluate(pulses) was defined");
    }

    const results: Record<string, number>[] = [];
    for (let i = 0; i < vectors.length; i++) {
      const answer = evaluate(new Set(vectors[i]));
      if (answer === null || typeof answer !== "object") {
        throw new Error(
          `evaluate() returned ${typeof answer} on vector ${i}; it must return an ` +
            `object of observables, e.g. { success: 1 }`,
        );
      }
      const row: Record<string, number> = {};
      for (const [key, value] of Object.entries(answer as Record<string, unknown>)) {
        row[key] = bit(value, key, i);
      }
      results.push(row);
    }
    (self as unknown as Worker).postMessage({ id, ok: true, results } satisfies ModelReply);
  } catch (err) {
    (self as unknown as Worker).postMessage({
      id,
      ok: false,
      error: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
    } satisfies ModelReply);
  }
};