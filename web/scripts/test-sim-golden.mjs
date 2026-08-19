// Runs the TypeScript gate-tape executor against the golden traces recorded
// in tests/golden/trace-*.json (see scripts/tape_golden.py at the repo
// root) -- the contract that keeps the Python and TypeScript executors from
// ever drifting. Deliberately outside web/src/: it runs under plain Node
// against samples/*.tape.bin on disk, not bundled by Vite, and has no
// business inside the browser source tree's tsconfig project.
//
// Usage: node --experimental-strip-types web/scripts/test-sim-golden.mjs
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseTapeBundle } from "../src/sim/tape.ts";
import { GateTapeExecutor } from "../src/sim/executor.ts";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootDir = path.dirname(webDir);
const goldenDir = path.join(rootDir, "tests", "golden");

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

/** sha256 of the named nets' values, in name order -- matches tape_golden.py's `_digest`. */
function digest(valuesByName, order) {
  const text = order.map((net) => String(valuesByName[net])).join("");
  return createHash("sha256").update(text, "ascii").digest("hex");
}

function checkSample(name) {
  const trace = JSON.parse(readFileSync(path.join(goldenDir, `trace-${name}.json`), "utf8"));
  const bin = readFileSync(path.join(rootDir, "samples", `${name}.tape.bin`));
  const tape = parseTapeBundle(toArrayBuffer(bin));

  if (tape.header.tape_version !== trace.tape_version) {
    throw new Error(`${name}: tape_version ${tape.header.tape_version} != golden ${trace.tape_version}`);
  }
  if (tape.header.flop_names.join(",") !== trace.flop_names.join(",")) {
    throw new Error(`${name}: flop_names order does not match the golden trace`);
  }

  const order = Object.keys(tape.header.names).sort();
  const executor = new GateTapeExecutor(tape);
  executor.reset();

  for (let cycle = 0; cycle < trace.cycles; cycle++) {
    const bits = trace.vectors[cycle];
    const inputs = {};
    trace.ports.forEach((port, i) => (inputs[port] = Number(bits[i])));
    executor.step(inputs);

    const gotFlops = Array.from(executor.state).join("");
    if (gotFlops !== trace.flops[cycle]) {
      throw new Error(
        `${name}: flop state diverges at cycle ${cycle}\n  golden: ${trace.flops[cycle]}\n  got:    ${gotFlops}`,
      );
    }
    const gotDigest = digest(executor.valuesByName(), order);
    if (gotDigest !== trace.nets[cycle]) {
      throw new Error(`${name}: net digest diverges at cycle ${cycle}`);
    }
  }
  console.log(`${name}: ${trace.cycles} cycles match the golden trace bit-for-bit`);
}

for (const name of ["sample", "puzzle"]) checkSample(name);
console.log("OK -- the TS and Python gate-tape executors agree");