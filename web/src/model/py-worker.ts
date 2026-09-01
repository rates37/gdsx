// Runs a player's Python model, in its own Pyodide.
//
// Deliberately NOT the analysis worker (web/src/worker.ts). Two reasons:
//
//   * a model that never returns would wedge the analysis engine, and with it
//     every claim, cone and requirement in the app. Here it costs one worker,
//     which the host terminates and replaces.
//   * a model needs plain Python, not `gdsx`. This boots the same same-origin
//     Pyodide runtime the analysis worker does but installs no wheel, so the
//     cost of picking "Python" is a runtime that is already cached, not a
//     second copy of the library.
//
// The result crosses back as JSON rather than as a `PyProxy`: an observable
// dict is small, JSON is the boundary the rest of the app already uses, and
// converting in Python is where a non-integer observable can be reported
// against the name that produced it.

import { loadPyodide, type PyodideInterface } from "pyodide";

import { assetUrl } from "../asset-url.ts";

export interface ModelRequest {
  id: number;
  source: string;
  vectors: number[][];
}

export type ModelReply =
  | { id: number; ok: true; results: Record<string, number>[] }
  | { id: number; ok: false; error: string };

// Defines the player's model in a namespace of its own, then evaluates every
// vector into JSON. `evaluate` is called with a real `set[int]`, per §6.
const HARNESS = `
import json

def _gdsx_run_model(source, vectors):
    scope = {}
    exec(source, scope)
    evaluate = scope.get("evaluate")
    if not callable(evaluate):
        raise ValueError("no function named evaluate(pulses) was defined")

    out = []
    for index, pulses in enumerate(vectors):
        answer = evaluate(set(int(p) for p in pulses))
        if not isinstance(answer, dict):
            raise ValueError(
                f"evaluate() returned {type(answer).__name__} on vector {index}; "
                "it must return a dict of observables, e.g. {'success': 1}"
            )
        row = {}
        for key, value in answer.items():
            if value is True or value is False:
                row[str(key)] = int(value)
                continue
            if value not in (0, 1):
                raise ValueError(
                    f"evaluate() returned {value!r} for {key!r} on vector {index}; "
                    "observables must be 0 or 1"
                )
            row[str(key)] = int(value)
        out.append(row)
    return json.dumps(out)
`;

let ready: Promise<PyodideInterface> | null = null;

function boot(): Promise<PyodideInterface> {
  if (!ready) {
    ready = loadPyodide({ indexURL: assetUrl("pyodide/") }).then((pyodide) => {
      pyodide.runPython(HARNESS);
      return pyodide;
    });
  }
  return ready;
}

self.onmessage = async (event: MessageEvent<ModelRequest>) => {
  const { id, source, vectors } = event.data;
  try {
    const pyodide = await boot();
    const run = pyodide.globals.get("_gdsx_run_model") as (
      source: string,
      vectors: unknown,
    ) => string;
    const json = run(source, pyodide.toPy(vectors));
    (self as unknown as Worker).postMessage({
      id,
      ok: true,
      results: JSON.parse(json) as Record<string, number>[],
    } satisfies ModelReply);
  } catch (err) {
    (self as unknown as Worker).postMessage({
      id,
      ok: false,
      // Pyodide puts the Python traceback in `message`; it is the useful half
      // for someone debugging their own model, so it is passed through whole.
      error: err instanceof Error ? err.message : String(err),
    } satisfies ModelReply);
  }
};