// Runs a sweep off the main thread.
//
// A sweep is `rows x cycles` tape steps -- a 141-row single-pulse sweep over
// Two Stars is ~20k steps, a few hundred milliseconds of arithmetic. That is
// affordable, but it is not affordable *behind the die view*: on the main
// thread the run has to yield to keep the page alive, and every yield waits
// behind whatever the renderer is doing, which turned a 300 ms sweep into a
// multi-second one when this was first measured in a real browser.
//
// So the engine runs here, exactly as the notebook's evaluator does, and for
// the same reason. The worker keeps its own copy of the gate tape (fetched from
// the same same-origin URL the page already loaded, so it is a cache hit) rather
// than having one posted to it per request: the tape does not change, and
// re-sending it for every recipe would cost more than the sweep.

import { parseTapeBundle, type GateTape } from "../sim/tape.ts";
import {
  run,
  type ExperimentContext,
  type ExperimentResult,
  type RecipeId,
  type RecipeParams,
} from "./recipes.ts";

/** The context, minus the tape: everything else survives a structured clone. */
export type SweepContext = Omit<ExperimentContext, "tape">;

export interface SweepRequest {
  id: number;
  tapeUrl: string;
  ctx: SweepContext;
  recipe: RecipeId;
  params: RecipeParams;
}

export type SweepReply =
  | { id: number; done: false; progress: number }
  | { id: number; done: true; ok: true; result: ExperimentResult }
  | { id: number; done: true; ok: false; error: string };

const tapes = new Map<string, Promise<GateTape>>();

function tapeOf(url: string): Promise<GateTape> {
  let found = tapes.get(url);
  if (!found) {
    found = fetch(url)
      .then((r) => r.arrayBuffer())
      .then(parseTapeBundle);
    tapes.set(url, found);
  }
  return found;
}

self.onmessage = async (event: MessageEvent<SweepRequest>) => {
  const { id, tapeUrl, ctx, recipe, params } = event.data;
  const post = (reply: SweepReply) => (self as unknown as Worker).postMessage(reply);
  try {
    const tape = await tapeOf(tapeUrl);
    const result = await run({ ...ctx, tape }, recipe, params, {
      onProgress: (progress) => post({ id, done: false, progress }),
      // Nothing else runs on this thread, so the engine's own yielding is only
      // there to keep progress flowing.
      sliceMs: 60,
    });
    post({ id, done: true, ok: true, result });
  } catch (err) {
    post({
      id,
      done: true,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    });
  }
};