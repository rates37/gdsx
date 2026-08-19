// A typed wrapper over `gdsx.api`, the JSON facade every Python-backed panel
// goes through (worker.ts's `call`). Every method returns both the decoded
// data and the exact `gdsx.api.*` call that produced it, in the form a
// player could paste into a REPL bound to `handle` -- that string is what
// every panel's `{ }` button shows.
//
// Deliberately thin: this is not a second copy of the netlist model. Every
// question here -- what drives a net, what a cone looks like, what a cone
// flattens to -- is answered by a single `gdsx.api` round trip, never
// recomputed client-side. Re-deriving graph traversal in TypeScript is
// exactly the mistake the library's own refactor guide calls out.

import type { Remote } from "comlink";
import type { GdsxWorker, Envelope } from "../worker";
import type {
  InstanceView,
  NetView,
  ConeNode,
  RequirementsView,
  ClaimPlanView,
  StickyView,
  WeightView,
  OrbitView,
  SystemView,
  SliceView,
} from "../gdsx-types";

export interface Call<T> {
  data: T;
  /** The `gdsx.api` call that produced `data`, e.g. `gdsx.api.cone(handle, "success", depth=6, direction="in", through_flops=False)`. */
  call: string;
}

export interface FlopDNet {
  instance: string;
  net: string | null;
}

export class DesignError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

function pyStr(v: unknown): string {
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "boolean") return v ? "True" : "False";
  if (v === null || v === undefined) return "None";
  return String(v);
}

export class DesignClient {
  constructor(
    private readonly api: Remote<GdsxWorker>,
    readonly handle: string,
    readonly top: string,
  ) {}

  private async run<T>(name: string, args: unknown[], call: string): Promise<Call<T>> {
    const env = (await this.api.call(name, ...args)) as Envelope<T>;
    if (!env.ok) {
      const err = env.error!;
      throw new DesignError(err.code, err.message, err.detail);
    }
    return { data: env.data as T, call };
  }

  /** Every instance in the design (the netlist browser filters client-side),
   *  or just `names` when given -- a lookup for the odd panel that only
   *  wants a handful, e.g. one flop's `Q` net before a `flopDNet` step. */
  instances(names?: string[]): Promise<Call<InstanceView[]>> {
    if (names === undefined) {
      return this.run("instances", [this.handle], "gdsx.api.instances(handle)");
    }
    const json = JSON.stringify(names);
    const call = `gdsx.api.instances(handle, ${pyStr(json)})`;
    return this.run("instances", [this.handle, json], call);
  }

  /** Every net in the design, with its driver, readers and leaf classification. */
  nets(): Promise<Call<NetView[]>> {
    return this.run("nets", [this.handle], "gdsx.api.nets(handle)");
  }

  cone(
    net: string,
    opts: { depth?: number; direction?: "in" | "out"; throughFlops?: boolean } = {},
  ): Promise<Call<ConeNode>> {
    const depth = opts.depth ?? 3;
    const direction = opts.direction ?? "in";
    const throughFlops = opts.throughFlops ?? false;
    const call =
      `gdsx.api.cone(handle, ${pyStr(net)}, depth=${depth}, ` +
      `direction=${pyStr(direction)}, through_flops=${pyStr(throughFlops)})`;
    return this.run("cone", [this.handle, net, depth, direction, throughFlops], call);
  }

  /** The flatten-AND/OR-tree operation: what forces `net == value`, and what merely narrows it to a choice. */
  requirements(net: string, value: 0 | 1 = 1): Promise<Call<RequirementsView>> {
    const call = `gdsx.api.requirements(handle, ${pyStr(net)}, value=${value})`;
    return this.run("requirements", [this.handle, net, value], call);
  }

  /** Step through the flop driving `net` (must be a flop's Q net): the net on its D pin, one cycle earlier. */
  flopDNet(net: string): Promise<Call<FlopDNet>> {
    const call = `gdsx.api.flop_d_net(handle, ${pyStr(net)})`;
    return this.run("flop_d_net", [this.handle, net], call);
  }

