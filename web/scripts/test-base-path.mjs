// Checks that a build deployed under a sub-path actually runs from that
// sub-path -- the GitHub Pages project-page case,
// https://<user>.github.io/<repo>/.
//
// This is the one failure mode the rest of the suite structurally cannot
// see. `npm run dev` and `npm run preview` both serve from a site root, so
// a hardcoded "/pyodide/" resolves correctly in every local workflow and in
// every unit test, and only 404s once the app is one directory down. A test
// that does not build, deploy under a prefix, and load the result in a real
// browser cannot tell the two apart.
//
// So: build with GDSX_BASE set, copy dist into <tmp>/gdsx/, serve <tmp> as
// the site root, and drive http://localhost:<port>/gdsx/ with Chromium.
// Every asset the app pulls at runtime is under that prefix and none of
// them is fingerprinted by Vite, so any surviving root-absolute URL shows up
// as a 404 rather than as a subtle rendering difference.
//
// The assertion is on the network log and the console, not on a screenshot:
// a missing wheel or font is a 404 and a broken import is a console error,
// and both are exact. A screenshot diff would be flaky about text rendering
// and would say nothing about which asset was wrong.
//
// Usage: node scripts/test-base-path.mjs [--base /gdsx/] [--no-build] [--headed]

import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const argv = process.argv.slice(2);
const baseArg = argv.indexOf("--base");
const BASE = baseArg === -1 ? "/gdsx/" : argv[baseArg + 1];
const SKIP_BUILD = argv.includes("--no-build");
const HEADED = argv.includes("--headed");

// Pyodide boots, loads a stdlib zip and micropip-installs the wheel; on a
// cold run that is tens of seconds, and it is CPU-bound rather than flaky.
const BOOT_TIMEOUT_MS = 180_000;

const failures = [];
let checks = 0;

function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}

// ---- 1. build under the sub-path ------------------------------------

if (!SKIP_BUILD) {
  console.log(`building with GDSX_BASE=${BASE} ...`);
  execFileSync("npm", ["run", "build"], {
    cwd: webDir,
    env: { ...process.env, GDSX_BASE: BASE },
    stdio: "inherit",
  });
}

// ---- 2. stage it where the prefix is real ---------------------------
//
// Copying into <tmp>/<prefix>/ rather than pointing a server's --base at
// dist/ matters: it is the deployed directory layout, so a URL that escapes
// the prefix lands on a genuinely absent file instead of being rewritten
// back into the app by a dev server's fallback.

const siteRoot = mkdtempSync(path.join(tmpdir(), "gdsx-pages-"));
const prefix = BASE.replace(/^\/+|\/+$/g, "");
const deployDir = path.join(siteRoot, prefix);
mkdirSync(deployDir, { recursive: true });
cpSync(path.join(webDir, "dist"), deployDir, { recursive: true });

// ---- 3. a static server with no SPA fallback ------------------------
//
// Deliberately no index.html fallback for unknown paths: GitHub Pages has
// none either, and a fallback would turn every 404 this test exists to
// catch into a 200 serving HTML.

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".wasm": "application/wasm",
  ".woff2": "font/woff2",
  ".svg": "image/svg+xml",
  ".whl": "application/octet-stream",
  ".zip": "application/zip",
  ".bin": "application/octet-stream",
  ".gds": "application/octet-stream",
};

const server = createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  let filePath = path.join(siteRoot, decodeURIComponent(url.pathname));
  // Escape guard, and the one convenience a real host does provide:
  // a directory URL serves its index.html.
  if (!filePath.startsWith(siteRoot)) {
    res.writeHead(403).end();
    return;
  }
  try {
    if (statSync(filePath).isDirectory()) filePath = path.join(filePath, "index.html");
  } catch {
    res.writeHead(404).end("not found");
    return;
  }
  let body;
  try {
    body = readFileSync(filePath);
  } catch {
    res.writeHead(404).end("not found");
    return;
  }
  res.writeHead(200, {
    "content-type": MIME[path.extname(filePath)] ?? "application/octet-stream",
    "content-length": body.length,
  });
  res.end(body);
});

const port = await new Promise((resolve) => {
  server.listen(0, "127.0.0.1", () => resolve(server.address().port));
});
const origin = `http://127.0.0.1:${port}`;
console.log(`serving ${siteRoot} at ${origin}${BASE}`);

// ---- 4. drive it ----------------------------------------------------

const browser = await chromium.launch({ headless: !HEADED });
const context = await browser.newContext();
const page = await context.newPage();

