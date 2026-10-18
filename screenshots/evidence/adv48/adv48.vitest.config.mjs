// ADV-48 transient vitest config (evidence only; NOT product code).
// Run from frontend/:  npx vitest run --config ../screenshots/evidence/adv48/adv48.vitest.config.mjs
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..", "..");
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
      "../screenshots/evidence/adv48/adv48-generator.probe.test.ts",
      "../screenshots/evidence/adv48/adv48-ui.probe.test.tsx",
      "../screenshots/evidence/adv48/adv48-generator2.probe.test.ts",
      "../screenshots/evidence/adv48/adv48-ui2.probe.test.tsx",
    ],
  },
};