  /** The self-contained mini-tape for `net`'s own cone -- free leaves plus
   *  the ops to compute it from them. Reused by the notebook's `function`
   *  claim and by any panel that wants to exhaustively (or, past the
   *  ceiling, by sampling) enumerate a truth table without re-deriving the
   *  cone-cutting itself. */
  coneSlice(net: string): Promise<Call<SliceView>> {
    const call = `gdsx.api.cone_slice(handle, ${pyStr(net)})`;
    return this.run("cone_slice", [this.handle, net], call);
  }

  /** What it would take to settle one notebook claim: a verdict, or the work.
   *
   * Structural claims come back settled -- their evidence is a fact about the
   * netlist. Everything else comes back as a job of tape slices for the
   * notebook's evaluator, because settling it means running a cone over up to
   * 2**24 assignments and that does not belong in Pyodide. */
  claimPlan(claim: unknown): Promise<Call<ClaimPlanView>> {
    const json = JSON.stringify(claim);
    const call = `gdsx.api.claim_plan(handle, ${pyStr(json)})`;
    return this.run("claim_plan", [this.handle, json], call);
  }

  /** The lists the claim forms build their dropdowns from, so the UI does not
   *  keep its own copy of what a role or an event may be. */
  claimVocabulary(): Promise<Call<ClaimVocabulary>> {
    return this.run("claim_vocabulary", [this.handle], "gdsx.api.claim_vocabulary(handle)");
  }

  /** What kind of thing each register is: role, width, reset value. */
  registers(): Promise<Call<RegistersResult>> {
    return this.run("registers", [this.handle], "gdsx.api.registers(handle)");
  }

  /** What has to be true for each register to change. */
  guards(minFanout = 4): Promise<Call<GuardsResult>> {
    const call = `gdsx.api.guards(handle, min_fanout=${minFanout})`;
    return this.run("guards", [this.handle, minFanout], call);
  }

  /** Every flop whose D pin latches on its own Q (L37). Never says checkpoint
   *  or trap -- stickiness alone does not determine which. */
  sticky(): Promise<Call<{ sticky: StickyView[] }>> {
    return this.run("sticky", [this.handle], "gdsx.api.sticky(handle)");
  }

  /** Split flops into groups by mutual D-cone reference (L34): assumption-free,
   *  no eyeballing which flops pair up. */
  grouping(flops: string[]): Promise<Call<{ groups: string[][] }>> {
    const json = JSON.stringify(flops);
    const call = `gdsx.api.grouping(handle, ${pyStr(json)})`;
    return this.run("grouping", [this.handle, json], call);
  }

  /** Infer each flop in a group's binary weight by observation (L35).
   *  `confidence` is load-bearing -- "observed" and "by_elimination" are not
   *  the same claim about the world, and "unknown" means the value is a
   *  sentinel, not a weight. */
  weights(
    group: string[],
    stimulus: Record<string, number>,
    cycles: number,
  ): Promise<Call<{ weights: Record<string, WeightView> }>> {
    const groupJson = JSON.stringify(group);
    const stimulusJson = JSON.stringify(stimulus);
    const call =
      `gdsx.api.weights(handle, ${pyStr(groupJson)}, ${pyStr(stimulusJson)}, ` +
      `cycles=${cycles})`;
    return this.run("weights", [this.handle, groupJson, stimulusJson, cycles], call);
  }

  /** Which control-flop values make `group` react to `perturb` at all (L36):
   *  an address decoder's selected address, found without naming the gate. */
  decodeSelects(
    group: string[],
    control: string[],
    baseline: Record<string, number>,
    perturb: Record<string, number>,
  ): Promise<Call<{ hits: Record<string, number>[] }>> {
    const groupJson = JSON.stringify(group);
    const controlJson = JSON.stringify(control);
    const baselineJson = JSON.stringify(baseline);
    const perturbJson = JSON.stringify(perturb);
    const call =
      `gdsx.api.decode_selects(handle, ${pyStr(groupJson)}, ${pyStr(controlJson)}, ` +
      `${pyStr(baselineJson)}, ${pyStr(perturbJson)})`;
    return this.run(
      "decode_selects",
      [this.handle, groupJson, controlJson, baselineJson, perturbJson],
      call,
    );
  }

