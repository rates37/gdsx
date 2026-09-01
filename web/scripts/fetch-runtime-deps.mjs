// Populate node_modules/.pyodide-extra with the pure-Python wheels the worker
// needs beyond the core Pyodide runtime -- micropip and its dependency
// `packaging` (needed to install anything at all), plus pyyaml (gdsx's one
// hard runtime dependency). Fetched from Pyodide's own package CDN and
// sha256-verified against pyodide-lock.json, then served same-origin by
// scripts/sync-assets.mjs so the shipped page never reaches a CDN itself.
//
// Needs network, so it is not part of `predev`/`prebuild`. It is a step of
// `npm run setup`, which is the supported way to run it; sync-assets refuses
// to build if its output is missing or belongs to an older `pyodide`, so a
// skipped run cannot reach a browser.
//
//     node scripts/fetch-runtime-deps.mjs              # always re-download
//     node scripts/fetch-runtime-deps.mjs --if-needed  # no-op when staged
import { mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { extraDir, missingWheels, requiredWheels } from "./pyodide-extra.mjs";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const ifNeeded = process.argv.includes("--if-needed");

const wheels = ifNeeded ? missingWheels(webDir) : requiredWheels(webDir);
if (wheels.length === 0) {
  console.log(
    `Pyodide extra wheels already staged for pyodide ${requiredWheels(webDir)[0].version}.`,
  );
} else {
  const outDir = extraDir(webDir);
  mkdirSync(outDir, { recursive: true });
  for (const { name, file_name, sha256, version } of wheels) {
    const url = `https://cdn.jsdelivr.net/pyodide/v${version}/full/${file_name}`;
    console.log(`Fetching ${url}`);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`fetch failed for ${name}: ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    const digest = createHash("sha256").update(buf).digest("hex");
    if (digest !== sha256) {
      throw new Error(`${name}: sha256 mismatch (expected ${sha256}, got ${digest})`);
    }
    const outFile = path.join(outDir, file_name);
    writeFileSync(outFile, buf);
    console.log(`Wrote ${outFile} (sha256 verified against pyodide-lock.json)`);
  }
}