// One-time, manual: populate node_modules/.pyodide-extra with the pure-Python
// wheels the worker needs beyond the core Pyodide runtime -- micropip and its
// dependency `packaging` (needed to install anything at all), plus pyyaml
// (gdsx's one hard runtime dependency). Fetched from Pyodide's own package
// CDN and sha256-verified against pyodide-lock.json, then served same-origin
// by scripts/sync-assets.mjs so the shipped page never reaches a CDN itself.
//
// Not run automatically (needs network). Run by hand after `npm install`, or
// whenever the `pyodide` npm package version changes.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const lock = JSON.parse(
  readFileSync(path.join(webDir, "node_modules/pyodide/pyodide-lock.json"), "utf8"),
);
const { version } = lock.info;
const outDir = path.join(webDir, "node_modules", ".pyodide-extra");
mkdirSync(outDir, { recursive: true });

const wanted = ["micropip", "packaging", "pyyaml"];

for (const name of wanted) {
  const entry = lock.packages[name];
  if (!entry) throw new Error(`${name} not found in pyodide-lock.json`);
  const url = `https://cdn.jsdelivr.net/pyodide/v${version}/full/${entry.file_name}`;
  console.log(`Fetching ${url}`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetch failed for ${name}: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const digest = createHash("sha256").update(buf).digest("hex");
  if (digest !== entry.sha256) {
    throw new Error(`${name}: sha256 mismatch (expected ${entry.sha256}, got ${digest})`);
  }
  const outFile = path.join(outDir, entry.file_name);
  writeFileSync(outFile, buf);
  console.log(`Wrote ${outFile} (sha256 verified against pyodide-lock.json)`);
}