  /** Apply a stimulus repeatedly from `start` and classify the resulting state
   *  sequence (L36): counter, saturating, wrapping, shift, or fixed-point. */
  decodeOrbit(
    group: string[],
    stimulus: Record<string, number>,
    start: Record<string, number> | null = null,
  ): Promise<Call<OrbitView>> {
    const groupJson = JSON.stringify(group);
    const stimulusJson = JSON.stringify(stimulus);
    const startJson = start === null ? null : JSON.stringify(start);
    const call =
      `gdsx.api.decode_orbit(handle, ${pyStr(groupJson)}, ${pyStr(stimulusJson)}, ` +
      `${startJson === null ? "None" : pyStr(startJson)})`;
    return this.run(
      "decode_orbit",
      [this.handle, groupJson, stimulusJson, startJson],
      call,
    );
  }

  /** Build a constraint `System` from measured hits, one `exact` row per
   *  target (L38). This does not need a design handle -- it is pure
   *  combinatorics over cycle numbers the caller already measured. */
  constraintsBuild(
    hits: Record<string, number[]>,
    watched: string[],
    targets: Record<string, number>,
  ): Promise<Call<SystemView>> {
    const hitsJson = JSON.stringify(hits);
    const watchedJson = JSON.stringify(watched);
    const targetsJson = JSON.stringify(targets);
    const call =
      `gdsx.api.constraints_build(${pyStr(hitsJson)}, ${pyStr(watchedJson)}, ` +
      `${pyStr(targetsJson)})`;
    return this.run("constraints_build", [hitsJson, watchedJson, targetsJson], call);
  }

  /** Add one row to a `System` (L38). */
  constraintsAdd(
    system: SystemView,
    constraint: { name: string; elements: number[]; lb: number; ub: number },
  ): Promise<Call<SystemView>> {
    const systemJson = JSON.stringify(system);
    const constraintJson = JSON.stringify(constraint);
    const call = `gdsx.api.constraints_add(${pyStr(systemJson)}, ${pyStr(constraintJson)})`;
    return this.run("constraints_add", [systemJson, constraintJson], call);
  }

  /** Solutions to a `System`, up to `limit` (L38): "a solution", or "how many
   *  exist" when `capped` comes back true and the true count is unknown. */
  constraintsSolve(
    system: SystemView,
    method: "dfs" | "ilp" = "dfs",
    limit = 50,
  ): Promise<Call<{ solutions: number[][]; capped: boolean }>> {
    const systemJson = JSON.stringify(system);
    const call =
      `gdsx.api.constraints_solve(${pyStr(systemJson)}, method=${pyStr(method)}, ` +
      `limit=${limit})`;
    return this.run("constraints_solve", [systemJson, method, limit], call);
  }
}

export interface RegistersResult {
  registers: Array<{
    name: string;
    flops: string[];
    width: number;
    description: string;
    [key: string]: unknown;
  }>;
  roles: unknown;
  pipelines: unknown;
}

export interface GuardsResult {
  guards: Array<{ condition: string; [key: string]: unknown }>;
  candidates: string[];
  flops: string[];
  ungated: string[];
  groups: Array<{ condition: Array<{ net: string; value: number }>; flops: string[] }>;
}

export interface ClaimVocabulary {
  kinds: string[];
  roles: string[];
  events: string[];
  measures: Record<string, string>;
  flops: string[];
  inputs: string[];
  outputs: string[];
}

/** Loads the baked netlist and opens a design handle against it. */
export async function createDesignClient(
  api: Remote<GdsxWorker>,
  netlistJsonUrl: string,
): Promise<DesignClient> {
  const netlistJson = await fetch(netlistJsonUrl).then((r) => r.text());
  const env = (await api.call("load_netlist", netlistJson)) as Envelope<{
    handle: string;
    top: string;
  }>;
  if (!env.ok) {
    const err = env.error!;
    throw new DesignError(err.code, err.message, err.detail);
  }
  return new DesignClient(api, env.data!.handle, env.data!.top);
}