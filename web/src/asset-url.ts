/**
 * Resolve a runtime asset path against the public base path the app is
 * served from.
 *
 * **Why this exists, given that `/pyodide/pyodide.mjs` works fine locally.**
 * Files under `public/` are copied to the deploy root verbatim: they keep
 * their names, they are never fingerprinted, and -- unlike an `import`ed
 * asset or a `url()` in CSS -- Vite cannot see the strings that reference
 * them, so it cannot rewrite those strings for you. A literal
 * `fetch('/puzzles/index.json')` therefore hardcodes an assumption that the
 * app is served from the site root.
 *
 * That assumption holds for `npm run dev` and `npm run preview`, which is
 * why it survived so long. It breaks on GitHub Pages, where a project page
 * lives at `https://<user>.github.io/<repo>/`: the asset is at
 * `/<repo>/puzzles/index.json` and the site-root URL 404s. Every such fetch
 * has to be joined to the base path at runtime instead, and this is the one
 * place that does the joining.
 *
 * `import.meta.env.BASE_URL` is Vite's build-time constant for that base
 * (`vite.config.ts` sets it from `GDSX_BASE`). It always ends in "/", so the
 * only real work here is not doubling the slash when a caller writes a
 * leading one -- which callers do, out of habit, and which would silently
 * produce `/gdsx//pyodide/`.
 *
 * @param path Asset path relative to the deploy root, with or without a
 *   leading slash -- e.g. 'puzzles/index.json' or '/pyodide/'.
 * @returns The path with the base prepended, e.g. "/gdsx/puzzles/index.json".
 */
export function assetUrl(path: string): string {
  // `import.meta.env` is a Vite build-time substitution and simply does not
  // exist when these modules are imported directly by the Node test scripts
  // (`node --experimental-strip-types`). Those tests assert on catalog data,
  // not on deploy paths, so a site root is the right answer for them.
  const base = (import.meta as { env?: { BASE_URL?: string } }).env?.BASE_URL ?? "/";
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}