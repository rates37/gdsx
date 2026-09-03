import { defineConfig } from "vite";

export default defineConfig({
  // The public path the app is served from, baked into every built asset URL
  // and exposed to source as `import.meta.env.BASE_URL` (see src/asset-url.ts).
  //
  // Default "/" covers `npm run dev`, `npm run preview`, a deploy to a site
  // root, and a custom domain -- all of those leave GDSX_BASE unset.
  //
  // The GitHub Pages workflow deploys to a *project* page, served from
  // https://<user>.github.io/<repo>/, and so sets GDSX_BASE="/<repo>/"
  // (leading and trailing slash both required by Vite).
  base: process.env.GDSX_BASE ?? "/",
  worker: { format: "es" },
  optimizeDeps: { exclude: ["pyodide"] },
  build: { target: "es2022" },
});