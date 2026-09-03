// M0 gate measurements, driven through a real browser.
//
//   node scripts/measure-m0.mjs [--headless] [--url http://localhost:4173/?puzzle=…]
//
//   1. total transfer for Pyodide + the gdsx wheel (raw and brotli),
//   2. wall-clock api.analyse() on puzzle.gds inside Pyodide,
//   3. frame rate at full-die zoom, per LOD, while panning.
//
// Run `npm run build:measure` first; this drives the built bundle, not the
// dev server. It has to be `build:measure` rather than plain `build`: the
// measurement reads dist/samples/, which the production build deliberately
// leaves out (it is a duplicate of dist/puzzles/original-puzzle/ that no
// player path fetches -- see scripts/sync-assets.mjs step 3).
import { chromium } from "playwright";
import { brotliCompressSync, gzipSync, constants } from "node:zlib";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const distDir = path.join(webDir, "dist");

const args = process.argv.slice(2);
const headless = args.includes("--headless");
const urlArg = args.indexOf("--url");
// The `?puzzle=` is required, not decoration: a bare URL opens the level menu,
// which loads no render bundle and defines no `globalThis.spike` for the frame
// measurements below to read. Pinned to the original puzzle for the same
// reason `analyseFromGds` is -- comparing a measurement across runs only means
// anything if it is the same design every time.
const url = urlArg >= 0 ? args[urlArg + 1] : "http://localhost:4173/?puzzle=original-puzzle";

const MB = (n) => `${(n / 1e6).toFixed(2)} MB`;

// Fail here with the fix rather than three minutes later on a browser
// timeout or a statSync throw at the last line of the report.
if (!existsSync(path.join(distDir, "samples", "puzzle.render.bin"))) {
  console.error(
    "dist/samples/ is missing -- this measurement needs it.\n" +
      "Run `npm run build:measure` (not `npm run build`, which excludes it).",
  );
  process.exit(1);
}

// ---- 1. on-disk transfer accounting ----------------------------------------

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

function compressed(file) {
  const raw = readFileSync(file);
  return {
    raw: raw.length,
    gzip: gzipSync(raw, { level: 9 }).length,
    // GitHub Pages / any static CDN serves brotli; text quality 11 is what
    // a pre-compressed deploy would ship.
    brotli: brotliCompressSync(raw, {
      params: { [constants.BROTLI_PARAM_QUALITY]: 11 },
    }).length,
  };
}

function categorise(rel) {
  if (rel.startsWith("pyodide/")) return "pyodide runtime";
  if (rel.startsWith("wheel/")) return "gdsx wheel";
  if (rel.includes("puzzle.render.bin")) return "render.bin";
  if (rel.includes("puzzle.netlist.json")) return "netlist.json";
  if (rel.includes("puzzle.gds")) return "puzzle.gds (re-extract only)";
  return "app shell (js/html)";
}

function diskReport() {
  const totals = new Map();
  for (const file of walk(distDir)) {
    const rel = path.relative(distDir, file).split(path.sep).join("/");
    const cat = categorise(rel);
    const c = compressed(file);
    const t = totals.get(cat) ?? { raw: 0, gzip: 0, brotli: 0, files: 0 };
    t.raw += c.raw;
    t.gzip += c.gzip;
    t.brotli += c.brotli;
    t.files++;
    totals.set(cat, t);
  }
  return totals;
}

// ---- 2 + 3. browser measurements -------------------------------------------

