import { defineConfig } from "vite";

export default defineConfig({
  worker: { format: "es" },
  optimizeDeps: { exclude: ["pyodide"] },
  build: { target: "es2022" },
});