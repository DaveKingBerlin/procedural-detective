// Plain-object vitest config (no bare 'vitest/config' import so it loads from
// the repo root, where only frontend/node_modules has vitest).
export default {
  test: {
    root: "C:/Users/Fujitsu/coding/procedural-detective",
    include: ["screenshots/evidence/phase49-adv/*.test.ts"],
    environment: "node",
  },
};
