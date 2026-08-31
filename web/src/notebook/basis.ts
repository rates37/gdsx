// Coverage's denominator (§5): the flop vocabulary and the nets of the
// success cone. Shared by boot.ts, which needs it to score a submission, and
// the Notebook panel, which needs it to show the live coverage line -- one
// cone call, not two, and neither has to wait on the other's.

import type { DesignClient } from "../design/client";

export interface CoverageBasis {
  flops: string[];
  coneNets: string[];
}

/** Every net named anywhere in a cone tree. */
function collect(node: {
  net: string;
  children: { net: string; children: unknown[] }[];
}): string[] {
  const found: string[] = [];
  const stack = [node];
  while (stack.length) {
    const current = stack.pop() as typeof node;
    found.push(current.net);
    for (const child of current.children) stack.push(child as typeof node);
  }
  return [...new Set(found)];
}

const cache = new WeakMap<DesignClient, Promise<CoverageBasis>>();

async function computeBasis(
  design: DesignClient,
  successNet: string | null,
): Promise<CoverageBasis> {
  const vocab = await design.claimVocabulary();
  const flops = vocab.data.flops;

  // One cone call at load, for the coverage denominator. Not recomputed per
  // claim -- coverage is a progress bar, not an analysis.
  //
  // The step through the flop is not optional. `success` is a port driven by
  // a flop, so its own fan-in cone is a single `flop_q` leaf and a
  // denominator of 1 would read 100% explained off one claim. What "the
  // success cone" means is the cone of the flop's D, one cycle earlier -- the
  // same step the cone walker makes explicit.
  let coneNets: string[] = [];
  try {
    const lock = successNet;
    // No lock, no success cone: coverage falls back to the claims alone
    // rather than being measured against an invented net.
    if (lock === null) throw new Error("this puzzle declares no lock");
    let root = lock;
    const head = await design.cone(root, { depth: 1 });
    if (head.data.leaf === "flop_q") {
      const stepped = await design.flopDNet(root);
      if (stepped.data.net) root = stepped.data.net;
    }
    const cone = await design.cone(root, { depth: 40 });
    // The port itself counts as part of its own cone: a claim about
    // `success` is a claim about the success cone in any sense a player
    // means it, even though the D-cone walk starts below it.
    coneNets = [...new Set([lock, ...collect(cone.data)])];
  } catch {
    coneNets = [];
  }

  return { flops, coneNets };
}

/**
 * The flop vocabulary and the nets of the success cone -- everything
 * `coverage()` needs as its denominator, per §5. Memoised per design handle:
 * boot.ts starts this as soon as the design is ready so a solve is never
 * scored against an unmeasured cone, and the Notebook panel awaits the same
 * promise to show the live coverage line, rather than each computing its own.
 */
export function coverageBasis(
  design: DesignClient,
  successNet: string | null,
): Promise<CoverageBasis> {
  const cached = cache.get(design);
  if (cached) return cached;
  const basis = computeBasis(design, successNet);
  cache.set(design, basis);
  return basis;
}