// Copies build outputs that must be served same-origin: the Pyodide runtime,
// the gdsx wheel, and the baked puzzles. Run before `dev`/`build` so
// nothing here depends on a CDN.
//
// `--samples` additionally stages the original puzzle under public/samples/,
// which is a duplicate of public/puzzles/original-puzzle/ and exists only for
// local tooling -- see step 3. `npm run dev` and `npm run build:measure` pass
// it; `npm run build` does not, so the deployed bundle does not carry it.
import { cpSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { existsSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { REQUIRED, describe } from "./puzzle-index.mjs";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoDir = path.dirname(webDir);
const publicDir = path.join(webDir, "public");
const withSamples = process.argv.includes("--samples");

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

// The worker has to know the wheel's filename to micropip-install it, and
// that filename embeds the version from pyproject.toml. It used to be
// spelled out in web/src/worker.ts, where a `uv version --bump` broke the
// running app while every build and test stayed green. Write it down here
// instead -- this script is the only thing that knows the real name,
// because it is the thing that copied it. The worker fetches this file.
//
// A manifest rather than a directory listing on purpose: GitHub Pages does
// not serve one, so there is nothing to glob or parse at runtime.
//
// `wheels` is sorted only by readdir order; if a stale wheel from an older
// version is still sitting in ../dist, pick the newest by mtime so the
// manifest names the wheel that was just built.
const newest = wheels
  .map((name) => ({ name, mtime: statSync(path.join(distDir, name)).mtimeMs }))
  .sort((a, b) => b.mtime - a.mtime)[0].name;
writeFileSync(
  path.join(wheelOut, "manifest.json"),
  JSON.stringify({ schema_version: 1, wheel: newest }, null, 2) + "\n",
);

// 3. Local-tooling copies of the original puzzle -> public/samples/: the
// baked netlist, the baked render bundle the die view draws (`uv run python
// scripts/bake_render.py`), the baked gate tape the waveform and sequence
// editor run (`uv run python scripts/bake_tape.py`), and the GDS itself so
// re-extraction in the browser can be timed.
//
// **No player path fetches any of these.** The original puzzle reaches the
// game through step 4 like every other level; the only reader of /samples/
// in the app is `spike.analyseFromGds()` in src/boot.ts, which fetches
// samples/puzzle.gds and is reachable only from `globalThis.spike`, i.e.
// only from scripts/measure-m0.mjs. So these six-odd MB were pure weight in
// a deployed bundle, and they are now opt-in: `npm run dev` and `npm run
// build:measure` pass --samples, `npm run build` does not.
//
// The rmSync is not tidiness. public/ persists between runs, and Vite copies
// whatever is in it into dist -- so without it, a `npm run dev` followed by
// a `npm run build` would ship the samples anyway, and the saving would
// depend on which command you happened to run last.
const samplesOut = path.join(publicDir, "samples");
rmSync(samplesOut, { recursive: true, force: true });
if (withSamples) {
  for (const name of [
    "puzzle.netlist.json",
    "puzzle.render.bin",
    "puzzle.tape.bin",
    "puzzle.gds",
  ]) {
    const src = path.join(repoDir, "samples", name);
    if (existsSync(src)) {
      copy(src, path.join(samplesOut, name));
    } else {
      console.warn(`warning: ${src} not found, skipping`);
    }
  }
}

// 4. Every baked puzzle -> public/puzzles/<dir>/, plus the index the level
// picker reads. This is the multi-puzzle path, and the one the game plays
// from: everything a player downloads comes from here.
//
// The /samples/ copies above overlap this directory -- original-puzzle is
// the same design -- and used to be written unconditionally, on the grounds
// that the duplication was dev-only cost. It was not: it was in the deployed
// bundle. They are now behind --samples, because the two things that do need
// them are both local tooling. `scripts/measure-m0.mjs` reads
// dist/samples/puzzle.render.bin by that exact path and drives
// `spike.analyseFromGds()`, which fetches /samples/puzzle.gds; and the node
// test scripts read the repo's samples/ directory directly, never public/.
// Keeping the path rather than repointing the measurement keeps the M0
// numbers comparable to every earlier run.
//
// solution.json is NOT copied. Only the driver protocol derived from it goes
// into index.json -- see puzzle-index.mjs for why. hints.json is also not
// copied as a file, but unlike solution.json it is meant to reach the
// player, so its tiers go into index.json verbatim rather than nothing.
const puzzlesDir = path.join(repoDir, "puzzles");
const puzzlesOut = path.join(publicDir, "puzzles");
rmSync(puzzlesOut, { recursive: true, force: true });
mkdirSync(puzzlesOut, { recursive: true });

const entries = existsSync(puzzlesDir)
  ? readdirSync(puzzlesDir)
      .filter((name) => statSync(path.join(puzzlesDir, name)).isDirectory())
      .sort()
  : [];

const index = [];
for (const dir of entries) {
  const src = path.join(puzzlesDir, dir);
  const missing = REQUIRED.filter((f) => !existsSync(path.join(src, f)));
  if (missing.length > 0) {
    console.warn(`warning: puzzles/${dir} is missing ${missing.join(", ")}, not offering it`);
    continue;
  }
  const read = (f) => JSON.parse(readFileSync(path.join(src, f), "utf8"));
  for (const name of ["netlist.json", "render.bin", "tape.bin"]) {
    copy(path.join(src, name), path.join(puzzlesOut, dir, name));
  }
  index.push(describe(dir, read("manifest.json"), read("solution.json"), read("hints.json")));
}

writeFileSync(
  path.join(puzzlesOut, "index.json"),
  JSON.stringify({ schema_version: 1, puzzles: index }, null, 2) + "\n",
);

console.log(
  `Synced pyodide runtime, gdsx wheel(s), and puzzle assets into public/ ` +
    `(${index.length} puzzle(s): ${index.map((p) => p.id).join(", ")})` +
    `${withSamples ? " + samples/ for local tooling" : ""}`,
);