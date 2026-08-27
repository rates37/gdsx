// Regression test for the Register Inspector's virtualized list
// (panels/virtual-list.ts + panels/register-inspector-panel.ts).
//
// scripts/solve/FINDINGS.md F19 reported that "the register list is
// virtualised with recycled rows... the selection highlight follows the DOM
// row, not the group", based on scripts/solve/10-pairs.mjs producing
// duplicated and skipped ad-hoc groups. Reproducing it in a real browser
// shows that report was wrong about the cause: the panel keys selection off
// the RegisterEntry object itself (`reg === selected` in
// register-inspector-panel.ts), which survives scrolling and DOM
// recycling correctly. The duplicates and gaps came from 10-pairs.mjs's own
// selector, `.ri-list-viewport [class*="row"], .ri-list-viewport > div >
// div`, which also matches the `.vlist-rows` container (so index 0 was the
// whole row stack, not a row -- clicking it landed wherever the browser's
// default click point happened to fall) and the `.ri-row-name` /
// `.ri-row-role` / `.ri-row-width` spans inside every row (`class*="row"`
// matches all three), doubling every real match; and the script never
// scrolled, so rows outside the initial render window were simply absent.
// That script has been fixed to use `.ri-row` and to scroll through the
// list; this test pins down the actual app behaviour so a future
// virtual-list change can't reintroduce a real version of the bug.
//
// Starts its own dev server and a headless browser, per
// docs/game/web-ui-architecture.md §6: everything is wrapped in
// try/finally, a hard watchdog is armed, and the 3D view is never opened.
//
// Usage: node --experimental-strip-types scripts/test-register-inspector.mjs

import { chromium } from "playwright";
import { spawn } from "node:child_process";

const PORT = 5175; // dedicated port so this doesn't collide with `npm run dev`
const BASE = `http://localhost:${PORT}/`;
const TARGET_URL = `${BASE}?puzzle=original-puzzle`;
const WATCHDOG_MS = 120_000;

const FLOPS = [
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 22, 23, 24,
  28, 29, 30, 31, 32, 35, 36, 39, 45, 46, 50, 56, 57, 61, 62, 63, 64, 65, 66,
  67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 78, 79, 80, 81, 82, 84,
]
  .map((n) => `dfrtp_2_${n}`)
  .join(",");

let checks = 0;
const failures = [];
function check(cond, message) {
  checks++;
  if (!cond) failures.push(message);
}

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`dev server did not come up at ${url} within ${timeoutMs}ms`);
}

const watchdog = setTimeout(() => {
  console.error(`[watchdog] no exit after ${WATCHDOG_MS}ms -- killing`);
  process.exit(2);
}, WATCHDOG_MS);
watchdog.unref?.();

const webDir = new URL("..", import.meta.url).pathname;
const server = spawn(
  "npm",
  ["run", "dev", "--", "--port", String(PORT), "--strictPort"],
  { cwd: webDir, stdio: ["ignore", "pipe", "pipe"] },
);
server.stderr.on("data", (d) => process.stderr.write(`[vite] ${d}`));

let browser;
try {
  await waitForServer(BASE, 30_000);

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1700, height: 1050 } });
  page.setDefaultTimeout(15_000);
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));
  await page.goto(TARGET_URL);
  await page.waitForTimeout(8_000);

  await page.locator(".dv-tab", { hasText: /^Registers$/ }).first().click();
  await page.waitForTimeout(300);

  await page.locator(".ri-adhoc-flops").fill(FLOPS);
  await page.locator(".ri-adhoc-find").click();
  await page.waitForTimeout(6_000);

  const rowsLocator = page.locator(".ri-list-viewport .ri-row");
  const initialCount = await rowsLocator.count();
  check(initialCount > 0, "ad-hoc grouping rendered at least one row");

  // Select a row that's currently rendered and remember what it showed.
  const firstLabel = (await rowsLocator.first().innerText()).trim().split("\n")[0];
  await rowsLocator.first().click();
  await page.waitForTimeout(300);
  const detailBefore = (await page.locator(".ri-detail-header").innerText()).trim();
  check(
    detailBefore.startsWith(firstLabel),
    `clicking the first rendered row ("${firstLabel}") selected "${detailBefore}"`,
  );

  // Scroll far enough that the virtual list recycles its DOM, then scroll
  // back. The row's DOM node is destroyed and rebuilt; selection must still
  // point at the same logical group, not whatever row lands in that slot.
  const viewport = page.locator(".ri-list-viewport");
  await viewport.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await page.waitForTimeout(300);
  await viewport.evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.waitForTimeout(300);

  const selectedLabel = await page.evaluate(() => {
    const sel = document.querySelector(".ri-row-selected");
    return sel ? sel.innerText.trim().split("\n")[0] : null;
  });
  const detailAfter = (await page.locator(".ri-detail-header").innerText()).trim();

  check(
    selectedLabel === firstLabel,
    `after scrolling away and back, the highlighted row is "${selectedLabel}", expected "${firstLabel}"`,
  );
  check(
    detailAfter === detailBefore,
    `after scrolling away and back, the detail pane reads "${detailAfter}", expected "${detailBefore}"`,
  );

  // Every group the toolbar claims to have must be reachable by scrolling --
  // virtualization must not strand rows permanently outside the render
  // window.
  const expectedGroups = await page.evaluate(() => {
    const m = document.querySelector(".ri-count")?.textContent?.match(/(\d+)/);
    return m ? Number(m[1]) : 0;
  });
  await viewport.evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.waitForTimeout(200);
  const seen = new Set();
  let lastSize = -1;
  for (let guard = 0; guard < 80; guard++) {
    const labels = await rowsLocator.allInnerTexts();
    for (const l of labels) seen.add(l.trim().split("\n")[0]);
    const atBottom = await viewport.evaluate(
      (el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 1,
    );
    if (atBottom && seen.size === lastSize) break;
    lastSize = seen.size;
    await viewport.evaluate((el) => {
      el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + el.clientHeight);
    });
    await page.waitForTimeout(150);
  }
  check(
    seen.size === expectedGroups,
    `scrolling through the list only reached ${seen.size} of ${expectedGroups} groups`,
  );
} finally {
  clearTimeout(watchdog);
  await browser?.close().catch(() => {});
  server.kill("SIGTERM");
  await new Promise((r) => setTimeout(r, 500));
}

if (failures.length) {
  console.error(`FAIL: ${failures.length} of ${checks} checks\n`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`ok: ${checks} register-inspector virtual-list selection checks`);