async function browserReport() {
  const browser = await chromium.launch({
    headless,
    args: [
      "--enable-unsafe-swiftshader",
      "--ignore-gpu-blocklist",
      "--enable-gpu-rasterization",
    ],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on("console", (m) => {
    if (m.type() === "error") console.error("  [page error]", m.text());
  });
  page.on("pageerror", (e) => console.error("  [page exception]", e.message));
  page.on("response", (r) => {
    if (r.status() >= 400) console.error(`  [http ${r.status()}]`, r.url());
  });

  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => "spike" in globalThis, null, { timeout: 60_000 });

  const renderer = await page.evaluate(() => globalThis.spike.rendererInfo);
  const bundleBytes = await page.evaluate(() => globalThis.spike.bundleBytes);

  // --- frame rate. Full-die zoom, panning, one pass per LOD.
  const fps = {};
  for (const lod of [0, 1, 2, "auto"]) {
    await page.evaluate((l) => {
      globalThis.spike.fit();
      globalThis.spike.setLod(l);
      globalThis.spike.resetMeter();
    }, lod);
    // Pan across the die for three seconds so this measures pan/zoom cost,
    // not a static frame the driver could be caching.
    await page.mouse.move(800, 500);
    await page.mouse.down();
    const t0 = Date.now();
    let i = 0;
    while (Date.now() - t0 < 3000) {
      i++;
      await page.mouse.move(800 + 220 * Math.sin(i / 6), 500 + 140 * Math.cos(i / 7));
      await page.waitForTimeout(16);
    }
    await page.mouse.up();
    const f = await page.evaluate(() => globalThis.spike.frame());

    // rAF is vsync-capped, so a comfortable scene and one with no headroom
    // both read 120 fps. Multiply the per-frame load until it drops below 60
    // to find how many times over this scene fits in a frame.
    f.headroom = 1;
    for (const k of [2, 4, 8, 16, 32, 64, 128, 256]) {
      await page.evaluate((n) => globalThis.spike.setRepeat(n), k);
      await page.waitForTimeout(1200);
      const got = await page.evaluate(() => globalThis.spike.frame());
      if (got.fps < 60) break;
      f.headroom = k;
    }
    await page.evaluate(() => globalThis.spike.setRepeat(1));
    fps[String(lod)] = f;
  }

  // --- screenshots: an fps number means nothing if the picture is wrong
  const shots = path.join(webDir, "m0-shots");
  await page.evaluate(() => {
    globalThis.spike.fit();
    globalThis.spike.setLod("auto");
  });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(shots, "1-full-die-auto-lod.png") });
  await page.evaluate(() => globalThis.spike.setLod(0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(shots, "2-full-die-lod0.png") });
  // Zoom in on the middle of the die, full detail. Eight notches is about
  // 7x; much more than that and the viewport is a micron across, which lands
  // between wires and photographs as an empty screen.
  await page.mouse.move(800, 430);
  for (let i = 0; i < 8; i++) await page.mouse.wheel(0, -120);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(shots, "3-zoomed-lod0.png") });
  // Layer toggles: met1 only.
  await page.evaluate(() => {
    for (const box of document.querySelectorAll("#layer-list input")) {
      const on = box.parentElement.textContent.startsWith("met1");
      if (box.checked !== on) box.click();
    }
  });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(shots, "4-zoomed-met1-only.png") });
  await page.evaluate(() => {
    for (const box of document.querySelectorAll("#layer-list input")) {
      if (!box.checked) box.click();
    }
    globalThis.spike.fit();
  });
  console.log(`\n  screenshots written to ${path.relative(process.cwd(), shots)}/`);

  // --- python timings
  await page.evaluate(() => globalThis.spike.ready);
  let analyseBaked = null;
  let analyseGds = null;
  try {
    analyseBaked = await page.evaluate(async () => {
      const r = await globalThis.spike.analyseBaked();
      return { ok: r.ok, error: r.error ?? null };
    });
  } catch (e) {
    analyseBaked = { ok: false, error: String(e) };
  }
  try {
    analyseGds = await page.evaluate(async () => {
      const r = await globalThis.spike.analyseFromGds();
      return { ok: r.ok, error: r.error ?? null };
    });
  } catch (e) {
    analyseGds = { ok: false, error: String(e) };
  }
  const timings = await page.evaluate(() => globalThis.spike.timings);

  // --- what actually crossed the wire
  const wire = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((e) => ({ name: e.name, transfer: e.transferSize, encoded: e.encodedBodySize })),
  );

  await browser.close();
  return { renderer, bundleBytes, fps, timings, analyseBaked, analyseGds, wire };
}

// ---- report ----------------------------------------------------------------

const disk = diskReport();
console.log("\n=== 1. Transfer size (dist/ on disk) ===");
let rawTotal = 0;
let brTotal = 0;
let coreRaw = 0;
let coreBr = 0;
for (const [cat, t] of [...disk].sort((a, b) => b[1].raw - a[1].raw)) {
  console.log(
    `  ${cat.padEnd(30)} ${String(t.files).padStart(3)} files  ` +
      `raw ${MB(t.raw).padStart(9)}  gzip ${MB(t.gzip).padStart(9)}  brotli ${MB(t.brotli).padStart(9)}`,
  );
  rawTotal += t.raw;
  brTotal += t.brotli;
  if (cat === "pyodide runtime" || cat === "gdsx wheel") {
    coreRaw += t.raw;
    coreBr += t.brotli;
  }
}
console.log(`  ${"-".repeat(78)}`);
console.log(`  Pyodide + gdsx wheel only:      raw ${MB(coreRaw)}   brotli ${MB(coreBr)}`);
console.log(`  Everything in dist/:            raw ${MB(rawTotal)}   brotli ${MB(brTotal)}`);
console.log(`  M0 kill criterion: > ~15 MB for Pyodide + gdsx  =>  ${coreBr > 15e6 ? "EXCEEDED" : "under"} (brotli)`);

const b = await browserReport();

console.log("\n=== GL renderer ===");
console.log(`  ${b.renderer}${headless ? "  (headless)" : "  (headed)"}`);

console.log("\n=== 3. Frame rate, whole die on screen, panning ===");
for (const [lod, f] of Object.entries(b.fps)) {
  console.log(
    `  lod ${lod.padEnd(5)} ${f.fps.toFixed(1).padStart(6)} fps  ` +
      `${f.frameMs.toFixed(2).padStart(6)} ms/frame  ` +
      `${String(f.drawCalls).padStart(3)} draws  ${String(f.instances).padStart(6)} rects  ` +
      `| still >=60 fps at ${f.headroom}x the load`,
  );
}

console.log("\n=== 2. Python in Pyodide ===");
for (const [k, v] of Object.entries(b.timings)) console.log(`  ${k.padEnd(28)} ${v} ms`);
console.log(`  analyse(baked netlist) ok: ${b.analyseBaked?.ok}`, b.analyseBaked?.error ?? "");
console.log(`  extract+analyse(gds)   ok: ${b.analyseGds?.ok}`, b.analyseGds?.error ?? "");
const analyseMs = b.timings.analyse_after_extract_ms ?? b.timings.analyse_baked_ms;
if (analyseMs != null) {
  console.log(`  M0 kill criterion: analyse > 10 s  =>  ${analyseMs > 10_000 ? "EXCEEDED" : "under"} (${analyseMs} ms)`);
}

console.log("\n=== bytes over the wire this session ===");
let wireTotal = 0;
for (const r of b.wire) {
  wireTotal += r.transfer || r.encoded || 0;
}
console.log(`  ${b.wire.length} requests, ${MB(wireTotal)} total (preview server, uncompressed)`);
console.log(`  render.bin in page: ${MB(b.bundleBytes)}`);
console.log(`  render.bin raw size on disk: ${MB(statSync(path.join(distDir, "samples/puzzle.render.bin")).size)}`);