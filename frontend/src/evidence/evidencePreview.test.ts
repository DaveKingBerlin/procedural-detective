import { describe, expect, it } from "vitest";
import { buildInvestigationScene } from "../scene/buildInvestigationScene";
import { makeBootstrap } from "../scene/testFixtures";
import { evidenceHeaderTitle, evidencePreviewFor } from "./evidencePreview";

/**
 * Phase 8_1 D — evidence-panel object context + small-evidence preview.
 * All data comes from the PUBLIC scene model (application-owned registry):
 * the record payload itself never contributes to the preview.
 */

const MODEL = buildInvestigationScene(makeBootstrap());

describe("evidencePreviewFor — object context from the scene model (pure)", () => {
  it("derives label + registry color for an object interaction", () => {
    const preview = evidencePreviewFor(MODEL, "kitchen_knife");
    expect(preview).toEqual({
      objectId: "kitchen_knife",
      label: "Kitchen knife",
      color: "#c8ccd4",
    });
  });

  it("never derives preview color/label from the record payload", () => {
    const laptop = evidencePreviewFor(MODEL, "apartment_laptop");
    expect(laptop?.label).toBe("Laptop"); // public label, NOT the email title
    expect(laptop?.color).toBe("#30343e"); // registry hex, not anything from the record
    expect(laptop?.label).not.toContain("Weekend");
  });

  it("returns null for unknown objects / no model / null objectId", () => {
    expect(evidencePreviewFor(MODEL, "does_not_exist")).toBeNull();
    expect(evidencePreviewFor(null, "kitchen_knife")).toBeNull();
    expect(evidencePreviewFor(MODEL, null)).toBeNull();
  });

  it("never shows a raw id when the label is missing — safe human fallback (Phase 18B)", () => {
    const model = buildInvestigationScene(
      makeBootstrap({
        scene: {
          ...makeBootstrap().scene,
          worldObjects: [
            {
              objectId: "mystery_box",
              assetId: "ASSET.THAT.DOES.NOT.EXIST",
              assetType: "unknown",
              subtype: null,
              locationId: "miller_apartment_kitchen",
              anchor: "kitchen_counter",
              interaction: "inspect",
              evidenceId: null,
              discovered: false,
              read: false,
            },
          ],
        },
      }),
    );
    const preview = evidencePreviewFor(model, "mystery_box");
    expect(preview).toEqual({
      objectId: "mystery_box",
      label: "Evidence Object", // semantic fallback — never the raw objectId
      color: "#8d8d93",
    });
    expect(preview?.label).not.toBe("mystery_box");
  });
});

describe("evidenceHeaderTitle — 'Kitchen knife — <evidence title>'", () => {
  it("combines the object label with the evidence title", () => {
    expect(evidenceHeaderTitle("Kitchen knife", "Blood on the kitchen knife matches the victim")).toBe(
      "Kitchen knife — Blood on the kitchen knife matches the victim",
    );
  });

  it("falls back to the bare title when the label is missing/blank", () => {
    expect(evidenceHeaderTitle(null, "Weekend plans")).toBe("Weekend plans");
    expect(evidenceHeaderTitle(undefined, "Weekend plans")).toBe("Weekend plans");
    expect(evidenceHeaderTitle("   ", "Weekend plans")).toBe("Weekend plans");
  });

  it("keeps hostile object labels INERT (plain text only)", () => {
    const header = evidenceHeaderTitle("<script>alert(1)</script>", "Weekend plans");
    expect(header).toBe("<script>alert(1)</script> — Weekend plans"); // the renderer escapes it
    expect(header).not.toContain("murderer");
  });
});