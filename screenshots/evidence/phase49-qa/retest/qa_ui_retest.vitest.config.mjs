// Independent QA Phase 49 retest vitest config.
// The probe lives under screenshots/evidence (outside frontend/), so bare
// React imports are aliased explicitly to the frontend install.
const FE = "C:/Users/Fujitsu/coding/procedural-detective/frontend/node_modules";

export default {
  root: "C:/Users/Fujitsu/coding/procedural-detective",
  test: {
    root: "C:/Users/Fujitsu/coding/procedural-detective",
    include: ["screenshots/evidence/phase49-qa/retest/*.probe.test.ts"],
    environment: "jsdom",
  },
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: [
      { find: /^react\/jsx-runtime$/, replacement: `${FE}/react/jsx-runtime.js` },
      { find: /^react\/jsx-dev-runtime$/, replacement: `${FE}/react/jsx-dev-runtime.js` },
      { find: /^react-dom\/client$/, replacement: `${FE}/react-dom/client.js` },
      { find: /^react-dom$/, replacement: `${FE}/react-dom/index.js` },
      { find: /^react$/, replacement: `${FE}/react/index.js` },
    ],
  },
  server: { fs: { allow: ["C:/Users/Fujitsu/coding/procedural-detective"] } },
};
