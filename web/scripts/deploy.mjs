// Build the game and publish it to GitHub Pages. This is the only thing in
// the repository that publishes anything: there are no GitHub Actions, no
// hooks, nothing that runs on push or on a schedule. A deploy happens when,
// and only when, the owner types:
//
//     cd web && npm run deploy
//
// Operator documentation lives in docs/deploy.md. This comment is for whoever
// has to change the script.
//
// WHAT IT RUNS, AND WHY IN THIS ORDER
//
//   0. preconditions      -- clean tree, commit pushed, origin resolves
//   1. build the wheel    -- repo root ../dist/*.whl, cleared first
//   2. bootstrap          -- the puzzle artifacts, which git does not track
//   3. npm ci             -- exact lockfile install; wipes node_modules
//   4. fetch-runtime-deps -- refills node_modules/.pyodide-extra (needs net)
//   5. npm test           -- the web suite; aborts the deploy on failure
//   6. npm run build      -- public/ and dist/ cleared first, GDSX_BASE set
//   7. publish            -- one orphan commit force-pushed to gh-pages
//
// The wheel is built before `npm ci` and the tests, not after, because
// scripts/sync-assets.mjs refuses to run without a wheel in ../dist -- and
// `npm test` ends in test:base-path, which does a full production build. And
// `npm ci` deletes node_modules outright, which is where fetch-runtime-deps
// stages the Pyodide wheels, so the fetch has to follow the install rather
// than precede it. No npm lifecycle hook runs the fetch, which is why it is
// spelled out here.
//
// Steps 1 and 6 delete their output directories first. A deploy that reuses a
// stale public/ or dist/ ships a half-old bundle -- the failure mode where the
// page loads, most of it works, and one panel is three commits behind. Full
// rebuild every time; the deploy is not the place to save ninety seconds.
//
// THE PYTHON SUITE IS NOT RUN HERE. `uv run pytest -q` is ~5 minutes and does
// not gate a single byte of what ends up in the bundle -- the wheel is built
// from the same source either way. Running the Python tests before committing
// is the developer's responsibility and always was; this script's job is to
// refuse to publish a web bundle whose own tests fail.
//
// Usage:
//   node scripts/deploy.mjs
//   node scripts/deploy.mjs --allow-dirty       publish a build of uncommitted work
//   node scripts/deploy.mjs --allow-unpushed    publish a commit no one else has
//   node scripts/deploy.mjs --remote <name>     push somewhere other than origin
//   node scripts/deploy.mjs --branch <name>     publish to a branch other than gh-pages

import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoDir = path.dirname(webDir);

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const value = (name, fallback) => {
  const i = argv.indexOf(name);
  return i === -1 ? fallback : argv[i + 1];
};

const ALLOW_DIRTY = flag("--allow-dirty");
const ALLOW_UNPUSHED = flag("--allow-unpushed");
const REMOTE = value("--remote", "origin");
const BRANCH = value("--branch", "gh-pages");

// --- small helpers -------------------------------------------------------

function fail(message) {
  console.error(`\ndeploy refused:\n\n${message}\n`);
  process.exit(1);
}

/** Run a command, streaming its output. Exits the deploy if it fails. */
function run(label, command, args, cwd, env = {}) {
  console.log(
    `\n=== ${label}\n$ ${command} ${args.join(" ")}   (in ${path.relative(repoDir, cwd) || "."}/)`,
  );
  const res = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: false,
    env: { ...process.env, ...env },
  });
  if (res.error) fail(`could not run \`${command}\`: ${res.error.message}`);
  if (res.status !== 0) {
    fail(
      `\`${command} ${args.join(" ")}\` exited ${res.status}.\n` +
        `Nothing has been pushed. Fix the failure above and re-run \`npm run deploy\`.`,
    );
  }
}