const notFound = [];
const consoleErrors = [];

page.on("response", (response) => {
  if (response.status() >= 400) notFound.push(`${response.status()} ${response.url()}`);
});
page.on("requestfailed", (request) => {
  notFound.push(`FAILED ${request.url()} (${request.failure()?.errorText})`);
});
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => {
  consoleErrors.push(`uncaught: ${err.message}`);
});

// Workers get their own console/network events only via the page they
// belong to in Playwright, which is what the handlers above already see --
// worker.ts's Pyodide and wheel fetches are reported on this page.

let opened = 0;

try {
  // -- the menu: proves puzzles/index.json resolved under the prefix ---
  await page.goto(`${origin}${BASE}`, { waitUntil: "load", timeout: 60_000 });
  await page.waitForSelector(".menu-card", { timeout: 30_000 });
  const cards = await page.locator(".menu-card").count();
  check(cards > 0, `level picker rendered no cards (catalog fetch failed?)`);
  console.log(`  menu: ${cards} level card(s)`);

  // -- both webfonts, from the CSS url()s Vite rewrote ------------------
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].map((f) => ({ family: f.family, status: f.status }));
  });
  for (const family of ["Fraunces", "Spline Sans Mono"]) {
    const face = fonts.find((f) => f.family === family);
    check(
      face?.status === "loaded",
      `webfont ${family} did not load (status: ${face?.status ?? "no such face"})`,
    );
  }
  console.log(`  fonts: ${fonts.map((f) => `${f.family}=${f.status}`).join(", ")}`);

  // -- open a puzzle: Pyodide, the wheel, the render bundle, the die ----
  const puzzleId = await page.evaluate(async () => {
    const r = await fetch(new URL("puzzles/index.json", document.baseURI));
    return (await r.json()).puzzles[0].id;
  });
  console.log(`  opening puzzle "${puzzleId}" ...`);
  await page.goto(`${origin}${BASE}?puzzle=${puzzleId}`, {
    waitUntil: "load",
    timeout: 60_000,
  });

  // `spike` is published by boot.ts only after the render bundle has
  // parsed, so its presence already means the bundle fetch resolved.
  await page.waitForFunction(() => "spike" in globalThis, null, { timeout: 60_000 });
  console.log(`  render bundle parsed, waiting for Pyodide + wheel ...`);

  // `spike.ready` is the worker's boot promise: Pyodide from pyodide/ and
  // micropip installing the wheel named by wheel/manifest.json. If either
  // URL is wrong under the prefix, this is where it fails.
  await page.evaluate(() => globalThis.spike.ready, null, { timeout: BOOT_TIMEOUT_MS });

  // Any real endpoint proves the wheel imported; `capabilities` is the
  // cheapest one and takes no arguments.
  const caps = await page.evaluate(async () => {
    const env = await globalThis.api.call("capabilities");
    return env.ok ? JSON.stringify(env.data) : `ERROR ${JSON.stringify(env.error)}`;
  });
  check(!caps.startsWith("ERROR"), `gdsx wheel did not import: ${caps}`);
  console.log(`  python: capabilities ok`);

  const renderer = await page.evaluate(() => globalThis.spike.rendererInfo);
  check(
    typeof renderer === "string" && renderer.length > 0,
    `die view did not report a renderer (it never drew)`,
  );
  console.log(`  die view: ${renderer}`);

  const bytes = await page.evaluate(() => globalThis.spike.bundleBytes);
  check(bytes > 0, `render bundle is empty (${bytes} bytes)`);
  opened = 1;
} catch (err) {
  failures.push(`sub-path run threw before finishing: ${err.message}`);
} finally {
  await browser.close();
  server.close();
  rmSync(siteRoot, { recursive: true, force: true });
}

// ---- 5. the two assertions that are the point -----------------------

check(notFound.length === 0, `${notFound.length} failed request(s):\n      ${notFound.join("\n      ")}`);
check(
  consoleErrors.length === 0,
  `${consoleErrors.length} console error(s):\n      ${consoleErrors.join("\n      ")}`,
);

if (failures.length > 0) {
  console.error(`\ntest-base-path: ${failures.length} failure(s) of ${checks} checks\n`);
  for (const f of failures) console.error(`  ✗ ${f}`);
  process.exit(1);
}

console.log(
  `\ntest-base-path: ${checks} checks passed under base ${BASE} ` +
    `(${opened} puzzle opened, 0 failed requests, 0 console errors)`,
);