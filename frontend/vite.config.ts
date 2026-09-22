import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Work from the frontend/ directory even when Vite is invoked from the repo root.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: path.resolve(__dirname),
  // Phase 21 F-01: production deep-link/reload safety. A ROUTE-RELATIVE base
  // ("./") makes the built asset URLs resolve against the current route
  // (/scene/assets/... after a refresh on /scene), which the backend SPA
  // fallback answers with HTML and the browser then rejects on module MIME.
  // A ROOT-ABSOLUTE base (/assets/...) keeps every chunk at the site root no
  // matter which BrowserRouter route the shell was loaded at — direct
  // navigation, refresh, copied deep-links and history back/forward all work
  // (the backend serves /assets/* as real static files with correct MIME).
  // Dev (:5173) and preview (:4173) serve / at their own site root, so the
  // same "/" base resolves identically there.
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 4173,
  },
  build: {
    outDir: path.resolve(__dirname, "dist"),
    emptyOutDir: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});