// Phase 47 QA retest vitest config (evidence only; NOT product code).
// Run from frontend/:  npx vitest run --config <this file>
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..", "..");
const frontend = path.join(repo, "frontend");
const nm = path.join(frontend, "node_modules");

export default {
  root: repo,
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      react: path.join(nm, "react"),
      "react-dom": path.join(nm, "react-dom"),
      vitest: path.join(nm, "vitest"),
    },
  },
  server: { fs: { allow: [repo] } },
  test: {
    environment: "jsdom",
    include: ["screenshots/evidence/phase47-qa/*.probe.test.tsx"],
  },
};
