// Which extra wheels the browser needs beyond the core Pyodide runtime, and
// where they are staged. Shared by scripts/fetch-runtime-deps.mjs (which
// downloads them) and scripts/sync-assets.mjs (which refuses to build without
// them), so the two cannot disagree about the answer.
//
// The filenames are read out of the installed pyodide package's own
// pyodide-lock.json rather than hardcoded. That is what makes a `pyodide`
// version bump self-enforcing: the new lock names new wheel files, the old
// ones staged on disk no longer satisfy it, and the next build stops.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

// micropip and packaging are what makes installing anything possible at all;
// pyyaml is gdsx's one hard runtime dependency and has no PyPI wheel Pyodide
// will accept. Without any one of them the worker cannot boot.
export const WANTED = ["micropip", "packaging", "pyyaml"];

export const FETCH_COMMAND = "npm run setup";

export function extraDir(webDir) {
  return path.join(webDir, "node_modules", ".pyodide-extra");
}

export function readLock(webDir) {
  const lockPath = path.join(webDir, "node_modules", "pyodide", "pyodide-lock.json");
  if (!existsSync(lockPath)) {
    throw new Error(
      `node_modules/pyodide is not installed (no ${path.relative(webDir, lockPath)}).\n` +
        `Run \`npm install\` in web/ first, or just \`${FETCH_COMMAND}\`.`,
    );
  }
  return JSON.parse(readFileSync(lockPath, "utf8"));
}

/** The wheel entries this pyodide version needs staged: [{name, file_name, sha256}]. */
export function requiredWheels(webDir) {
  const lock = readLock(webDir);
  return WANTED.map((name) => {
    const entry = lock.packages[name];
    if (!entry) throw new Error(`${name} not found in pyodide-lock.json`);
    return { name, file_name: entry.file_name, sha256: entry.sha256, version: lock.info.version };
  });
}

/** Those of them not currently on disk. Empty array means the stage is good. */
export function missingWheels(webDir) {
  const dir = extraDir(webDir);
  return requiredWheels(webDir).filter((w) => !existsSync(path.join(dir, w.file_name)));
}