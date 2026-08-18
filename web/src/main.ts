import { wrap, type Remote } from "comlink";
import type { GdsxWorker, Envelope } from "./worker";

const log = document.getElementById("log") as HTMLPreElement;
function line(msg: string): void {
  log.textContent += msg + "\n";
  console.log(msg);
}

async function main(): Promise<void> {
  const t0 = performance.now();

  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const api = wrap<GdsxWorker>(worker);

  line("booting pyodide + micropip-installing gdsx wheel...");
  await api.ready();
  const tBoot = performance.now();
  line(`ready in ${(tBoot - t0).toFixed(0)} ms`);

  const netlistJson = await fetch("/samples/puzzle.netlist.json").then((r) => r.text());

  line("calling api.load_netlist(puzzle.netlist.json)...");
  const result = (await api.call("load_netlist", netlistJson)) as Envelope<{
    handle: string;
    top: string;
    instance_count: number;
    net_count: number;
    port_count: number;
  }>;
  const tCall = performance.now();

  line(`first api call in ${(tCall - t0).toFixed(0)} ms total (${(tCall - tBoot).toFixed(0)} ms for the call itself)`);
  line(JSON.stringify(result, null, 2));

  // Exposed for manual poking from the browser console per the task's
  // success criterion: `await api.call("load_netlist", ...)`.
  (globalThis as unknown as { api: Remote<GdsxWorker> }).api = api;
}

main().catch((err) => {
  line(`ERROR: ${err instanceof Error ? err.stack ?? err.message : String(err)}`);
});