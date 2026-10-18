// PHASE 48 QA RETEST — QA-owned vitest config (evidence only; NOT product code).
// Run from frontend/:
//   npx vitest run --config ../e2e/probes/qa-p48-retest.vitest.config.mjs
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const frontend = path.join(repo, "frontend");
const nm = path.join(frontend, "node_modules");

export default {
  root: frontend,
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      react: path.join(nm, "react"),
      "react-dom": path.join(nm, "react-dom"),
      "react-router": path.join(nm, "react-router"),
      vitest: path.join(nm, "vitest"),
    },
  },
  server: { fs: { allow: [repo] } },
  test: {
    environment: "node",
    include: [
      "../e2e/probes/qa_p48_retest_frontend.probe.test.ts",
      "../e2e/probes/qa_p48_retest_ui.probe.test.tsx",
    ],
  },
};
