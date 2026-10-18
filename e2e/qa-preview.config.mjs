// PHASE 48 — QA-owned `vite preview` config.
//
// The production SPA resolves its API base to the SAME-ORIGIN `/api/v1`
// (frontend/src/api/client.ts). `vite preview` has NO proxy, so the committed
// config alone cannot satisfy the Phase-48 browser matrix (baseURL :4173 +
// backend :8000) for the one leg that submits generation. This QA config only
// adds the documented dev reverse-proxy /api -> 127.0.0.1:8000; it changes no
// product source and no committed config.
//
// Plain object export (no bare imports) so it loads regardless of module
// resolution while the vite binary itself comes from frontend/node_modules.
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..", "frontend");

export default {
  root: frontendDir,
  base: "/",
  preview: {
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
};
