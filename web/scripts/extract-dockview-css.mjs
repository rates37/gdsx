// dockview-core's npm "module" entry (dist/package/main.esm.mjs) -- the one
// `import "dockview-core"` resolves to -- does not inject its stylesheet.
// Only the standalone browser bundle (dist/dockview-core.js) does, as a
// `document.createElement("style")` side effect baked into that file. There
// is no separate .css file shipped at all in this package version.
//
// Rather than pull in the whole browser bundle (UMD, meant for a <script>
// tag, not an ESM import) just for its CSS, this pulls the same string out
// and writes it as a normal .css file that Vite handles the usual way.
//
// Re-run this after bumping the `dockview-core` version:
//   node scripts/extract-dockview-css.mjs
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const bundlePath = path.join(webDir, "node_modules/dockview-core/dist/dockview-core.js");
const outPath = path.join(webDir, "src/workspace/dockview.css");

const src = readFileSync(bundlePath, "utf8");
const marker = 's.textContent = "';
const start = src.indexOf(marker) + marker.length - 1;
if (start < marker.length - 1) {
  throw new Error(`could not find the injected stylesheet in ${bundlePath}`);
}
let i = start + 1;
while (true) {
  if (src[i] === "\\") {
    i += 2;
    continue;
  }
  if (src[i] === '"') break;
  i++;
}
const css = JSON.parse(src.slice(start, i + 1));

const header =
  "/* GENERATED FILE -- do not edit by hand.\n" +
  " * Run `node scripts/extract-dockview-css.mjs` to regenerate.\n" +
  " * Source: node_modules/dockview-core/dist/dockview-core.js (see that script for why). */\n\n";
writeFileSync(outPath, header + css);
console.log(`wrote ${css.length} chars of dockview CSS to ${path.relative(webDir, outPath)}`);