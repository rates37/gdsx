// One command from a fresh clone to a runnable game:
//
//     cd web && npm run setup && npm run dev
//
// It lives in web/ rather than at the repo root because web/ is where the
// entry point already is -- a stranger clones, sees a web app, and reaches for
// `npm install`. The repo has no root-level JS tooling and no Makefile, so a
// root script would have meant inventing a second toolchain to drive the one
// that exists. `npm run setup` can be run with no node_modules present, since
// npm only needs package.json to find it, so this is genuinely the first
// command anyone has to type.
//
// The chain, in the order the dependencies actually run:
//
//   1. npm install                  -- needs node_modules/pyodide for step 3
//   2. uv run python scripts/build_wheel.py  (repo root)  -- makes ../dist/*.whl
//   3. fetch-runtime-deps --if-needed        -- needs network, stages wheels
//   4. sync-assets --samples                 -- needs 1-3, fills public/
//
// Safe to re-run: every step is idempotent. Step 3 no-ops when the staged
// wheels already match the installed pyodide's lock, so the common re-run
// costs no network; it downloads again exactly when the `pyodide` version
// changed, which is the case nothing used to enforce.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { missingWheels } from "./pyodide-extra.mjs";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoDir = path.dirname(webDir);

function fail(message) {
  console.error(`\nsetup failed:\n${message}\n`);
  process.exit(1);
}

function run(label, command, args, cwd) {
  console.log(`\n=== ${label}\n$ ${command} ${args.join(" ")}   (in ${path.relative(repoDir, cwd) || "."}/)`);
  const res = spawnSync(command, args, { cwd, stdio: "inherit", shell: false });
  if (res.error) fail(`could not run \`${command}\`: ${res.error.message}`);
  if (res.status !== 0) fail(`\`${command} ${args.join(" ")}\` exited ${res.status}`);
}

// --- preconditions -------------------------------------------------------
// Checked up front, all of them, before anything is downloaded or built, so
// a machine missing two things is told about both on the first run.

const problems = [];

// Node, against the floor declared in package.json engines. Read from there
// rather than repeated here, so there is one place to raise it.
const pkg = JSON.parse(readFileSync(path.join(webDir, "package.json"), "utf8"));
const floor = (pkg.engines?.node ?? ">=22.6.0").replace(/^[^\d]*/, "");
const cmp = (a, b) => {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) - (pb[i] ?? 0);
  return 0;
};
const nodeVersion = process.versions.node;
if (cmp(nodeVersion, floor) < 0) {
  problems.push(
    `node ${nodeVersion} is below the required ${floor}.\n` +
      `  The web test scripts use --experimental-strip-types, added in 22.6.0.\n` +
      `  Install a newer node (e.g. \`nvm install 22\`) and re-run.`,
  );
}

// uv, the only way anything Python is run here.
const uv = spawnSync("uv", ["--version"], { encoding: "utf8" });
if (uv.error || uv.status !== 0) {
  problems.push(
    `\`uv\` was not found on PATH.\n` +
      `  gdsx builds its wheel with uv; bare pip/python are not supported.\n` +
      `  Install it: curl -LsSf https://astral.sh/uv/install.sh | sh`,
  );
}

// The repo root really is above us -- catches web/ copied out on its own.
if (!existsSync(path.join(repoDir, "pyproject.toml"))) {
  problems.push(`no pyproject.toml at ${repoDir}; web/ must sit inside the gdsx checkout.`);
}

if (problems.length > 0) fail(problems.map((p) => `  - ${p}`).join("\n"));

console.log(`node ${nodeVersion} (>= ${floor}) ok`);
console.log(`${uv.stdout.trim()} ok`);

// --- 1. npm install ------------------------------------------------------
run("1/4  install node dependencies", "npm", ["install"], webDir);

// --- 2. the gdsx wheel ---------------------------------------------------
// Not `uv build`: that omits config/, and the resulting wheel fails only in
// the browser, at analysis time. build_wheel.py stages config/ in first.
run(
  "2/4  build the gdsx wheel (with config/ inside it)",
  "uv",
  ["run", "python", "scripts/build_wheel.py"],
  repoDir,
);

// --- 3. pyodide runtime wheels ------------------------------------------
// Network step, and the only one. Probe first so an offline machine is told
// that plainly instead of failing inside a fetch with a stack trace -- but
// only when there is actually something to download.
if (missingWheels(webDir).length > 0) {
  process.stdout.write("\n=== 3/4  checking network reachability ... ");
  try {
    // Any HTTP response at all means the host is reachable, which is the only
    // question here -- the CDN answers a bare directory HEAD with 403, and
    // that is still a working network. Only a thrown error (DNS, refused,
    // timeout) means offline.
    await fetch("https://cdn.jsdelivr.net/pyodide/", {
      method: "HEAD",
      signal: AbortSignal.timeout(10000),
    });
    console.log("ok");
  } catch (err) {
    fail(
      `cannot reach https://cdn.jsdelivr.net (${err.message}).\n` +
        `  The Pyodide runtime wheels have to be downloaded once, from there.\n` +
        `  Connect to a network and re-run \`npm run setup\`; every other step\n` +
        `  above it has already completed and will be a no-op.`,
    );
  }
  run(
    "3/4  fetch the pyodide runtime wheels",
    "node",
    ["scripts/fetch-runtime-deps.mjs", "--if-needed"],
    webDir,
  );
} else {
  console.log("\n=== 3/4  pyodide runtime wheels already staged, skipping (no network needed)");
}

// --- 4. stage everything into public/ ------------------------------------
// public/ is entirely gitignored; this is what creates every asset the app
// serves. --samples matches what `npm run dev` does, so a later dev run has
// nothing left to do.
run("4/4  stage assets into public/", "node", ["scripts/sync-assets.mjs", "--samples"], webDir);

console.log(`\nSetup complete. Start the game with:\n\n    npm run dev\n`);