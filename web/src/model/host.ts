// One interface over "run this model on these vectors", whichever language it
// is written in: Python (Pyodide) or JS, their choice.
//
// Both hosts are workers, and the host owns the timeout: a model that never
// returns is terminated and the worker replaced, so a runaway loop costs one
// run rather than the session. Nothing else in the app has to know which
// language was picked -- `diff.ts` compares dictionaries.

import type { ModelReply, ModelRequest } from "./js-worker.ts";
import type { Pulses } from "./vectors.ts";

export type Language = "javascript" | "python";

/** How long a whole batch may take before the worker is killed. Python's is
 *  larger because its first run pays for booting a Pyodide of its own. */
const TIMEOUT_MS: Record<Language, number> = {
  javascript: 10_000,
  python: 60_000,
};

export class ModelTimeout extends Error {
  constructor(readonly ms: number) {
    super(
      `the model did not finish within ${Math.round(ms / 1000)}s and was stopped — ` +
        `an infinite loop, or too much work per vector`,
    );
  }
}

export class ModelHost {
  private worker: Worker | null = null;
  private nextId = 1;

  constructor(readonly language: Language) {}

  /** Run `source`'s `evaluate` over every vector, in order. */
  run(source: string, vectors: readonly Pulses[]): Promise<Record<string, number>[]> {
    const worker = this.ensure();
    const request: ModelRequest = {
      id: this.nextId++,
      source,
      vectors: vectors.map((v) => [...v]),
    };
    const limit = TIMEOUT_MS[this.language];

    return new Promise<Record<string, number>[]>((resolve, reject) => {
      const timer = setTimeout(() => {
        // The worker is not merely abandoned: it is still spinning, and a
        // second run against a wedged worker would never be answered.
        this.dispose();
        reject(new ModelTimeout(limit));
      }, limit);

      const done = (fn: () => void) => {
        clearTimeout(timer);
        worker.removeEventListener("message", onMessage);
        worker.removeEventListener("error", onError);
        fn();
      };
      const onMessage = (event: MessageEvent<ModelReply>) => {
        if (event.data.id !== request.id) return;
        const reply = event.data;
        done(() =>
          reply.ok ? resolve(reply.results) : reject(new Error(reply.error)),
        );
      };
      const onError = (event: ErrorEvent) => {
        done(() => reject(new Error(event.message || "the model worker stopped")));
      };

      worker.addEventListener("message", onMessage);
      worker.addEventListener("error", onError);
      worker.postMessage(request);
    });
  }

  private ensure(): Worker {
    if (this.worker) return this.worker;
    this.worker =
      this.language === "python"
        ? new Worker(new URL("./py-worker.ts", import.meta.url), { type: "module" })
        : new Worker(new URL("./js-worker.ts", import.meta.url), { type: "module" });
    return this.worker;
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
  }
}

/** The starter each language opens with: a model, not a stub. It is wrong on
 *  purpose -- "count the pulses and hope" is the first thing anyone writes, the
 *  differential disagrees with it immediately, and that is the loop. */
/**
 * The empty model a player starts from, in each language.
 *
 * Takes the observable's name rather than writing one down: the sample used
 * to return `success` from a guess about a pulse count, which named one
 * design's lock and, worse, put a specific number in front of a player who
 * had not worked one out yet. It now returns 0 for whatever this puzzle's
 * lock is called, which is a model that runs, is wrong, and says nothing.
 *
 * `null` for a puzzle with no lock: the starter then names no observable and
 * the player picks one, which is the whole exercise for that answer kind.
 */
export function starterFor(observable: string | null): Record<Language, string> {
  const jsKey = observable === null ? "/* an observable */" : JSON.stringify(observable);
  const pyKey = observable === null ? "# an observable" : JSON.stringify(observable);
  return {
    javascript: `// pulses is a Set of cycle numbers where the key port is high.
// Return the observables you claim to predict — every name must be a
// real net or flop of the design, because that is what it is checked against.
function evaluate(pulses) {
  return { ${jsKey}: 0 };
}
`,
    python: `# pulses is a set of cycle numbers where the key port is high.
# Return the observables you claim to predict — every name must be a
# real net or flop of the design, because that is what it is checked against.
def evaluate(pulses):
    return {${pyKey}: 0}
`,
  };
}