/** Run a command for its stdout. Returns null instead of throwing. */
function capture(command, args, cwd = repoDir) {
  try {
    return execFileSync(command, args, { cwd, encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

function git(...args) {
  return capture("git", args);
}

/** git, keeping leading whitespace -- `git status --porcelain` encodes the
 *  staged/unstaged distinction in column 1, and trimming eats it. */
function gitRaw(...args) {
  try {
    return execFileSync("git", args, { cwd: repoDir, encoding: "utf8" }).replace(/\n$/, "");
  } catch {
    return "";
  }
}

function bytesIn(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    total += entry.isDirectory() ? bytesIn(full) : statSync(full).size;
  }
  return total;
}

function human(bytes) {
  const mb = bytes / 1024 / 1024;
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
}

/**
 * owner and repo from a remote URL, in either form git speaks:
 *   https://github.com/rates37/gdsx.git
 *   git@github.com:rates37/gdsx.git
 * A local path (used by the deploy's own tests) yields owner null and the
 * directory name as the repo, which is enough to derive a base path.
 */
function parseRemote(url) {
  const cleaned = url.replace(/\.git$/, "").replace(/\/+$/, "");
  const match = cleaned.match(/[:/]([^/:]+)\/([^/]+)$/);
  if (match && /github\.com/i.test(cleaned)) {
    return { owner: match[1], repo: match[2] };
  }
  return { owner: null, repo: path.basename(cleaned) };
}

// --- 0. preconditions ----------------------------------------------------
//
// All of them are checked before anything is built, so a machine that is
// going to be refused is refused in a second rather than after a five-minute
// build. Each message names the fix, because the person reading it is
// mid-deploy and wants the next command, not a diagnosis.

if (!git("rev-parse", "--git-dir")) {
  fail(`${repoDir} is not a git checkout, so there is nothing to publish from.`);
}

const remoteUrl = git("remote", "get-url", REMOTE);
if (!remoteUrl) {
  fail(
    `no remote named "${REMOTE}".\n\n` +
      `  Fix:  git remote add ${REMOTE} https://github.com/<you>/<repo>.git\n` +
      `  (or pass --remote <name> to publish to a different one)`,
  );
}

const { owner, repo } = parseRemote(remoteUrl);
if (!repo) {
  fail(`could not read a repository name out of ${REMOTE} = ${remoteUrl}.`);
}

// A GitHub *user* page (<owner>.github.io) is served from the site root; every
// other repository is a *project* page served one directory down. Getting this
// wrong is the single most likely cause of a deployed page that loads a blank
// screen and 404s every asset, so it is derived from the remote rather than
// written down anywhere.
const isUserPage = owner !== null && repo.toLowerCase() === `${owner.toLowerCase()}.github.io`;
const BASE = isUserPage ? "/" : `/${repo}/`;
const liveUrl = owner
  ? `https://${owner.toLowerCase()}.github.io${isUserPage ? "/" : `/${repo}/`}`
  : `(no github.com remote; base would be ${BASE})`;

// An inherited GDSX_BASE would silently win over the one derived here -- the
// deploy would build for one prefix and be published under another. Refuse
// rather than guess which the operator meant.
if (process.env.GDSX_BASE !== undefined && process.env.GDSX_BASE !== BASE) {
  fail(
    `GDSX_BASE is set to "${process.env.GDSX_BASE}" in the environment, but the\n` +
      `"${REMOTE}" remote (${remoteUrl}) means the site will be served from "${BASE}".\n\n` +
      `  A build made for the wrong base path deploys as a blank page: every\n` +
      `  asset URL is prefixed with the base, and none of them resolve.\n\n` +
      `  Fix:  unset GDSX_BASE     (the deploy sets it itself, from the remote)`,
  );
}

const status = gitRaw("status", "--porcelain");
if (status && !ALLOW_DIRTY) {
  fail(
    `the working tree has uncommitted changes:\n\n` +
      status
        .split("\n")
        .slice(0, 20)
        .map((l) => `    ${l}`)
        .join("\n") +
      (status.split("\n").length > 20 ? `\n    ... and more` : "") +
      `\n\n  You are about to publish a build of whatever is on disk. If that is not\n` +
      `  a commit, the published site cannot be reproduced from the repository.\n\n` +
      `  Fix:  git add -A && git commit   (then git push)\n` +
      `  or:   npm run deploy -- --allow-dirty   to publish it anyway`,
  );
}

const sha = git("rev-parse", "HEAD");
const shortSha = git("rev-parse", "--short", "HEAD");
if (!sha) fail(`the repository has no commits yet.`);

// "Pushed" is asked of the remote-tracking refs, not the remote itself: this
// does no network fetch, so a deploy is not blocked by a slow connection. The
// consequence is that it is answering "have I pushed this?", which is the
// question the operator can act on.
const unpushed = git("rev-list", "--max-count=1", "HEAD", `--not`, `--remotes=${REMOTE}/*`);
if (unpushed && !ALLOW_UNPUSHED) {
  fail(
    `the current commit ${shortSha} is not on "${REMOTE}".\n\n` +
      `  The published site should correspond to a commit someone else can check\n` +
      `  out; otherwise the only copy of what is deployed is on this machine.\n\n` +
      `  Fix:  git push ${REMOTE} ${git("rev-parse", "--abbrev-ref", "HEAD") ?? "HEAD"}\n` +
      `  or:   npm run deploy -- --allow-unpushed   to publish it anyway`,
  );
}

console.log(`remote      ${REMOTE} = ${remoteUrl}`);
console.log(`source      ${shortSha}${status ? "  (dirty, --allow-dirty)" : ""}`);
console.log(`base path   ${BASE}`);
console.log(`publishing  ${BRANCH}  ->  ${liveUrl}`);

// --- 1. the gdsx wheel ---------------------------------------------------
//
// Not `uv build`: that produces a wheel that imports and then cannot analyse
// anything, because config/ lives above the package and is left out of it. The
// failure surfaces only in the browser, at analysis time, which is far too
// late. scripts/build_wheel.py stages config/ into the package first.
//
// ../dist is emptied of wheels first. sync-assets.mjs copies *every* .whl it
// finds there into the bundle and names the newest by mtime in the manifest,
// so a wheel left over from an older version is both dead weight in the
// deployed bundle and one clock skew away from being the one that is served.
const distRoot = path.join(repoDir, "dist");
if (existsSync(distRoot)) {
  for (const name of readdirSync(distRoot)) {
    if (name.endsWith(".whl") || name.endsWith(".tar.gz")) unlinkSync(path.join(distRoot, name));
  }
}
run("1/7  build the gdsx wheel (with config/ inside it)", "uv", ["run", "python", "scripts/build_wheel.py"], repoDir);

// --- 2. the generated puzzle artifacts -----------------------------------
//
// git tracks each level's design.gds and puzzles/catalog.json, not the files
// derived from them: the manifests, the extraction, the render bundle, the
// gate tape, the hints and the zips. A clone has none of them, and the build
// reads all of them -- sync-assets copies netlist/render/tape into public/
// and turns the manifests into index.json.
//
// So the deploy rebuilds them, for the same reason it rebuilds the wheel and
// empties public/ and dist/: what is published should be a function of the
// source commit and nothing else. Ten seconds, and it means a stale artifact
// left on this machine by an experiment cannot reach the site.
//
// This writes only gitignored paths, so it cannot dirty the tree the
// precondition above just checked.
run("2/7  rebuild the generated puzzle artifacts", "uv", ["run", "python", "scripts/bootstrap.py"], repoDir);

// --- 3. node dependencies, from the lockfile -----------------------------
//
// `npm ci` rather than `npm install`: it installs exactly what
// package-lock.json says and fails if the lockfile and package.json disagree,
// so what is published is built from a dependency set that is written down.
// It also deletes node_modules first, which is why step 3 comes after it.
run("3/7  install node dependencies (npm ci)", "npm", ["ci"], webDir);

// --- 4. pyodide runtime wheels ------------------------------------------
//
// Needs network. No npm lifecycle hook runs this; sync-assets.mjs only checks
// that its output is present and refuses to build otherwise. Unconditional
// rather than --if-needed, because npm ci just deleted the staging directory.
run("4/7  fetch the pyodide runtime wheels", "node", ["scripts/fetch-runtime-deps.mjs"], webDir);

// --- 5. the web test suite ----------------------------------------------
//
// Nothing else runs these -- there is no CI in this repository -- so the
// deploy runs them. The suite ends with test:base-path, which builds and
// drives the app under a sub-path in a real browser: the one check that would
// otherwise only fail after publishing.
run("5/7  run the web test suite", "npm", ["test"], webDir);

// --- 6. the production build --------------------------------------------
//
// public/ and dist/ are both removed first. public/ persists between runs and
// Vite copies whatever is in it into dist wholesale, so a `npm run dev` from
// last week would put its --samples payload (several MB no player path
// fetches) into the deployed bundle. Rebuilding from empty makes the deployed
// bundle a function of the source commit and nothing else.
const publicDir = path.join(webDir, "public");
const buildDir = path.join(webDir, "dist");
rmSync(publicDir, { recursive: true, force: true });
rmSync(buildDir, { recursive: true, force: true });
run("6/7  production build", "npm", ["run", "build"], webDir, { GDSX_BASE: BASE });

if (!existsSync(path.join(buildDir, "index.html"))) {
  fail(`the build produced no ${path.relative(repoDir, buildDir)}/index.html; nothing to publish.`);
}

// GitHub Pages runs the published directory through Jekyll unless this file is
// present, and Jekyll SILENTLY DROPS every path whose name begins with an
// underscore. Vite does not emit underscore-prefixed names today, but Pyodide's
// payload and any future chunk naming scheme are not under our control, and the
// failure looks like a 404 on one asset in production and nowhere else. This
// line looks deletable. It is not.
writeFileSync(path.join(buildDir, ".nojekyll"), "");

// --- 7. publish as a single orphan commit --------------------------------
//
// dist/ is ~38 MB, most of it one 9.6 MB pyodide.asm.wasm. Appending each
// deploy to a branch would grow the repository without bound and there is no
// value in the history: a build is reproducible from the source commit named
// in the commit message. So every deploy replaces the branch with ONE commit
// that has no parent, force-pushed. Git deduplicates by content hash, so the
// second deploy of an unchanged wasm blob transfers and stores nothing.
//
// Built with git plumbing against a scratch index rather than a `git worktree`
// or the `gh-pages` npm package. A worktree would check out the branch's 38 MB
// onto disk a second time purely to overwrite it, and the npm package is a
// dependency this repo does not otherwise need for something git does in three
// commands. The scratch index also means the deploy never touches the working
// tree, the real index, or any local branch -- if the push fails, the
// repository is in exactly the state it started in.
console.log(`\n=== 7/7  publish ${path.relative(repoDir, buildDir)}/ to ${REMOTE}/${BRANCH}`);

const indexDir = mkdtempSync(path.join(tmpdir(), "gdsx-deploy-"));
const indexFile = path.join(indexDir, "index");
const gitEnv = { ...process.env, GIT_INDEX_FILE: indexFile };

function plumbing(args) {
  const res = spawnSync("git", args, { cwd: buildDir, env: gitEnv, encoding: "utf8" });
  if (res.status !== 0) {
    rmSync(indexDir, { recursive: true, force: true });
    fail(`git ${args.join(" ")} failed:\n${res.stderr || res.stdout}`);
  }
  return res.stdout.trim();
}

let commit;
try {
  // -f because the build output is gitignored by every sensible .gitignore,
  // this one included -- these paths are only ever committed from here.
  plumbing(["--git-dir", path.join(repoDir, ".git"), "--work-tree", buildDir, "add", "-A", "-f", "."]);
  const tree = plumbing(["--git-dir", path.join(repoDir, ".git"), "write-tree"]);
  const message =
    `Deploy ${shortSha}${status ? " (dirty tree)" : ""}\n\n` +
    `Built from source commit ${sha}\n` +
    `base ${BASE}\n`;
  // No -p: no parent, so the branch is exactly one commit deep every time.
  commit = plumbing(["--git-dir", path.join(repoDir, ".git"), "commit-tree", tree, "-m", message]);
} finally {
  rmSync(indexDir, { recursive: true, force: true });
}

// Pushed by object id rather than by local branch name, so no local ref is
// created or moved: `git checkout gh-pages` on this machine keeps meaning
// nothing, and the published branch has exactly one writer.
run(
  `      force-push ${commit.slice(0, 9)} to ${REMOTE}/${BRANCH}`,
  "git",
  ["push", "--force", REMOTE, `${commit}:refs/heads/${BRANCH}`],
  repoDir,
);

// --- done ----------------------------------------------------------------

const size = bytesIn(buildDir);
console.log(
  `\n` +
    `Deployed.\n` +
    `  source commit   ${sha}${status ? "  (plus uncommitted changes)" : ""}\n` +
    `  deploy commit   ${commit}  (orphan, on ${REMOTE}/${BRANCH})\n` +
    `  size            ${human(size)}\n` +
    `  live at         ${liveUrl}\n` +
    `\n` +
    `If this is the first deploy, the site is not being served yet: set\n` +
    `Settings -> Pages -> Source -> "Deploy from a branch" -> ${BRANCH} / root.\n`,
);