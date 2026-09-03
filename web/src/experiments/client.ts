// The panel's handle on the sweep worker: one request in flight, progress out,
// a result or an error back. Modelled on the notebook's `VerifyEngine`,
// including the rule that a crashed worker must not leave the panel spinning.

import type { ExperimentContext, ExperimentResult, RecipeId, RecipeParams } from "./recipes.ts";
import type { SweepReply, SweepRequest } from "./sweep-worker.ts";

export class SweepClient {
  private worker: Worker | null = null;
  private nextId = 1;
  private readonly pending = new Map<
    number,
    {
      resolve: (result: ExperimentResult) => void;
      reject: (err: Error) => void;
      onProgress?: (fraction: number) => void;
    }
  >();

  /** Where the worker fetches its own copy of the gate tape from. */
  constructor(private readonly tapeUrl: string) {}

  run(
    ctx: ExperimentContext,
    recipe: RecipeId,
    params: RecipeParams,
    onProgress?: (fraction: number) => void,
  ): Promise<ExperimentResult> {
    const worker = this.ensure();
    const { tape: _tape, ...rest } = ctx;
    const request: SweepRequest = {
      id: this.nextId++,
      tapeUrl: this.tapeUrl,
      ctx: rest,
      recipe,
      params,
    };
    return new Promise<ExperimentResult>((resolve, reject) => {
      this.pending.set(request.id, { resolve, reject, onProgress });
      worker.postMessage(request);
    });
  }

  private ensure(): Worker {
    if (this.worker) return this.worker;
    this.worker = new Worker(new URL("./sweep-worker.ts", import.meta.url), {
      type: "module",
    });
    this.worker.onmessage = (event: MessageEvent<SweepReply>) => {
      const reply = event.data;
      const waiting = this.pending.get(reply.id);
      if (!waiting) return;
      if (!reply.done) {
        waiting.onProgress?.(reply.progress);
        return;
      }
      this.pending.delete(reply.id);
      if (reply.ok) waiting.resolve(reply.result);
      else waiting.reject(new Error(reply.error));
    };
    this.worker.onerror = (event) => {
      for (const [id, waiting] of this.pending) {
        waiting.reject(new Error(event.message || "the sweep worker stopped"));
        this.pending.delete(id);
      }
      // The worker is gone; the next run gets a fresh one rather than posting
      // into a dead port and never being answered.
      this.dispose();
    };
    return this.worker;
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
  }
}