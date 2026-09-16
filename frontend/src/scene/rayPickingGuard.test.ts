import { describe, expect, it } from "vitest";
import { Scene } from "@babylonjs/core/scene";

/**
 * DEF-056 regression guard (HIGH).
 *
 * Babylon v8 ships `Scene.prototype.pick` as a feature-gated STUB in
 * scene.js (it warns and returns a dummy PickingInfo). The REAL picking
 * implementation is installed as a SIDE-EFFECT of evaluating
 * `@babylonjs/core/Culling/ray` — it monkey-patches the prototype. If no
 * code imports that module (tree-shaken subpath imports), the production
 * bundle ships the dead stub and direct 3D interaction never works, even
 * though unit tests pass (they feed fake pickInfo objects).
 *
 * These tests lock BOTH directions in-process:
 *   1. a bare `Scene` import leaves the stub in place (proof the
 *      discriminator is real and the bug would be caught);
 *   2. importing the ray side-effect module replaces it with the real
 *      implementation (proof the fix mechanism works on the installed
 *      @babylonjs/core version).
 *
 * Marker-based (deterministic, no network/GPU/DOM):
 *   - STUB source references `_WarnImport` and never calls `Pick(this ...`;
 *   - the REAL patched pick source calls `Pick(this ...` and never touches
 *     `_WarnImport`.
 */

function pickSource(): string {
  const pick = (Scene as unknown as { prototype?: { pick?: unknown } }).prototype?.pick;
  return typeof pick === "function" ? Function.prototype.toString.call(pick) : "";
}

describe("DEF-056 — ray side-effect guard", () => {
  it("a bare Scene import leaves the pick STUB in place (discriminator is real)", () => {
    const source = pickSource();
    expect(source, "stub pick should reference the _WarnImport helper").toContain("_WarnImport");
    expect(source, "stub pick must NOT call the real Pick implementation").not.toContain("Pick(this");
  });

  it("importing @babylonjs/core/Culling/ray installs the REAL pick implementation", async () => {
    await import("@babylonjs/core/Culling/ray");
    const source = pickSource();
    expect(source, "real pick must call the Pick implementation").toContain("Pick(this");
    expect(source, "real pick must not keep the feature-gated warning stub").not.toContain("_WarnImport");
  });
});