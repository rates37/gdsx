// One claim in, one verdict out.
//
// This is the seam where `gdsx.api.claim_plan` decides
// what it would take to settle a claim, and this decides where that work runs
// and turns its result into a `Verdict`. Three destinations, and the reason each
// is where it is:
//
//   * settled already -- structural claims. Their evidence is a fact about the
//     netlist and looking it up IS the verification, so the library answers and
//     nothing runs here.
//   * the verify worker -- combinational and transition claims. Up to 2**24
//     assignments; off the main thread so a five-second sweep is a progress bar
//     rather than a frozen page.
//   * this thread -- sequential claims. A few hundred short runs against the
//     tape the page already has, which is tens of milliseconds.
//
// What this file must never do is decide a verdict itself. It routes, it times,
// and it hands back what the evaluator said.

import type { ClaimPlanView } from "../gdsx-types.ts";
import type { DesignClient } from "../design/client.ts";
import type { Claim, Stimulus, Verdict, VerdictRecord } from "./model.ts";
import { budget } from "./verify.ts";
import {
  SAMPLE_STIMULI,
  checkConstraint,
  checkSequentialInvariant,
  checkTiming,
  type SimContext,
} from "./sequential.ts";
import type { VerifyReply, VerifyRequest } from "./verify-worker.ts";

export interface Estimate {
  /** What the form shows before you press verify: "22 leaves · exhaustive · 4.2M cases". */
  text: string;
  /** True when the best available verdict is LIKELY, so the form can say so
   *  BEFORE the player spends attention on it rather than after. */
  sampledOnly: boolean;
}

const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact" });

/** What this claim would cost and what it could come back as, before running. */
export function estimate(plan: ClaimPlanView): Estimate {
  if (plan.verdict) return { text: "structural · instant", sampledOnly: false };
  const job = plan.job!;
  if (job.engine === "sequential") {
    if (job.check === "timing") {
      return { text: "replay of one stimulus", sampledOnly: false };
    }
    return { text: `${SAMPLE_STIMULI} generated keys`, sampledOnly: true };
  }
  const free = job.design!.free.length;
  const found = budget(free);
  const noun = job.engine === "transition" ? "flops" : "leaves";
  return {
    text:
      `${free} ${noun} · ${found.method} · ${COMPACT.format(found.cases)} cases` +
      (found.inline ? "" : " · with a progress bar"),
    sampledOnly: found.clean === "LIKELY",
  };
}

export interface EngineContext {
  design: DesignClient;
  /** How to drive the design, from the live SimStore. */
  sim: () => SimContext;
  /** The sequence editor's current contents, snapshotted into a claim. */
  baseline: () => Stimulus;
}

export type ProgressFn = (percent: number) => void;

export class VerifyEngine {
  private worker: Worker | null = null;
  private nextId = 1;
  private readonly pending = new Map<
    number,
    { resolve: (v: Verdict) => void; onProgress?: ProgressFn }
  >();

  constructor(private readonly ctx: EngineContext) {}

  /** The plan alone, for the form's cost estimate before anything is run. */
  plan(claim: Claim): Promise<{ data: ClaimPlanView; call: string }> {
    return this.ctx.design.claimPlan(claim);
  }

  /**
   * Plan and settle one claim.
   *
   * The returned record is what goes in the notebook: the verdict, when it was
   * reached, the assumptions the plan was built under, and the `gdsx` call
   * behind it. Nothing is discarded on the way -- a claim verified under
   * assumptions is only honest if the notebook keeps which.
   */
  async verify(claim: Claim, onProgress?: ProgressFn): Promise<VerdictRecord> {
    const { data: plan, call } = await this.plan(claim);
    const verdict = plan.verdict
      ? fromLibrary(plan)
      : await this.settle(claim, plan, onProgress);
    return { verdict, at: Date.now(), notes: [...plan.notes], call: plan.call || call };
  }

  private settle(
    claim: Claim,
    plan: ClaimPlanView,
    onProgress?: ProgressFn,
  ): Promise<Verdict> {
    const job = plan.job!;
    if (job.engine === "sequential") {
      return Promise.resolve(this.sequential(claim, plan));
    }
    const request: VerifyRequest = {
      id: this.nextId++,
      check: job.check as VerifyRequest["check"],
      design: job.design!,
      claim: job.claim,
      value: "value" in claim ? claim.value : undefined,
      flopValue: claim.kind === "requirement" ? claim.flop_value : undefined,
      claimed: claim.kind === "support" ? claim.leaves : undefined,
      role: claim.kind === "role" ? claim.role : undefined,
    };
    return this.dispatch(request, onProgress);
  }

  /** Sequential checks run here: see sequential.ts's header for why. */
  private sequential(claim: Claim, plan: ClaimPlanView): Verdict {
    const sim = this.ctx.sim();
    if (claim.kind === "timing") {
      return checkTiming(sim, claim.net, claim.event, claim.cycles, claim.stimulus);
    }
    if (claim.kind === "constraint") {
      return checkConstraint(
        sim,
        claim.output,
        plan.job!.predicate!,
        claim.stimulus,
      );
    }
    if (claim.kind === "invariant") {
      return checkSequentialInvariant(
        sim,
        claim.net,
        claim.value,
        plan.job!.claim!,
        this.ctx.baseline(),
      );
    }
    return { kind: "UNKNOWN", reason: `no sequential check for a ${claim.kind} claim` };
  }

  private dispatch(request: VerifyRequest, onProgress?: ProgressFn): Promise<Verdict> {
    const worker = this.ensureWorker();
    return new Promise<Verdict>((resolve) => {
      this.pending.set(request.id, { resolve, onProgress });
      worker.postMessage(request);
    });
  }

  private ensureWorker(): Worker {
    if (this.worker) return this.worker;
    this.worker = new Worker(new URL("./verify-worker.ts", import.meta.url), {
      type: "module",
    });
    this.worker.onmessage = (event: MessageEvent<VerifyReply>) => {
      const reply = event.data;
      const waiting = this.pending.get(reply.id);
      if (!waiting) return;
      if (!reply.done) {
        waiting.onProgress?.(reply.progress);
        return;
      }
      this.pending.delete(reply.id);
      waiting.resolve(reply.verdict);
    };
    // A crashed evaluator must not leave a claim spinning forever with no
    // explanation -- every outstanding job gets an honest UNKNOWN instead.
    this.worker.onerror = (event) => {
      for (const [id, waiting] of this.pending) {
        waiting.resolve({
          kind: "UNKNOWN",
          reason: `the evaluator stopped: ${event.message || "unknown error"}`,
        });
        this.pending.delete(id);
      }
    };
    return this.worker;
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    this.pending.clear();
  }
}

/**
 * A verdict the library settled, widened into the notebook's own type.
 *
 * The library never returns `LIKELY` and never returns a counterexample vector
 * (gdsx/api.py says why), so those two arms are not reachable from here -- which
 * is exactly the property that makes a `PROVEN` arriving from Python
 * trustworthy: it can only have come from a graph fact.
 */
function fromLibrary(plan: ClaimPlanView): Verdict {
  const found = plan.verdict!;
  switch (found.kind) {
    case "PROVEN":
      return { kind: "PROVEN", method: "structural", cases: found.cases };
    case "DISPROVEN":
      return {
        kind: "DISPROVEN",
        counterexample: null,
        observed: found.observed,
        expected: found.expected,
      };
    default:
      return { kind: "UNKNOWN", reason: found.reason || "the library could not settle this" };
  }
}