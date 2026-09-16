import { describe, expect, it } from "vitest";
import { buildInvestigationScene } from "./buildInvestigationScene";
import { tooltipForHover, tooltipLabelFor } from "./objectTooltip";
import { makeBootstrap } from "./testFixtures";

/**
 * Phase 8_1 B1 — hover tooltip data flow. The tooltip payload may carry ONLY
 * the public registry label: no evidence titles, no case content, no hidden
 * data (the payload type has no room for anything else).
 */

const MODEL = buildInvestigationScene(makeBootstrap());

describe("tooltipLabelFor — hover label lookup (pure)", () => {
  it("returns the public registry label for a hovered interactable", () => {
    expect(tooltipLabelFor(MODEL, "kitchen_knife")).toBe("Kitchen knife");
    expect(tooltipLabelFor(MODEL, "apartment_laptop")).toBe("Laptop");
  });

  it("returns the label for non-interactables too (the renderer gates hover)", () => {
    expect(tooltipLabelFor(MODEL, "victim_body_placeholder")).toBe("Victim");
  });

  it("returns null for unknown objects / null input", () => {
    expect(tooltipLabelFor(MODEL, "does_not_exist")).toBeNull();
    expect(tooltipLabelFor(MODEL, null)).toBeNull();
    expect(tooltipLabelFor(null, "kitchen_knife")).toBeNull();
  });

  it("never leaks evidence content into the tooltip label", () => {
    // The laptop has an evidence link (email_thomas_01 -> "Weekend plans"),
    // but the tooltip only ever carries the PUBLIC object label.
    const label = tooltipLabelFor(MODEL, "apartment_laptop");
    expect(label).toBe("Laptop");
    expect(label).not.toContain("Weekend");
    expect(label).not.toContain("mail");
  });
});

describe("tooltipForHover — hover state payload", () => {
  it("builds a payload with ONLY objectId + public label", () => {
    const payload = tooltipForHover(MODEL, "kitchen_knife");
    expect(payload).toEqual({ objectId: "kitchen_knife", label: "Kitchen knife" });
    // Frozen shape: no evidence/case fields can ever ride along.
    expect(Object.keys(payload ?? {})).toEqual(["objectId", "label"]);
  });

  it("returns null on hover end (null objectId) and for unknown objects", () => {
    expect(tooltipForHover(MODEL, null)).toBeNull();
    expect(tooltipForHover(MODEL, "ghost")).toBeNull();
    expect(tooltipForHover(null, "kitchen_knife")).toBeNull();
  });
});