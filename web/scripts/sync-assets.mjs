// Copies build outputs that must be served same-origin: the Pyodide runtime,
// the gdsx wheel, and the baked puzzle netlist. Run before `dev`/`build` so
// nothing here depends on a CDN.
import { cpSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoDir = path.dirname(webDir);
const publicDir = path.join(webDir, "public");

function copy(src, dest) {
  mkdirSync(path.dirname(dest), { recursive: true });
  cpSync(src, dest, { recursive: true });
}

// 1. Pyodide runtime (wasm + interpreter + stdlib) -> public/pyodide/
const pyodidePkg = path.join(webDir, "node_modules", "pyodide");
const pyodideOut = path.join(publicDir, "pyodide");
rmSync(pyodideOut, { recursive: true, force: true });
mkdirSync(pyodideOut, { recursive: true });
const wanted = [
  "pyodide.mjs",
  "pyodide.asm.js",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
];
for (const name of wanted) {
  const src = path.join(pyodidePkg, name);
  if (existsSync(src)) copy(src, path.join(pyodideOut, name));
}
// Ship the pure-Python wheels Pyodide needs at runtime (pyyaml, gdsx's one
// hard dependency) from a local cache, so micropip never has to reach a CDN
// for them either. See scripts/fetch-pyyaml.mjs for how the cache is filled;
// its sha256 must match the `pyyaml` entry in pyodide-lock.json above.
const extraDir = path.join(webDir, "node_modules", ".pyodide-extra");
if (existsSync(extraDir)) {
  for (const name of readdirSync(extraDir)) {
    if (name.endsWith(".whl")) copy(path.join(extraDir, name), path.join(pyodideOut, name));
  }
} else {
  console.warn(
    "warning: node_modules/.pyodide-extra missing pyyaml wheel; micropip will fall back to jsdelivr for it",
  );
}

// 2. The gdsx wheel -> public/wheel/
const distDir = path.join(repoDir, "dist");
const wheelOut = path.join(publicDir, "wheel");
rmSync(wheelOut, { recursive: true, force: true });
mkdirSync(wheelOut, { recursive: true });
const wheels = existsSync(distDir)
  ? readdirSync(distDir).filter((f) => f.endsWith(".whl"))
  : [];
if (wheels.length === 0) {
  throw new Error(
    "No wheel found in ../dist. Run `uv build` at the repo root first.",
  );
}
for (const wheel of wheels) {
  copy(path.join(distDir, wheel), path.join(wheelOut, wheel));
}

// 3. Baked puzzle netlist -> public/samples/
const sampleSrc = path.join(repoDir, "samples", "puzzle.netlist.json");
const sampleOut = path.join(publicDir, "samples", "puzzle.netlist.json");
if (existsSync(sampleSrc)) {
  copy(sampleSrc, sampleOut);
} else {
  console.warn(`warning: ${sampleSrc} not found, skipping`);
}

console.log("Synced pyodide runtime, gdsx wheel(s), and puzzle netlist into public/");