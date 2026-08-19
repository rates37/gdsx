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
import type { InstanceView, NetView, ConeNode, RequirementsView } from "../gdsx-types";

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

  /** Every instance in the design; the netlist browser filters client-side. */
  instances(): Promise<Call<InstanceView[]>> {
    return this.run("instances", [this.handle], "gdsx.api.instances(handle)");
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