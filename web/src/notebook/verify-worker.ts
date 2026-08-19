// The combinational evaluator, in its own worker.
//
// Why a worker at all: the budget table allows five seconds for a 2**24 sweep.
// Five seconds on the main thread would freeze the die view, the waveform and
// every hover in the app, which is a worse experience than the sweep being slow.
// Off the main thread it is a progress bar next to one claim while the rest of
// the page keeps running at 60fps.
//
// Deliberately NOT Comlink, unlike web/src/worker.ts: this exchanges two plain
// JSON slices for one verdict plus progress messages, and a bare `postMessage`
// protocol is less machinery than a proxy for that. The Pyodide worker earns
// Comlink because it exposes a whole API surface; this exposes one function.

import type { SliceView } from "../gdsx-types.ts";
import type { Verdict } from "./model.ts";
import { checkCombinational, checkEssential, checkRole } from "./verify.ts";

export interface VerifyRequest {
  id: number;
  check: "equal" | "implies" | "essential" | "requirement" | "role";
  design: SliceView;
  claim: SliceView | null;
  value?: number;
  flopValue?: number;
  claimed?: string[];
  role?: string;
  seed?: number;
}

export type VerifyReply =
  | { id: number; done: false; progress: number }
  | { id: number; done: true; verdict: Verdict; ms: number };

self.onmessage = (event: MessageEvent<VerifyRequest>) => {
  const request = event.data;
  const started = performance.now();

  // Throttled to whole percent: a progress message per block would cost more in
  // structured-clone than the evaluation it is reporting on.
  let lastSent = -1;
  const onProgress = (done: number, total: number) => {
    const percent = total > 0 ? Math.floor((done / total) * 100) : 100;
    if (percent === lastSent) return;
    lastSent = percent;
    const reply: VerifyReply = { id: request.id, done: false, progress: percent };
    self.postMessage(reply);
  };

  let verdict: Verdict;
  try {
    verdict = run(request, onProgress);
  } catch (err) {
    verdict = {
      kind: "UNKNOWN",
      reason: err instanceof Error ? err.message : String(err),
    };
  }

  const reply: VerifyReply = {
    id: request.id,
    done: true,
    verdict,
    ms: Math.round(performance.now() - started),
  };
  self.postMessage(reply);
};

function run(
  request: VerifyRequest,
  onProgress: (done: number, total: number) => void,
): Verdict {
  const { check, design, claim } = request;
  const options = {
    value: request.value,
    flopValue: request.flopValue,
    claimed: request.claimed,
    seed: request.seed,
    onProgress,
  };
  if (check === "essential") return checkEssential(design, options);
  if (check === "role") return checkRole(request.role ?? "", design, options);
  return checkCombinational(check, design, claim, options);
}